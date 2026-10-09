#!/usr/bin/env python3
"""Read-only runtime recorder for TB323FU / SM8850P fault investigation.

Samples a small, fixed set of sysfs/proc nodes (CPU cpufreq policies, the
Adreno ``3d00000.gpu`` devfreq node, thermal zones, cooling devices and power
supplies) into a JSONL file so that thermal/frequency/power state around an
Adreno GPU fault and auto-reboot is preserved for later analysis.

This tool is strictly read-only: it never writes sysfs, never opens
/dev/watchdog, GPU APIs, stress tools or block devices, and only reads
supply/thermal information (never process environment, user data or
credentials).

Each record is flushed and ``fsync``-ed immediately. The log rotates at
5 MiB per file and keeps 4 files total (about 20 MiB) so it stays small on the
device while remaining easy to recover over SSH to the NAS.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import signal
import threading
import time
from logging.handlers import RotatingFileHandler

DEFAULT_SYS_ROOT = "/sys"
DEFAULT_PROC_ROOT = "/proc"
DEFAULT_INTERVAL = 2.0
MIN_INTERVAL = 0.5
DEFAULT_DURATION = 0.0

# 5 MiB per file, 4 files total (~20 MiB): runtime.jsonl plus 3 rotations.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

GPU_DEVFREQ_NAME = "3d00000.gpu"
OUTPUT_FILENAME = "runtime.jsonl"

_LOGGER_NAME = "y700_runtime_record"
_MAX_ERRORS = 32


def _read_text(path):
    """Return the stripped contents of ``path`` or None on any read error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_int(path):
    """Return ``path`` parsed as int, or None if missing/non-numeric."""
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_kv(path):
    """Parse a ``KEY=VALUE`` uevent-style file, or None if unreadable."""
    text = _read_text(path)
    if text is None:
        return None
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def _listdir(path):
    """Return a sorted listing of ``path`` or an empty list if unavailable."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _isdir(path):
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def collect_sample(sys_root, proc_root):
    """Collect one read-only sample from ``sys_root`` and ``proc_root``.

    Missing nodes or read errors yield ``None``/empty collections and a short
    entry in ``errors``; a missing field never aborts the sample.
    """
    errors = []

    def note(message):
        if len(errors) < _MAX_ERRORS:
            errors.append(message)

    sample = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "monotonic": time.monotonic(),
        "boot_id": _read_text(os.path.join(proc_root, "sys/kernel/random/boot_id")),
        "cpu_online": _read_text(os.path.join(sys_root, "devices/system/cpu/online")),
        "cpufreq": [],
        "gpu": {},
        "thermal_zones": [],
        "cooling_devices": [],
        "power_supply": {},
        "errors": errors,
    }

    # CPU cpufreq policies.
    cpufreq_root = os.path.join(sys_root, "devices/system/cpu/cpufreq")
    for entry in _listdir(cpufreq_root):
        if not entry.startswith("policy"):
            continue
        policy_dir = os.path.join(cpufreq_root, entry)
        if not _isdir(policy_dir):
            continue
        sample["cpufreq"].append({
            "policy": entry,
            "related_cpus": _read_text(os.path.join(policy_dir, "related_cpus")),
            "cur_freq": _read_int(os.path.join(policy_dir, "scaling_cur_freq")),
            "min_freq": _read_int(os.path.join(policy_dir, "scaling_min_freq")),
            "max_freq": _read_int(os.path.join(policy_dir, "scaling_max_freq")),
            "governor": _read_text(os.path.join(policy_dir, "scaling_governor")),
        })

    # Adreno GPU devfreq node.
    gpu_dir = os.path.join(sys_root, "class/devfreq", GPU_DEVFREQ_NAME)
    if not _isdir(gpu_dir):
        note("devfreq/{} missing".format(GPU_DEVFREQ_NAME))
    sample["gpu"] = {
        "cur_freq": _read_int(os.path.join(gpu_dir, "cur_freq")),
        "min_freq": _read_int(os.path.join(gpu_dir, "min_freq")),
        "max_freq": _read_int(os.path.join(gpu_dir, "max_freq")),
        "governor": _read_text(os.path.join(gpu_dir, "governor")),
    }

    # Thermal zones (raw millidegree Celsius preserved) and cooling devices.
    thermal_root = os.path.join(sys_root, "class/thermal")
    for entry in _listdir(thermal_root):
        zone_dir = os.path.join(thermal_root, entry)
        if entry.startswith("thermal_zone") and _isdir(zone_dir):
            sample["thermal_zones"].append({
                "zone": entry,
                "type": _read_text(os.path.join(zone_dir, "type")),
                "temp_milli": _read_int(os.path.join(zone_dir, "temp")),
            })
        elif entry.startswith("cooling_device") and _isdir(zone_dir):
            sample["cooling_devices"].append({
                "device": entry,
                "type": _read_text(os.path.join(zone_dir, "type")),
                "cur_state": _read_int(os.path.join(zone_dir, "cur_state")),
                "max_state": _read_int(os.path.join(zone_dir, "max_state")),
            })

    # Power supplies: supply/thermal information only, from uevent.
    ps_root = os.path.join(sys_root, "class/power_supply")
    for entry in _listdir(ps_root):
        ps_dir = os.path.join(ps_root, entry)
        if not _isdir(ps_dir):
            continue
        uevent = _read_kv(os.path.join(ps_dir, "uevent"))
        if uevent is None:
            note("power_supply/{}/uevent unreadable".format(entry))
        sample["power_supply"][entry] = uevent

    return sample


def _build_logger(output_path):
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for old in list(logger.handlers):
        logger.removeHandler(old)
        try:
            old.close()
        except Exception:
            pass
    handler = RotatingFileHandler(
        output_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, handler


def _emit(logger, handler, sample):
    """Write one JSONL record, then flush and fsync it."""
    line = json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
    logger.info(line)
    handler.flush()
    stream = handler.stream
    if stream is not None:
        try:
            os.fsync(stream.fileno())
        except (OSError, ValueError):
            pass


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Read-only sysfs/proc runtime recorder for TB323FU / SM8850P."
    )
    parser.add_argument("--sys-root", default=DEFAULT_SYS_ROOT,
                        help="sysfs root (default: %(default)s)")
    parser.add_argument("--proc-root", default=DEFAULT_PROC_ROOT,
                        help="procfs root (default: %(default)s)")
    parser.add_argument("--output", required=True,
                        help="directory to write runtime.jsonl into")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="seconds between samples, min %.1f (default: %%(default)s)" % MIN_INTERVAL)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help="seconds to run, 0 = until signal (default: %(default)s)")
    parser.add_argument("--once", action="store_true",
                        help="collect a single sample and exit 0")
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL:
        parser.error("--interval must be >= %.1f" % MIN_INTERVAL)
    if args.duration < 0:
        parser.error("--duration must be >= 0")
    return args


def main(argv=None):
    args = _parse_args(argv)
    os.makedirs(args.output, exist_ok=True)
    output_path = os.path.join(args.output, OUTPUT_FILENAME)

    stop_event = threading.Event()

    def _handle_signal(signum, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        # Not in the main thread; the caller manages the loop lifetime.
        pass

    logger, handler = _build_logger(output_path)
    deadline = time.monotonic() + args.duration if args.duration > 0 else None
    try:
        while True:
            _emit(logger, handler, collect_sample(args.sys_root, args.proc_root))
            if args.once or stop_event.is_set():
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            wait = args.interval
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            if stop_event.wait(wait):
                break
        return 0
    finally:
        try:
            handler.flush()
            if handler.stream is not None:
                os.fsync(handler.stream.fileno())
        except (OSError, ValueError):
            pass
        handler.close()
        logger.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
