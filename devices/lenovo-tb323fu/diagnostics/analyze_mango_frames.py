#!/usr/bin/env python3
"""Read-only analyzer for NativeMango frame-timing CSVs.

Reads one CSV and writes one JSON report. No network, device, or deployment I/O.
"""

import argparse
import csv
import json
import os
import statistics
import sys

OPTIONAL = ("gpu_core_clock", "cpu_temp", "gpu_temp", "battery_percent")


def percentile(vals, p):
    n = len(vals)
    if n == 1:
        return float(vals[0])
    rank = p * (n - 1)
    lo = int(rank)
    frac = rank - lo
    hi = min(lo + 1, n - 1)
    return vals[lo] + frac * (vals[hi] - vals[lo])


def num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="Analyze NativeMango frame-timing CSV (read-only).")
    ap.add_argument("csv_path")
    ap.add_argument("--start", type=float, required=True, help="window start (seconds)")
    ap.add_argument("--end", type=float, required=True, help="window end (seconds)")
    ap.add_argument("--output", required=True, help="JSON report path")
    args = ap.parse_args()

    with open(args.csv_path, "r", newline="") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        sys.exit("error: bad CSV: missing data rows")
    header = [h.strip() for h in rows[2]]
    if "elapsed" not in header or "frametime" not in header:
        sys.exit("error: bad CSV: required columns missing")
    idx = {name: header.index(name) for name in header}

    frames, optional = [], {name: [] for name in OPTIONAL if name in idx}
    for row in rows[3:]:
        if not row or not row[0].strip():
            continue
        elapsed = num(row[idx["elapsed"]])
        if elapsed is None or not (args.start <= elapsed / 1e9 <= args.end):
            continue
        ft = num(row[idx["frametime"]])
        if ft is None:
            continue
        frames.append(ft)
        for name in optional:
            v = num(row[idx[name]])
            if v is not None:
                optional[name].append(v)

    if not frames:
        sys.exit("error: no frames in requested elapsed window")

    ordered = sorted(frames)
    mean = statistics.fmean(frames)
    report = {
        "file": os.path.basename(args.csv_path),
        "window": {"start_s": args.start, "end_s": args.end},
        "count": len(frames),
        "frametime_ms": {
            "min": min(frames),
            "mean": mean,
            "p50": percentile(ordered, 0.50),
            "p95": percentile(ordered, 0.95),
            "p99": percentile(ordered, 0.99),
            "max": max(frames),
        },
        "implied_fps": 1000.0 / mean,
        "bins": {
            "lt_12ms": sum(1 for v in frames if v < 12),
            "12_to_20ms": sum(1 for v in frames if 12 <= v < 20),
            "20_to_30ms": sum(1 for v in frames if 20 <= v < 30),
            "ge_30ms": sum(1 for v in frames if v >= 30),
        },
        "first_20_frametime": frames[:20],
        "optional": {
            name: {"min": min(vals), "max": max(vals), "mean": statistics.fmean(vals)}
            for name, vals in optional.items()
            if vals
        },
        "note": "frametime values are per-frame intervals, not GPU render durations",
    }

    with open(args.output, "w") as fh:
        json.dump(report, fh, indent=2)


if __name__ == "__main__":
    main()
