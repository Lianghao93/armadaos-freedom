#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Behavioral checks for the Kaanapali DP0 descriptor and idle guard.

This is a narrow host test.  It extracts the real ``struct msm_dp_desc``, the
real descriptor arrays, the real ``msm_dp_dt_match`` table and the real
``msm_dp_bridge_atomic_disable()`` body from a live ``dp_display.c``, then
compiles them into a small C harness with stubs for types and helpers only.

Usage:
    python3 -B tests/test_kaanapali_dp_backport.py \
        --source /path/to/dp_display.c --work-dir /work/dp-backport-check

Baseline (current 7.2.6 without the backport) is expected to report:

    descriptor: FAIL
    idle_off:   FAIL (one PUSH_IDLE MMIO call)
    idle_on:    PASS

With the backport applied all three checks must pass.  The C file and compiler
output stay in the required work directory; there is no tempfile fallback.
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

C_NAME = "kaanapali_dp_harness.c"
BIN_NAME = "kaanapali_dp_harness"


class ExtractionError(RuntimeError):
    """The live source does not contain the expected production text."""


def _brace_end(text, brace):
    """Return the offset just past the brace matching ``text[brace]``."""
    depth = 0
    i = brace
    state = "code"
    length = len(text)

    while i < length:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < length else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        elif state == "line":
            if ch == "\n":
                state = "code"
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"
        else:  # char
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    raise ExtractionError("unterminated brace block at offset %d" % brace)


def _after_block_semicolon(text, end, what):
    """Return the offset past the ``;`` that terminates a block."""
    tail = end
    while tail < len(text) and text[tail] in " \t\r\n":
        tail += 1
    if tail >= len(text) or text[tail] != ";":
        raise ExtractionError("%s is not terminated by ';'" % what)
    return tail + 1


def extract_desc_struct(text):
    """Extract the actual ``struct msm_dp_desc`` definition."""
    match = re.search(r"(?m)^struct\s+msm_dp_desc\s*\{", text)
    if not match:
        raise ExtractionError("could not find struct msm_dp_desc")
    brace = text.find("{", match.start(), match.end())
    end = _brace_end(text, brace)
    end = _after_block_semicolon(text, end, "struct msm_dp_desc")
    return text[match.start():end]


def extract_desc_arrays(text):
    """Extract every actual ``static const struct msm_dp_desc ...[]`` array."""
    pattern = re.compile(
        r"(?m)^static\s+const\s+struct\s+msm_dp_desc\s+\w+\[\]\s*=\s*\{")
    arrays = []
    for match in pattern.finditer(text):
        brace = text.find("{", match.start(), match.end())
        end = _brace_end(text, brace)
        end = _after_block_semicolon(text, end, "msm_dp_desc array")
        arrays.append(text[match.start():end])
    if not arrays:
        raise ExtractionError("could not find any msm_dp_desc array")
    return arrays


def extract_dt_match(text):
    """Extract the real match table through MODULE_DEVICE_TABLE."""
    pattern = re.compile(
        r"(?m)^static\s+const\s+struct\s+of_device_id\s+"
        r"msm_dp_dt_match\[\]\s*=\s*\{")
    match = pattern.search(text)
    if not match:
        raise ExtractionError("could not find msm_dp_dt_match")
    brace = text.find("{", match.start(), match.end())
    end = _brace_end(text, brace)
    table_end = _after_block_semicolon(text, end, "msm_dp_dt_match")

    module = text.find("MODULE_DEVICE_TABLE", table_end)
    if module < 0:
        raise ExtractionError("could not find MODULE_DEVICE_TABLE after match table")
    if text[table_end:module].strip():
        raise ExtractionError("unexpected text between match table and MODULE_DEVICE_TABLE")
    line_end = text.find("\n", module)
    if line_end < 0:
        line_end = len(text)
    return text[match.start():line_end]


def extract_atomic_disable(text):
    """Extract the whole actual ``msm_dp_bridge_atomic_disable()`` function."""
    pattern = re.compile(
        r"(?m)^void\s+msm_dp_bridge_atomic_disable\s*\(")
    match = pattern.search(text)
    if not match:
        raise ExtractionError("could not find msm_dp_bridge_atomic_disable")
    brace = text.find("{", match.end())
    if brace < 0:
        raise ExtractionError("callback has no body")
    if ";" in text[match.end():brace]:
        raise ExtractionError("msm_dp_bridge_atomic_disable is only a prototype")
    end = _brace_end(text, brace)
    return text[match.start():end]


STUBS = r"""
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

typedef unsigned long long phys_addr_t;

/* Missing kernel types.  Only fields touched by the extracted code exist. */
struct drm_bridge { int unused; };
struct drm_atomic_commit { int unused; };

struct msm_dp {
	bool power_on;
};

struct msm_dp_ctrl {
	int unused;
};

struct msm_dp_display_private {
	struct msm_dp msm_dp_display;
	struct msm_dp_ctrl *ctrl;
};

struct msm_dp_bridge {
	struct drm_bridge base;
	struct msm_dp *msm_dp_display;
};

#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

static inline struct msm_dp_bridge *to_dp_bridge(struct drm_bridge *bridge)
{
	return container_of(bridge, struct msm_dp_bridge, base);
}

static int g_push_idle_calls;
static struct msm_dp_ctrl *g_last_push_idle_ctrl;

static void msm_dp_ctrl_push_idle(struct msm_dp_ctrl *ctrl)
{
	g_push_idle_calls++;
	g_last_push_idle_ctrl = ctrl;
}

enum {
	MSM_DP_CONTROLLER_0 = 0,
	MSM_DP_CONTROLLER_1 = 1,
	MSM_DP_CONTROLLER_2 = 2,
	MSM_DP_CONTROLLER_3 = 3,
};

struct of_device_id {
	const char *compatible;
	const void *data;
};

#define MODULE_DEVICE_TABLE(type, name)
"""

MAIN = r"""
int main(void)
{
	const struct msm_dp_desc *desc = NULL;
	struct msm_dp_ctrl ctrl;
	struct msm_dp_display_private priv;
	struct msm_dp_bridge bridge;
	size_t i;
	int failures = 0;

	memset(&ctrl, 0, sizeof(ctrl));
	memset(&priv, 0, sizeof(priv));
	memset(&bridge, 0, sizeof(bridge));

	for (i = 0; msm_dp_dt_match[i].compatible; i++) {
		if (strcmp(msm_dp_dt_match[i].compatible,
			   "qcom,kaanapali-dp") == 0) {
			desc = (const struct msm_dp_desc *)msm_dp_dt_match[i].data;
			break;
		}
	}

	if (!desc) {
		printf("descriptor: FAIL (no qcom,kaanapali-dp entry)\n");
		failures++;
	} else if (desc->io_start != 0x09ad2000ULL ||
		   desc->id != MSM_DP_CONTROLLER_0 ||
		   !desc->wide_bus_supported) {
		printf("descriptor: FAIL (io_start=0x%llx id=%u wide_bus=%d)\n",
		       (unsigned long long)desc->io_start, desc->id,
		       desc->wide_bus_supported);
		failures++;
	} else {
		printf("descriptor: PASS (io_start=0x%llx id=%u wide_bus=%d)\n",
		       (unsigned long long)desc->io_start, desc->id,
		       desc->wide_bus_supported);
	}

	priv.ctrl = &ctrl;
	bridge.msm_dp_display = &priv.msm_dp_display;

	priv.msm_dp_display.power_on = false;
	g_push_idle_calls = 0;
	g_last_push_idle_ctrl = NULL;
	msm_dp_bridge_atomic_disable(&bridge.base, NULL);
	if (g_push_idle_calls == 0) {
		printf("idle_off: PASS (push_idle calls=0)\n");
	} else {
		printf("idle_off: FAIL (push_idle calls=%d, expected 0)\n",
		       g_push_idle_calls);
		failures++;
	}

	priv.msm_dp_display.power_on = true;
	g_push_idle_calls = 0;
	g_last_push_idle_ctrl = NULL;
	msm_dp_bridge_atomic_disable(&bridge.base, NULL);
	if (g_push_idle_calls == 1 && g_last_push_idle_ctrl == priv.ctrl) {
		printf("idle_on: PASS (push_idle calls=1, ctrl=%p)\n",
		       (void *)g_last_push_idle_ctrl);
	} else {
		printf("idle_on: FAIL (push_idle calls=%d ctrl=%p expected=%p)\n",
		       g_push_idle_calls, (void *)g_last_push_idle_ctrl,
		       (void *)priv.ctrl);
		failures++;
	}

	printf("result: %s (%d failure%s)\n", failures ? "FAIL" : "PASS",
	       failures, failures == 1 ? "" : "s");
	return failures ? 1 : 0;
}
"""


def build_harness(source_text):
    """Assemble the harness in source order without rewriting production code."""
    desc_struct = extract_desc_struct(source_text)
    desc_arrays = extract_desc_arrays(source_text)
    dt_match = extract_dt_match(source_text)
    atomic_disable = extract_atomic_disable(source_text)

    return "\n".join([
        STUBS,
        desc_struct,
        "",
        "\n\n".join(desc_arrays),
        "",
        dt_match,
        "",
        atomic_disable,
        "",
        MAIN,
    ])


def show_streams(label, completed):
    print("%s: exit %d" % (label, completed.returncode))
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    print("%s stdout:" % label)
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    print("%s stderr:" % label)
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n")


def run_tool(label, command, work_dir):
    print("%s command: %s" % (
        label, " ".join(shlex.quote(part) for part in command)))
    try:
        completed = subprocess.run(
            command,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        print("%s: FAIL to start: %s" % (label, exc))
        return None
    show_streams(label, completed)
    return completed.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Kaanapali DP descriptor and idle-guard check")
    parser.add_argument("--source", required=True, type=Path,
                        help="live dp_display.c to extract production code from")
    parser.add_argument("--work-dir", required=True, type=Path,
                        help="required directory for generated C and compiler output")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print("source: FAIL (%s is not a file)" % args.source)
        return 2

    try:
        source_text = args.source.read_text(encoding="utf-8")
    except OSError as exc:
        print("source: FAIL to read %s: %s" % (args.source, exc))
        return 2
    except UnicodeDecodeError as exc:
        print("source: FAIL to decode %s as UTF-8: %s" % (args.source, exc))
        return 2

    try:
        harness = build_harness(source_text)
    except ExtractionError as exc:
        print("extract: FAIL (%s)" % exc)
        return 2

    try:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print("work-dir: FAIL to create %s: %s" % (args.work_dir, exc))
        return 2

    c_path = work_dir / C_NAME
    bin_path = work_dir / BIN_NAME

    try:
        c_path.write_text(harness, encoding="utf-8")
    except OSError as exc:
        print("write: FAIL to write %s: %s" % (c_path, exc))
        return 2

    cc = shlex.split(os.environ.get("CC") or "cc")
    compile_command = cc + ["-std=c11", "-O0", "-o", str(bin_path), str(c_path)]
    compile_rc = run_tool("compile", compile_command, work_dir)
    if compile_rc != 0:
        print("compile: FAIL")
        return compile_rc if compile_rc is not None else 2
    print("compile: PASS")

    run_rc = run_tool("run", [str(bin_path)], work_dir)
    if run_rc != 0:
        print("run: FAIL")
        return run_rc if run_rc is not None else 2

    print("run: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
