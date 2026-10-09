#!/usr/bin/env python3
"""
analyze_ui_sync_trace.py

Read-only kernel ftrace analyzer for UI / compositor synchronization traces
(e.g. SteamOS / gamescope on TB323FU / SM8850P).

It parses kernel ftrace text lines, pairs related events, and emits a JSON
summary plus CSV detail files. Standard library only. No device access, no
network, no shelling out.

CLI:
    python analyze_ui_sync_trace.py TRACE_PATH --start FLOAT --end FLOAT \
        --output-dir DIR

SEMANTICS / DISCLAIMER
----------------------
Every ``*_ms`` value computed here is a *timestamp interval* / *wall-clock
completion* measurement derived from ftrace timestamps. It is NOT the pure GPU
render / execution duration. ``reported_elapsed_ms`` is the
instrument/firmware-reported ``elapsed`` field (raw ns / 1e6) and is likewise
not pure GPU execution time. This tool reports observed timing only; it does
not invent causes.

Outputs (written into --output-dir):
    summary.json         structured summary
    gpu_submissions.csv  complete active-window GPU submit rows
    slow_pairs.csv       all paired durations >= 1 ms, with full details
    vblank_intervals.csv consecutive vblank intervals + DRM seq deltas

Exit code is non-zero on any error (never a silent success).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque

# ---------------------------------------------------------------------------
# ftrace line grammar
# ---------------------------------------------------------------------------
# Example:
#   "   gamescope-xwm-31966   [006] .....  6613.609200: msm_gpu_submit: id=987117 pid=31966 ring=2 bos=0 cmds=1"
# Task name may contain spaces; TID is the trailing "-<digits>" before "[cpu]".
TRACE_LINE_RE = re.compile(
    r"^\s*(?P<task>.*?)-(?P<tid>\d+)\s+\[\s*(?P<cpu>\d+)\]\s+"
    r"(?P<flags>\S+)\s+(?P<ts>\d+\.\d+):\s+"
    r"(?P<event>[A-Za-z0-9_]+):?\s?(?P<fields>.*)$"
)

INTERESTING_EVENTS = {
    "msm_gpu_submit",
    "msm_gpu_submit_flush",
    "msm_gpu_submit_retired",
    "msm_atomic_commit_tail_start",
    "msm_atomic_commit_tail_finish",
    "msm_atomic_wait_flush_start",
    "msm_atomic_wait_flush_finish",
    "dma_fence_wait_start",
    "dma_fence_wait_end",
    "dpu_crtc_vblank_cb",
    "drm_vblank_event_delivered",
}

SLOW_THRESHOLD_MS = 1.0
UNMATCHED_SAMPLE_LIMIT = 1000
TOP_N_GPU = 20


class Rec:
    """One parsed ftrace record."""

    __slots__ = ("task", "tid", "cpu", "ts", "event", "fields", "raw")

    def __init__(self, task, tid, cpu, ts, event, fields, raw):
        self.task = task
        self.tid = tid
        self.cpu = cpu
        self.ts = ts
        self.event = event
        self.fields = fields
        self.raw = raw


def parse_line(line):
    """Parse one ftrace line. Returns Rec or None for headers/unparseable lines."""
    m = TRACE_LINE_RE.match(line)
    if m is None:
        return None
    fields = {}
    rest = m.group("fields")
    if rest:
        for tok in rest.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                fields[k] = v
    return Rec(
        task=m.group("task").strip(),
        tid=int(m.group("tid")),
        cpu=int(m.group("cpu")),
        ts=float(m.group("ts")),
        event=m.group("event"),
        fields=fields,
        raw=line.rstrip("\n"),
    )


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------
def to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    f = to_float(value)
    if f is None:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f)


def first_field(rec, names, default=None):
    for n in names:
        if n in rec.fields:
            return rec.fields[n]
    return default


def linear_quantile(sorted_vals, q):
    """Linear-interpolation quantile over an already sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def dist_summary(values):
    """count/mean/min/p50/p95/p99/max with linear quantiles."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {
            "count": 0,
            "mean": None,
            "min": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    s = sorted(clean)
    return {
        "count": len(s),
        "mean": round(sum(s) / len(s), 6),
        "min": round(s[0], 6),
        "p50": round(linear_quantile(s, 0.50), 6),
        "p95": round(linear_quantile(s, 0.95), 6),
        "p99": round(linear_quantile(s, 0.99), 6),
        "max": round(s[-1], 6),
    }


def key_repr(rec, key):
    return {
        "ts": rec.ts,
        "tid": rec.tid,
        "task": rec.task,
        "key": list(key) if isinstance(key, tuple) else key,
    }


# ---------------------------------------------------------------------------
# FIFO pairing for start/end event families
# ---------------------------------------------------------------------------
def fifo_pair(records, start_event, end_event, key_fn, start, end):
    """Pair start/end events by FIFO per key.

    A pair is emitted only when a matching start exists and dt >= 0.
    Pairs whose two events are not both inside [start, end] are counted as
    ``excluded_by_window`` (they are matched, just outside the window).
    """
    pending = defaultdict(deque)
    pairs = []
    unmatched_end = []
    negative_dt = 0
    excluded_by_window = 0

    for rec in records:
        if rec.event == start_event:
            pending[key_fn(rec)].append(rec)
        elif rec.event == end_event:
            k = key_fn(rec)
            q = pending.get(k)
            if not q:
                unmatched_end.append((rec, k))
                continue
            s = q.popleft()
            dt = (rec.ts - s.ts) * 1000.0
            if dt < 0.0:
                negative_dt += 1
                continue
            if (start <= s.ts <= end) and (start <= rec.ts <= end):
                pairs.append((s, rec, dt))
            else:
                excluded_by_window += 1

    unmatched_start = []
    for k, q in pending.items():
        for s in q:
            unmatched_start.append((s, k))

    unmatched_start.sort(key=lambda x: x[0].ts)
    unmatched_end.sort(key=lambda x: x[0].ts)

    return {
        "pairs": pairs,
        "unmatched_start": unmatched_start,
        "unmatched_end": unmatched_end,
        "negative_dt": negative_dt,
        "excluded_by_window": excluded_by_window,
    }


def pair_group_summary(pairs, group_fields):
    """Group matched pairs by fields, return per-group duration distributions."""
    groups = defaultdict(list)
    for p in pairs:
        groups[tuple(p.get(f) for f in group_fields)].append(p["duration_ms"])
    out = []
    for k, vals in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        entry = {f: k[i] for i, f in enumerate(group_fields)}
        entry["duration_ms"] = dist_summary(vals)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# main analysis
# ---------------------------------------------------------------------------
def analyze(args):
    start = args.start
    end = args.end
    trace_path = args.trace_path

    # ---- single pass over the whole file ---------------------------------
    total_lines = 0
    parsed_lines = 0
    unparsed_lines = 0
    cpu_ts = {}
    event_counts = Counter()
    interesting = []

    with open(trace_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            total_lines += 1
            rec = parse_line(line)
            if rec is None:
                unparsed_lines += 1
                continue
            parsed_lines += 1

            c = cpu_ts.get(rec.cpu)
            if c is None:
                cpu_ts[rec.cpu] = {"first": rec.ts, "last": rec.ts, "count": 1}
            else:
                if rec.ts < c["first"]:
                    c["first"] = rec.ts
                if rec.ts > c["last"]:
                    c["last"] = rec.ts
                c["count"] += 1

            if start <= rec.ts <= end:
                event_counts[rec.event] += 1

            if rec.event in INTERESTING_EVENTS:
                interesting.append(rec)

    interesting.sort(key=lambda r: r.ts)

    by_event = defaultdict(list)
    for r in interesting:
        by_event[r.event].append(r)

    slow_rows = []

    # ---- GPU submissions --------------------------------------------------
    submits, flush_dup = {}, Counter()
    for r in by_event["msm_gpu_submit"]:
        i = r.fields.get("id")
        if i is None:
            continue
        if i in submits:
            flush_dup[i] += 1
            continue
        submits[i] = r

    flushes, flush_dup2 = {}, Counter()
    for r in by_event["msm_gpu_submit_flush"]:
        i = r.fields.get("id")
        if i is None:
            continue
        if i in flushes:
            flush_dup2[i] += 1
            continue
        flushes[i] = r

    retires, retire_dup = {}, Counter()
    for r in by_event["msm_gpu_submit_retired"]:
        i = r.fields.get("id")
        if i is None:
            continue
        if i in retires:
            retire_dup[i] += 1
            continue
        retires[i] = r

    gpu_rows = []
    negative_interval_ids = []
    for i in sorted(submits.keys()):
        sub = submits[i]
        fl = flushes.get(i)
        ret = retires.get(i)
        if fl is None or ret is None:
            continue
        # complete row -> but only keep it for windowed output if submit is in window
        dt_sf = (fl.ts - sub.ts) * 1000.0
        dt_fr = (ret.ts - fl.ts) * 1000.0
        dt_sr = (ret.ts - sub.ts) * 1000.0
        if dt_sf < 0.0 or dt_fr < 0.0 or dt_sr < 0.0:
            negative_interval_ids.append(i)
        elapsed_ns = to_float(ret.fields.get("elapsed"))
        row = {
            "id": i,
            "pid": to_int(sub.fields.get("pid")),
            "task": sub.task,
            "tid": sub.tid,
            "ring": sub.fields.get("ring"),
            "submit_ts": sub.ts,
            "flush_ts": fl.ts,
            "retire_ts": ret.ts,
            "submit_to_flush_ms": dt_sf,
            "flush_to_retire_ms": dt_fr,
            "submit_to_retire_ms": dt_sr,
            "reported_elapsed_ms": (elapsed_ns / 1e6) if elapsed_ns is not None else None,
            "mhz": to_int(ret.fields.get("mhz")),
            "gpu_start": to_int(ret.fields.get("start")),
            "gpu_end": to_int(ret.fields.get("end")),
        }
        gpu_rows.append(row)

    # complete rows whose submit time is inside the active window
    gpu_active_rows = [r for r in gpu_rows if start <= r["submit_ts"] <= end]

    missing_flush_ids = sorted(i for i in submits if i not in flushes)
    missing_retire_ids = sorted(i for i in submits if i not in retires)
    orphan_flush_ids = sorted(i for i in flushes if i not in submits)
    orphan_retire_ids = sorted(i for i in retires if i not in submits)

    # per (pid, task, ring) summaries
    groups = defaultdict(list)
    for r in gpu_active_rows:
        groups[(r["pid"], r["task"], r["ring"])].append(r)
    gpu_summary = []
    for (pid, task, ring), rows in sorted(
        groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2]))
    ):
        gpu_summary.append(
            {
                "pid": pid,
                "task": task,
                "ring": ring,
                "row_count": len(rows),
                "submit_to_flush_ms": dist_summary([x["submit_to_flush_ms"] for x in rows]),
                "flush_to_retire_ms": dist_summary([x["flush_to_retire_ms"] for x in rows]),
                "submit_to_retire_ms": dist_summary([x["submit_to_retire_ms"] for x in rows]),
                "reported_elapsed_ms": dist_summary(
                    [x["reported_elapsed_ms"] for x in rows if x["reported_elapsed_ms"] is not None]
                ),
            }
        )

    # GPU slow rows -> slow_pairs.csv
    for r in gpu_active_rows:
        for label, key, ev_start, ev_end, start_ts in (
            (
                "gpu_submit_to_flush",
                "submit_to_flush_ms",
                "msm_gpu_submit",
                "msm_gpu_submit_flush",
                r["submit_ts"],
            ),
            (
                "gpu_flush_to_retire",
                "flush_to_retire_ms",
                "msm_gpu_submit_flush",
                "msm_gpu_submit_retired",
                r["flush_ts"],
            ),
            (
                "gpu_submit_to_retire",
                "submit_to_retire_ms",
                "msm_gpu_submit",
                "msm_gpu_submit_retired",
                r["submit_ts"],
            ),
        ):
            if r[key] >= SLOW_THRESHOLD_MS:
                slow_rows.append(
                    {
                        "pair_type": label,
                        "event_start": ev_start,
                        "event_end": ev_end,
                        "task": r["task"],
                        "tid": r["tid"],
                        "start_ts": start_ts,
                        "end_ts": r["retire_ts"] if key != "submit_to_flush_ms" else r["flush_ts"],
                        "duration_ms": r[key],
                        "id": r["id"],
                        "pid": r["pid"],
                        "ring": r["ring"],
                        "crtc_mask": "",
                        "driver": "",
                        "timeline": "",
                        "context": "",
                        "seqno": "",
                    }
                )

    # GPU pid/task counts (submits in window)
    gpu_pid_counts = Counter()
    gpu_task_counts = Counter()
    for r in by_event["msm_gpu_submit"]:
        if start <= r.ts <= end:
            gpu_pid_counts[str(to_int(r.fields.get("pid")))] += 1
            gpu_task_counts[r.task] += 1

    # top-N slow GPU rows by submit_to_retire_ms
    top_slow_gpu = sorted(
        gpu_active_rows, key=lambda x: x["submit_to_retire_ms"], reverse=True
    )[:TOP_N_GPU]

    # ---- atomic commit tail / wait flush / dma fence ----------------------
    def atomic_key(rec):
        cm = first_field(rec, ["crtc_mask", "mask"])
        return (rec.tid, cm)

    def fence_key(rec):
        return (
            rec.tid,
            first_field(rec, ["driver"]),
            first_field(rec, ["timeline"]),
            first_field(rec, ["context"]),
            first_field(rec, ["seqno", "seq"]),
        )

    family_specs = [
        (
            "msm_atomic_commit_tail",
            "msm_atomic_commit_tail_start",
            "msm_atomic_commit_tail_finish",
            atomic_key,
            ("crtc_mask",),
            ["event_start", "event_end", "task"],
        ),
        (
            "msm_atomic_wait_flush",
            "msm_atomic_wait_flush_start",
            "msm_atomic_wait_flush_finish",
            atomic_key,
            ("crtc_mask",),
            ["event_start", "event_end", "task"],
        ),
        (
            "dma_fence_wait",
            "dma_fence_wait_start",
            "dma_fence_wait_end",
            fence_key,
            ("driver", "timeline", "context", "seqno"),
            ["event_start", "event_end", "task", "driver", "timeline"],
        ),
    ]

    pairs_output = {}
    for label, ev_start, ev_end, key_fn, detail_fields, group_fields in family_specs:
        res = fifo_pair(interesting, ev_start, ev_end, key_fn, start, end)
        pair_dicts = []
        for s, e, dt in res["pairs"]:
            d = {
                "event_start": ev_start,
                "event_end": ev_end,
                "task": s.task,
                "tid": s.tid,
                "start_ts": s.ts,
                "end_ts": e.ts,
                "duration_ms": dt,
            }
            for f in detail_fields:
                d[f] = None
            if key_fn is atomic_key:
                d["crtc_mask"] = first_field(s, ["crtc_mask", "mask"])
            else:
                d["driver"] = first_field(s, ["driver"])
                d["timeline"] = first_field(s, ["timeline"])
                d["context"] = first_field(s, ["context"])
                d["seqno"] = first_field(s, ["seqno", "seq"])
            pair_dicts.append(d)

            if dt >= SLOW_THRESHOLD_MS:
                slow_rows.append(
                    {
                        "pair_type": label,
                        "event_start": ev_start,
                        "event_end": ev_end,
                        "task": s.task,
                        "tid": s.tid,
                        "start_ts": s.ts,
                        "end_ts": e.ts,
                        "duration_ms": dt,
                        "id": "",
                        "pid": "",
                        "ring": "",
                        "crtc_mask": d.get("crtc_mask") or "",
                        "driver": d.get("driver") or "",
                        "timeline": d.get("timeline") or "",
                        "context": d.get("context") or "",
                        "seqno": d.get("seqno") if d.get("seqno") is not None else "",
                    }
                )

        pairs_output[label] = {
            "matched_count": len(pair_dicts),
            "excluded_by_window": res["excluded_by_window"],
            "negative_dt": res["negative_dt"],
            "unmatched_end_count": len(res["unmatched_end"]),
            "unmatched_start_count": len(res["unmatched_start"]),
            "unmatched_end_sample": [
                key_repr(r, k) for r, k in res["unmatched_end"][:UNMATCHED_SAMPLE_LIMIT]
            ],
            "unmatched_start_sample": [
                key_repr(r, k) for r, k in res["unmatched_start"][:UNMATCHED_SAMPLE_LIMIT]
            ],
            "summary": pair_group_summary(pair_dicts, group_fields),
            "aggregate_duration_ms": dist_summary([p["duration_ms"] for p in pair_dicts]),
        }

    # ---- vblank intervals -------------------------------------------------
    def seq_of(rec):
        return to_int(first_field(rec, ["seq", "seqno", "sequence"]))

    # dpu_crtc_vblank_cb grouped by id
    dpu_groups = defaultdict(list)
    for r in by_event["dpu_crtc_vblank_cb"]:
        if start <= r.ts <= end:
            dpu_groups[r.fields.get("id")].append(r)
    dpu_intervals = []
    dpu_rows = []
    for gid, recs in sorted(dpu_groups.items(), key=lambda kv: str(kv[0])):
        recs.sort(key=lambda x: x.ts)
        for i in range(1, len(recs)):
            dt = (recs[i].ts - recs[i - 1].ts) * 1000.0
            dpu_intervals.append(dt)
            dpu_rows.append(
                {
                    "source": "dpu_crtc_vblank_cb",
                    "group": gid if gid is not None else "",
                    "index": i,
                    "timestamp_s": recs[i].ts,
                    "interval_ms": dt,
                    "seq": "",
                    "seq_delta": "",
                }
            )

    # drm_vblank_event_delivered grouped by crtc + file
    drm_groups = defaultdict(list)
    for r in by_event["drm_vblank_event_delivered"]:
        if start <= r.ts <= end:
            gkey = (first_field(r, ["crtc", "crtc_id", "pipe"]), first_field(r, ["file", "file_id"]))
            drm_groups[gkey].append(r)
    drm_intervals = []
    drm_seq_deltas = []
    drm_rows = []
    for gkey, recs in sorted(drm_groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        recs.sort(key=lambda x: x.ts)
        gname = "{crtc=%s,file=%s}" % (gkey[0], gkey[1])
        for i in range(1, len(recs)):
            dt = (recs[i].ts - recs[i - 1].ts) * 1000.0
            s0, s1 = seq_of(recs[i - 1]), seq_of(recs[i])
            delta = (s1 - s0) if (s0 is not None and s1 is not None) else None
            drm_intervals.append(dt)
            if delta is not None:
                drm_seq_deltas.append(delta)
            drm_rows.append(
                {
                    "source": "drm_vblank_event_delivered",
                    "group": gname,
                    "index": i,
                    "timestamp_s": recs[i].ts,
                    "interval_ms": dt,
                    "seq": s1 if s1 is not None else "",
                    "seq_delta": delta if delta is not None else "",
                }
            )

    vblank_output = {
        "dpu_crtc_vblank_cb": {
            "group_count": len(dpu_groups),
            "event_count_in_window": sum(len(v) for v in dpu_groups.values()),
            "interval_ms": dist_summary(dpu_intervals),
            "seq_delta": dist_summary([]),
        },
        "drm_vblank_event_delivered": {
            "group_count": len(drm_groups),
            "event_count_in_window": sum(len(v) for v in drm_groups.values()),
            "interval_ms": dist_summary(drm_intervals),
            "seq_delta": dist_summary(drm_seq_deltas),
        },
    }

    # ---- coverage / warnings ---------------------------------------------
    cpu_first = {c: v["first"] for c, v in cpu_ts.items()}
    global_first = min(cpu_first.values()) if cpu_first else None

    warnings = []
    warnings.append(
        "DURATION SEMANTICS: every *_ms value is a timestamp interval / wall-clock "
        "completion measurement derived from ftrace timestamps, NOT pure GPU render or "
        "execution duration. reported_elapsed_ms is the instrument-reported 'elapsed' "
        "field (raw ns / 1e6) and is also not pure GPU execution time."
    )
    warnings.append(
        "No causal conclusions are drawn; only observed timing is reported."
    )
    warnings.append(
        "dma_fence/atomic/wait pairs are reported only when BOTH events fall inside "
        "[start,end]; pairs matched but outside the window are counted under "
        "'excluded_by_window'. GPU complete rows are kept when the SUBMIT time is in window, "
        "so flush/retire may lie beyond the window boundary."
    )
    if global_first is not None and global_first < start:
        warnings.append(
            "Active window start=%s is after the earliest parsed trace timestamp %s; "
            "pre-window events were parsed for matching and are excluded from window "
            "summaries." % (start, global_first)
        )
    for c in sorted(cpu_ts):
        first = cpu_ts[c]["first"]
        if first > start:
            warnings.append(
                "CPU%d earliest trace timestamp %s is after window start %s; no earlier "
                "coverage on this CPU." % (c, first, start)
            )
    for label, ev_start, ev_end, _k, _d, _g in family_specs:
        info = pairs_output[label]
        if info["unmatched_end_count"] or info["unmatched_start_count"] or info["negative_dt"]:
            warnings.append(
                "%s/%s: unmatched_end=%d unmatched_start=%d negative_dt=%d"
                % (ev_start, ev_end, info["unmatched_end_count"], info["unmatched_start_count"], info["negative_dt"])
            )
    if missing_flush_ids or missing_retire_ids or orphan_flush_ids or orphan_retire_ids:
        warnings.append(
            "GPU pairing gaps: submits_missing_flush=%d submits_missing_retire=%d "
            "orphan_flush=%d orphan_retire=%d"
            % (len(missing_flush_ids), len(missing_retire_ids), len(orphan_flush_ids), len(orphan_retire_ids))
        )

    # ---- assemble + write -------------------------------------------------
    summary = {
        "meta": {
            "tool": "analyze_ui_sync_trace.py",
            "start": start,
            "end": end,
            "trace_path": trace_path,
            "output_dir": args.output_dir,
            "slow_threshold_ms": SLOW_THRESHOLD_MS,
        },
        "line_counts": {
            "total": total_lines,
            "parsed": parsed_lines,
            "unparsed_or_header": unparsed_lines,
        },
        "cpu_timestamps": {
            str(c): {
                "first": round(v["first"], 6),
                "last": round(v["last"], 6),
                "count": v["count"],
            }
            for c, v in sorted(cpu_ts.items())
        },
        "event_counts_active_window": dict(sorted(event_counts.items())),
        "gpu": {
            "complete_row_count_total": len(gpu_rows),
            "complete_row_count_active_window": len(gpu_active_rows),
            "pid_counts_active_window": dict(sorted(gpu_pid_counts.items())),
            "task_counts_active_window": dict(sorted(gpu_task_counts.items())),
            "unique_pids_active_window": len(gpu_pid_counts),
            "unique_tasks_active_window": len(gpu_task_counts),
            "summary_by_pid_task_ring": gpu_summary,
            "incomplete": {
                "missing_flush_count": len(missing_flush_ids),
                "missing_flush_ids": missing_flush_ids,
                "missing_retire_count": len(missing_retire_ids),
                "missing_retire_ids": missing_retire_ids,
                "orphan_flush_count": len(orphan_flush_ids),
                "orphan_flush_ids": orphan_flush_ids,
                "orphan_retire_count": len(orphan_retire_ids),
                "orphan_retire_ids": orphan_retire_ids,
                "duplicate_submit_id_count": len(flush_dup),
                "duplicate_flush_id_count": len(flush_dup2),
                "duplicate_retire_id_count": len(retire_dup),
            },
            "negative_interval_ids": negative_interval_ids,
            "top20_slow_by_submit_to_retire_ms": top_slow_gpu,
        },
        "pairs": pairs_output,
        "vblank": vblank_output,
        "slow_pairs_count": len(slow_rows),
        "warnings": warnings,
        "disclaimer": (
            "All *_ms values are ftrace timestamp intervals / wall-clock completion "
            "measurements, not pure GPU render duration. Instrument-reported elapsed is "
            "labeled as such. No causes are inferred."
        ),
    }

    os.makedirs(args.output_dir, exist_ok=True)

    summary_path = os.path.join(args.output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=False)
        fh.write("\n")

    gpu_csv = os.path.join(args.output_dir, "gpu_submissions.csv")
    with open(gpu_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "id", "pid", "task", "tid", "ring",
                "submit_ts", "flush_ts", "retire_ts",
                "submit_to_flush_ms", "flush_to_retire_ms", "submit_to_retire_ms",
                "reported_elapsed_ms", "mhz", "gpu_start", "gpu_end",
            ]
        )
        for r in gpu_active_rows:
            w.writerow(
                [
                    r["id"], r["pid"], r["task"], r["tid"], r["ring"],
                    r["submit_ts"], r["flush_ts"], r["retire_ts"],
                    "%.6f" % r["submit_to_flush_ms"],
                    "%.6f" % r["flush_to_retire_ms"],
                    "%.6f" % r["submit_to_retire_ms"],
                    "" if r["reported_elapsed_ms"] is None else "%.6f" % r["reported_elapsed_ms"],
                    "" if r["mhz"] is None else r["mhz"],
                    "" if r["gpu_start"] is None else r["gpu_start"],
                    "" if r["gpu_end"] is None else r["gpu_end"],
                ]
            )

    slow_csv = os.path.join(args.output_dir, "slow_pairs.csv")
    with open(slow_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "pair_type", "event_start", "event_end", "task", "tid",
                "start_ts", "end_ts", "duration_ms",
                "id", "pid", "ring", "crtc_mask", "driver", "timeline", "context", "seqno",
            ]
        )
        for r in sorted(slow_rows, key=lambda x: x["duration_ms"], reverse=True):
            w.writerow(
                [
                    r["pair_type"], r["event_start"], r["event_end"], r["task"], r["tid"],
                    r["start_ts"], r["end_ts"], "%.6f" % r["duration_ms"],
                    r["id"], r["pid"], r["ring"], r["crtc_mask"], r["driver"],
                    r["timeline"], r["context"], r["seqno"],
                ]
            )

    vblank_csv = os.path.join(args.output_dir, "vblank_intervals.csv")
    with open(vblank_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "group", "index", "timestamp_s", "interval_ms", "seq", "seq_delta"])
        for r in dpu_rows + drm_rows:
            w.writerow(
                [
                    r["source"], r["group"], r["index"], r["timestamp_s"],
                    "%.6f" % r["interval_ms"], r["seq"], r["seq_delta"],
                ]
            )

    return {
        "summary": summary_path,
        "gpu_submissions": gpu_csv,
        "slow_pairs": slow_csv,
        "vblank_intervals": vblank_csv,
    }


# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        description="Analyze kernel ftrace traces for UI/compositor synchronization."
    )
    p.add_argument("trace_path", help="path to the kernel ftrace text file")
    p.add_argument("--start", required=True, type=float, help="active window start (seconds)")
    p.add_argument("--end", required=True, type=float, help="active window end (seconds)")
    p.add_argument("--output-dir", required=True, dest="output_dir", help="output directory")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not (args.start < args.end):
        print("error: --start must be strictly less than --end", file=sys.stderr)
        return 1
    if not os.path.isfile(args.trace_path):
        print("error: trace file not found: %s" % args.trace_path, file=sys.stderr)
        return 1

    try:
        outputs = analyze(args)
    except Exception as exc:  # never silently succeed
        import traceback

        traceback.print_exc()
        print("error: analysis failed: %s" % exc, file=sys.stderr)
        return 1

    for name, path in outputs.items():
        print("wrote %s: %s" % (name, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
