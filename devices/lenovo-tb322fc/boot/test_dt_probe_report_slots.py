#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RED regression tests for the intended explicit ``--dt-probe-report-slots``
flag of the bootv4 narrow repacker (repack_boot_v4.py).

Author-only artifact.  It deliberately does NOT import any production helper:
every expectation is recomputed locally from the task contract so that the
tests stay independent of the future implementation.  It drives the real
repacker through ``subprocess`` and uses a REAL supplied ``avbtool`` (no mock
tool, no fake avbtool).

Run (executed inside the remote Docker build environment):

    python3 test_dt_probe_report_slots.py \
        --repacker          <updated repack_boot_v4.py> \
        --baseline-repacker <unmodified repack_boot_v4.py> \
        --avbtool           <avbtool.py> \
        --work-dir          <empty writable directory>

All fixtures, outputs, reports and logs are created exclusively under
``--work-dir`` (no ``tempfile`` defaults are used).  The process exits nonzero
on any failure and leaves per-case evidence (``*.cmd.txt`` / ``*.stdout.txt`` /
``*.stderr.txt`` / ``*.rc.txt``) under the work directory.

Initial state is intentionally RED: the flag-on success cases must fail because
the not-yet-updated repacker rejects the unknown flag, while the flag-off case
must already pass.  The negative cases add an explicit guard so they cannot
pass merely because argparse rejected an unknown flag.

Contract under test
-------------------
* ``--dt-probe-report-slots`` appends exactly two fixed ordinary cmdline
  parameters, in this order:
      kshimreport.kshim_probe=VALUE
      kshimreport.kshim_s0=VALUE
  where VALUE is the literal ``pending`` followed by 73 underscores (80 bytes).
* The original cmdline bytes are retained verbatim; a single separator is added
  when the original is nonempty, and an empty original gains no leading
  separator.
* The 1536-byte cmdline field [44:1580] is NUL-terminated and zero-padded; the
  maximum resulting text length is 1535 bytes.
* Reject: absent NUL terminator, any nonzero byte after the first terminator,
  overlong output, and the pre-existing exact parameter names
  ``kshimreport.kshim_probe``, ``kshimreport.kshim_s0``,
  ``androidboot.kshim_probe``, ``androidboot.kshim_s0``.  Unrelated similarly
  named parameters must NOT be rejected.
* Flag off retains the old behaviour exactly (byte-identical output).
"""

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import unittest

# --------------------------------------------------------------------------
# Android boot image v4 numeric layout (matches the production repacker).
# --------------------------------------------------------------------------
HDR = 4096
HEADER_SIZE = 1584
CMDLINE_START = 44
CMDLINE_END = 1580
CMDLINE_LEN = CMDLINE_END - CMDLINE_START          # 1536
SIGNATURE_OFF = 1580
MAX_TEXT = 1535

# Partition must fit header+kernel+footer: the real avbtool reserves
# MAX_VBMETA_SIZE + MAX_FOOTER_SIZE (69632 bytes) before the kernel, so 64 KiB
# is too small; 128 KiB leaves ample room.
PARTITION_SIZE = 131072
SALT_BYTES = bytes(range(32))
SALT_HEX = SALT_BYTES.hex()

# --------------------------------------------------------------------------
# Contract constants (independently computed here, never imported).
# --------------------------------------------------------------------------
VALUE = "pending" + ("_" * 73)                      # 80 bytes
PARAM_PROBE = "kshimreport.kshim_probe"
PARAM_S0 = "kshimreport.kshim_s0"
SEP = " "
P1 = PARAM_PROBE + "=" + VALUE
P2 = PARAM_S0 + "=" + VALUE
# For a nonempty original: <orig><sep><P1><sep><P2>  ->  2 separators added.
ADDED_NONEMPTY = 1 + len(P1) + 1 + len(P2)
MAX_ORIG_TEXT_LEN = MAX_TEXT - ADDED_NONEMPTY        # 1535 - 207 = 1328

CONFLICT_NAMES = (
    PARAM_PROBE,
    PARAM_S0,
    "androidboot.kshim_probe",
    "androidboot.kshim_s0",
)

FLAG = "--dt-probe-report-slots"

# --------------------------------------------------------------------------
# Globals populated from the command line in main().
# --------------------------------------------------------------------------
REPACKER = None
BASELINE_REPACKER = None
AVBTOOL = None
WORK_DIR = None


# --------------------------------------------------------------------------
# Small process/IO helpers.  Everything lives under WORK_DIR.
# --------------------------------------------------------------------------
class RunResult(object):
    def __init__(self, argv, cwd, returncode, stdout, stderr):
        self.argv = list(argv)
        self.cwd = cwd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def detail(self):
        return (
            "argv: %s\ncwd: %s\nreturncode: %s\n"
            "--- stdout ---\n%s\n--- stderr ---\n%s"
            % (" ".join(self.argv), self.cwd, self.returncode,
               self.stdout, self.stderr)
        )


def _case_dir(case):
    path = os.path.join(WORK_DIR, case)
    os.makedirs(path, exist_ok=True)
    return path


def sh(case, tag, argv, cwd):
    """Run a real command, capture output, and persist evidence under WORK_DIR."""
    proc = subprocess.run(
        list(argv), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    d = _case_dir(case)
    with open(os.path.join(d, tag + ".cmd.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(argv) + "\n")
    with open(os.path.join(d, tag + ".stdout.txt"), "w", encoding="utf-8") as f:
        f.write(stdout)
    with open(os.path.join(d, tag + ".stderr.txt"), "w", encoding="utf-8") as f:
        f.write(stderr)
    with open(os.path.join(d, tag + ".rc.txt"), "w", encoding="utf-8") as f:
        f.write("%d\n" % proc.returncode)
    return RunResult(argv, cwd, proc.returncode, stdout, stderr)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Fixture construction (real avbtool, not the production repacker).
# --------------------------------------------------------------------------
def arm64_kernel(size, fill):
    """A tiny but valid raw ARM64 image: >=64 bytes and 'ARM\\x64' at offset 56."""
    if size < 64:
        raise ValueError("kernel must be >= 64 bytes")
    data = bytearray([fill & 0xff] * size)
    struct.pack_into("<Q", data, 8, 0x80000)     # text_offset
    struct.pack_into("<Q", data, 16, size)       # image_size
    data[56:60] = b"ARM\x64"                     # ARM64 magic
    return bytes(data)


def cmdline_field(text):
    """Return a NUL-terminated, zero-padded 1536-byte cmdline field for text."""
    encoded = text.encode("utf-8")
    if len(encoded) + 1 > CMDLINE_LEN:
        raise ValueError("cmdline text too long for the field: %d" % len(encoded))
    return encoded + b"\x00" + b"\x00" * (CMDLINE_LEN - len(encoded) - 1)


def cmdline_field_raw(raw):
    if len(raw) != CMDLINE_LEN:
        raise ValueError("cmdline field must be exactly %d bytes" % CMDLINE_LEN)
    return bytes(raw)


def raw_boot_image(kernel, field):
    """Pre-AVB boot v4 image with header fields matching the repacker's checks."""
    if len(field) != CMDLINE_LEN:
        raise ValueError("cmdline field must be exactly %d bytes" % CMDLINE_LEN)
    header = bytearray(HDR)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))     # kernel_size
    struct.pack_into("<I", header, 12, 0)              # ramdisk_size = 0
    struct.pack_into("<I", header, 16, 0)              # os_version
    struct.pack_into("<I", header, 20, HEADER_SIZE)    # header_size = 1584
    struct.pack_into("<I", header, 40, 4)              # header_version = 4
    header[CMDLINE_START:CMDLINE_END] = field
    struct.pack_into("<I", header, SIGNATURE_OFF, 0)   # signature_size = 0
    body = bytes(header) + kernel
    body += b"\x00" * ((-len(body)) % 4096)            # keep image block aligned
    return body


def make_original(case, name, kernel, field):
    """Write a raw boot v4 image and sign it in place with the real avbtool."""
    d = _case_dir(case)
    path = os.path.join(d, name + ".img")
    with open(path, "wb") as handle:
        handle.write(raw_boot_image(kernel, field))
    res = sh(case, name + "_avbtool_add_hash_footer", [
        sys.executable, "-B", AVBTOOL, "add_hash_footer",
        "--image", path,
        "--partition_size", str(PARTITION_SIZE),
        "--partition_name", "boot",
        "--algorithm", "NONE",
        "--hash_algorithm", "sha256",
        "--salt", SALT_HEX,
        "--rollback_index", "0",
        "--rollback_index_location", "0",
    ], cwd=d)
    if res.returncode != 0:
        raise AssertionError("fixture add_hash_footer failed:\n" + res.detail())
    actual = os.path.getsize(path)
    if actual != PARTITION_SIZE:
        raise AssertionError(
            "fixture %s length %d != partition size %d" % (path, actual, PARTITION_SIZE))
    return path


def write_kernel(case, name, kernel):
    d = _case_dir(case)
    path = os.path.join(d, name + ".kernel")
    with open(path, "wb") as handle:
        handle.write(kernel)
    return path


def run_repacker(case, tag, original, kernel_path, use_flag, which="new"):
    d = _case_dir(case)
    # Each invocation gets its own subdirectory so every output is a real
    # ``boot.img`` named after the AVB partition; the official avbtool resolves
    # the descriptor's partition filename beside the tested image and therefore
    # reads exactly this file.  Baseline and updated runs stay separate tags.
    tag_dir = os.path.join(d, tag)
    os.makedirs(tag_dir, exist_ok=True)
    out = os.path.join(tag_dir, "boot.img")
    report = os.path.join(tag_dir, "report.json")
    repacker = REPACKER if which == "new" else BASELINE_REPACKER
    argv = [
        sys.executable, "-B", repacker,
        "--original", original,
        "--kernel", kernel_path,
        "--output", out,
        "--report", report,
        "--avbtool", AVBTOOL,
    ]
    if use_flag:
        argv.append(FLAG)
    res = sh(case, tag, argv, cwd=tag_dir)
    return res, out, report


def verify_image(case, tag, image):
    d = _case_dir(case)
    return sh(case, tag, [
        sys.executable, "-B", AVBTOOL, "verify_image", "--image", image,
    ], cwd=d)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def expected_text(original_text):
    """Independently computed expected cmdline text per the contract."""
    if original_text:
        return original_text + SEP + P1 + SEP + P2
    return P1 + SEP + P2


# --------------------------------------------------------------------------
# Tests.
# --------------------------------------------------------------------------
class DtProbeReportSlotsTests(unittest.TestCase):
    maxDiff = None

    # -- assertion helpers -------------------------------------------------
    def _assert_ok(self, res, what):
        self.assertEqual(res.returncode, 0,
                         "%s should have succeeded:\n%s" % (what, res.detail()))

    def _assert_rejected(self, res, out_path, what):
        self.assertNotEqual(
            res.returncode, 0,
            "%s should have been rejected but succeeded:\n%s" % (what, res.detail()))
        combined = (res.stdout + "\n" + res.stderr).lower()
        self.assertNotIn(
            "unrecognized arguments", combined,
            "%s was rejected by argparse as an unknown flag, not by real "
            "validation (the flag is likely not implemented yet):\n%s"
            % (what, res.detail()))
        self.assertFalse(
            os.path.exists(out_path),
            "%s was rejected but still produced output %s" % (what, out_path))

    def _assert_report_slots(self, report_path):
        self.assertTrue(os.path.exists(report_path),
                        "diagnostic report was not written: %s" % report_path)
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertIn("dt_probe_report_slots", report,
                      "report is missing the dt_probe_report_slots key")
        payload = report["dt_probe_report_slots"]
        self.assertIsInstance(payload, dict,
                              "dt_probe_report_slots must be a JSON object")
        slots = payload.get("slots")
        self.assertIsInstance(slots, dict,
                              "dt_probe_report_slots.slots must be a JSON object")
        self.assertEqual(slots, {PARAM_PROBE: VALUE, PARAM_S0: VALUE},
                         "slots must map exactly the two probe parameters to VALUE")
        self.assertEqual(payload.get("value_bytes"), 80,
                         "value_bytes must be the integer 80")
        changes = payload.get("allowed_header_changes")
        self.assertIsInstance(changes, list, "allowed_header_changes must be a list")
        self.assertEqual([list(item) for item in changes], [[8, 12], [44, 1580]],
                         "allowed_header_changes must be [[8,12],[44,1580]]")
        self.assertIs(payload.get("hardware_tested"), False,
                      "hardware_tested must be false")

    # -- constant sanity (contract is self-consistent) ---------------------
    def test_00_flag_recognized_positive_gate(self):
        self.assertEqual(len(VALUE), 80, "VALUE must be 80 bytes")
        self.assertEqual(len(P1), 23 + 1 + 80)
        self.assertEqual(len(P2), 20 + 1 + 80)
        self.assertEqual(ADDED_NONEMPTY, 207)
        self.assertEqual(MAX_ORIG_TEXT_LEN, 1328)

        case = "gate"
        orig = make_original(case, "orig", arm64_kernel(64, 0x90), cmdline_field(""))
        kern = write_kernel(case, "new", arm64_kernel(64, 0x91))
        res, _out, _rep = run_repacker(case, "gate_on", orig, kern, use_flag=True)
        self.assertEqual(
            res.returncode, 0,
            "a positive flag-on case must succeed before the failure cases are "
            "meaningful:\n" + res.detail())

    # -- case 1: flag off is byte-identical to the baseline repacker -------
    def test_01_flag_off_byte_identical_to_baseline(self):
        case = "case01_flag_off"
        orig_kernel = arm64_kernel(64, 0x11)
        field = cmdline_field("androidboot.hardware=qcom")
        orig = make_original(case, "orig", orig_kernel, field)
        new_kernel = arm64_kernel(128, 0x22)
        kern = write_kernel(case, "new", new_kernel)
        orig_hash_before = sha256_file(orig)

        res_new, out_new, _rep_new = run_repacker(
            case, "new_flagoff", orig, kern, use_flag=False, which="new")
        res_base, out_base, _rep_base = run_repacker(
            case, "baseline", orig, kern, use_flag=False, which="baseline")
        self._assert_ok(res_new, "new repacker (flag off)")
        self._assert_ok(res_base, "baseline repacker")

        new_bytes = read_bytes(out_new)
        base_bytes = read_bytes(out_base)
        orig_bytes = read_bytes(orig)
        self.assertEqual(new_bytes, base_bytes,
                         "flag-off output is not byte-identical to the baseline")

        # Header beyond kernel_size is untouched; kernel_size is updated.
        self.assertEqual(new_bytes[:8], orig_bytes[:8], "header [0:8] changed")
        self.assertEqual(new_bytes[12:HDR], orig_bytes[12:HDR],
                         "header beyond kernel_size changed")
        self.assertEqual(struct.unpack_from("<I", new_bytes, 8)[0], len(new_kernel),
                         "kernel_size was not updated to the new kernel length")
        self.assertEqual(sha256_file(orig), orig_hash_before,
                         "flag-off run modified the original input")

        v = verify_image(case, "verify_off", out_new)
        self._assert_ok(v, "avbtool verify_image (flag off)")

    # -- case 2: flag on preserves whitespace and writes the report --------
    def test_02_flag_on_preserves_cmdline_and_report(self):
        case = "case02_flag_on"
        orig_kernel = arm64_kernel(64, 0x33)
        orig_text = "  androidboot.selinux=permissive  "
        orig = make_original(case, "orig", orig_kernel, cmdline_field(orig_text))
        # Pass the identical kernel so the kernel bytes stay unchanged too.
        kern = write_kernel(case, "same_kernel", orig_kernel)
        orig_hash_before = sha256_file(orig)

        res, out, report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_ok(res, "flag-on success with meaningful cmdline")

        out_bytes = read_bytes(out)
        orig_bytes = read_bytes(orig)

        want_text = expected_text(orig_text)  # verbatim original + single separators
        want_field = cmdline_field(want_text)
        self.assertEqual(out_bytes[CMDLINE_START:CMDLINE_END], want_field,
                         "cmdline field bytes differ from the contract")
        # Prove the leading/trailing whitespace of the original survived verbatim.
        self.assertTrue(want_text.startswith("  androidboot.selinux=permissive  "),
                        "contract helper lost the original whitespace")

        # Only the allowed header ranges [8:12] and [44:1580] may change.
        self.assertEqual(out_bytes[:8], orig_bytes[:8], "header [0:8] changed")
        self.assertEqual(out_bytes[12:CMDLINE_START], orig_bytes[12:CMDLINE_START],
                         "header [12:44] changed")
        self.assertEqual(out_bytes[CMDLINE_END:HDR], orig_bytes[CMDLINE_END:HDR],
                         "header [1580:4096] changed")
        kernel_size = struct.unpack_from("<I", orig_bytes, 8)[0]
        self.assertEqual(out_bytes[HDR:HDR + kernel_size],
                         orig_bytes[HDR:HDR + kernel_size],
                         "original kernel bytes changed")
        self.assertEqual(sha256_file(orig), orig_hash_before,
                         "flag-on run modified the original input")

        v = verify_image(case, "verify_on", out)
        self._assert_ok(v, "avbtool verify_image (flag on)")

        self._assert_report_slots(report)

    # -- case 3: empty original gains no leading separator -----------------
    def test_03_flag_on_empty_cmdline_no_leading_separator(self):
        case = "case03_empty"
        orig_kernel = arm64_kernel(64, 0x44)
        orig = make_original(case, "orig", orig_kernel, cmdline_field(""))
        kern = write_kernel(case, "new", arm64_kernel(128, 0x45))

        res, out, report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_ok(res, "flag-on success with empty original cmdline")

        out_bytes = read_bytes(out)
        want_field = cmdline_field(expected_text(""))
        self.assertEqual(out_bytes[CMDLINE_START:CMDLINE_END], want_field,
                         "empty-original field bytes differ from the contract")
        self.assertEqual(out_bytes[CMDLINE_START], ord("k"),
                         "empty original must not gain a leading separator")

        v = verify_image(case, "verify_on", out)
        self._assert_ok(v, "avbtool verify_image (empty original)")
        self._assert_report_slots(report)

    # -- case 4a: exactly the maximum length succeeds ----------------------
    def test_04a_flag_on_exact_max_length_succeeds(self):
        case = "case04a_max"
        orig_text = "a" * MAX_ORIG_TEXT_LEN
        want_text = expected_text(orig_text)
        self.assertEqual(len(want_text.encode("utf-8")), MAX_TEXT,
                         "the maximum-length case must produce exactly 1535 bytes")

        orig_kernel = arm64_kernel(64, 0x55)
        orig = make_original(case, "orig", orig_kernel, cmdline_field(orig_text))
        kern = write_kernel(case, "new", arm64_kernel(128, 0x56))

        res, out, report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_ok(res, "flag-on success at the 1535-byte maximum")

        out_bytes = read_bytes(out)
        want_field = cmdline_field(want_text)
        self.assertEqual(len(want_field), CMDLINE_LEN,
                         "the maximum-length field must exactly fill the buffer")
        self.assertEqual(out_bytes[CMDLINE_START:CMDLINE_END], want_field,
                         "maximum-length field bytes differ from the contract")

        v = verify_image(case, "verify_on", out)
        self._assert_ok(v, "avbtool verify_image (max length)")

    # -- case 4b: one byte larger is rejected before output exists ---------
    def test_04b_flag_on_one_byte_over_max_rejects(self):
        case = "case04b_over"
        orig_text = "a" * (MAX_ORIG_TEXT_LEN + 1)
        want_text = expected_text(orig_text)
        self.assertEqual(len(want_text.encode("utf-8")), MAX_TEXT + 1)

        orig_kernel = arm64_kernel(64, 0x57)
        orig = make_original(case, "orig", orig_kernel, cmdline_field(orig_text))
        kern = write_kernel(case, "new", arm64_kernel(128, 0x58))

        res, out, _report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_rejected(res, out, "overlong (1536-byte) cmdline")

    # -- case 5a: source lacks a NUL terminator ----------------------------
    def test_05a_flag_on_source_without_nul_terminator_rejects(self):
        case = "case05a_nonul"
        field = bytes((0x41 + (i % 26)) for i in range(CMDLINE_LEN))
        self.assertNotIn(b"\x00", field, "fixture must have no NUL terminator")

        orig_kernel = arm64_kernel(64, 0x59)
        orig = make_original(case, "orig", orig_kernel, cmdline_field_raw(field))
        kern = write_kernel(case, "new", arm64_kernel(128, 0x5A))

        res, out, _report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_rejected(res, out, "source cmdline without NUL terminator")

    # -- case 5b: nonzero bytes after the first NUL ------------------------
    def test_05b_flag_on_nonzero_after_nul_rejects(self):
        case = "case05b_embedded"
        raw = bytearray(CMDLINE_LEN)
        text = b"androidboot.selinux=enforcing"
        raw[:len(text)] = text
        raw[len(text)] = 0x00
        tail = b"garbage-after-nul"
        raw[len(text) + 1:len(text) + 1 + len(tail)] = tail
        field = cmdline_field_raw(bytes(raw))
        self.assertIn(b"\x00", field)
        tail_off = len(text) + 1
        self.assertTrue(any(field[tail_off:]),
                        "fixture must carry nonzero bytes after the first NUL")

        orig_kernel = arm64_kernel(64, 0x5B)
        orig = make_original(case, "orig", orig_kernel, field)
        kern = write_kernel(case, "new", arm64_kernel(128, 0x5C))

        res, out, _report = run_repacker(case, "on", orig, kern, use_flag=True)
        self._assert_rejected(res, out, "nonzero bytes after the first NUL")

    # -- case 6a: the four conflicting names each reject -------------------
    def test_06a_flag_on_conflicting_names_reject(self):
        for index, name in enumerate(CONFLICT_NAMES):
            case = "case06a_%d" % index
            orig_kernel = arm64_kernel(64, 0x60 + index)
            orig_text = "quiet " + name + "=1"
            orig = make_original(case, "orig", orig_kernel, cmdline_field(orig_text))
            kern = write_kernel(case, "new", arm64_kernel(128, 0x70 + index))
            res, out, _report = run_repacker(case, "on", orig, kern, use_flag=True)
            self._assert_rejected(res, out, "pre-existing exact name %s" % name)

    # -- case 6b: unrelated similarly named parameters are permitted -------
    def test_06b_flag_on_unrelated_similar_names_permitted(self):
        tokens = (
            "kshimreport.kshim_probe_extra=1",
            "androidboot.kshim_probe_extra=1",
            "kshimreport.kshim_s0x=2",
            "vendor.kshim_probe=1",
        )
        for index, token in enumerate(tokens):
            case = "case06b_%d" % index
            orig_kernel = arm64_kernel(64, 0x80 + index)
            orig_text = "quiet " + token
            orig = make_original(case, "orig", orig_kernel, cmdline_field(orig_text))
            kern = write_kernel(case, "new", arm64_kernel(128, 0x90 + index))
            res, out, _report = run_repacker(case, "on", orig, kern, use_flag=True)
            self._assert_ok(res, "similar-but-unrelated name %s" % token)
            out_bytes = read_bytes(out)
            want = expected_text(orig_text).encode("utf-8")
            self.assertEqual(
                out_bytes[CMDLINE_START:CMDLINE_START + len(want)], want,
                "unrelated name %s was not preserved verbatim" % token)


def main():
    global REPACKER, BASELINE_REPACKER, AVBTOOL, WORK_DIR

    parser = argparse.ArgumentParser(
        description="RED regression tests for repack_boot_v4 "
                    "--dt-probe-report-slots")
    parser.add_argument("--repacker", required=True,
                        help="repacker under test (with the new flag)")
    parser.add_argument("--baseline-repacker", required=True,
                        help="unmodified repacker used for the flag-off baseline")
    parser.add_argument("--avbtool", required=True,
                        help="official avbtool.py (used for real AVB signing)")
    parser.add_argument("--work-dir", required=True,
                        help="writable directory for all fixtures/logs/outputs")
    args, _rest = parser.parse_known_args()

    REPACKER = os.path.abspath(args.repacker)
    BASELINE_REPACKER = os.path.abspath(args.baseline_repacker)
    AVBTOOL = os.path.abspath(args.avbtool)
    WORK_DIR = os.path.abspath(args.work_dir)
    os.makedirs(WORK_DIR, exist_ok=True)

    missing = [p for p in (REPACKER, BASELINE_REPACKER, AVBTOOL)
               if not os.path.isfile(p)]
    if missing:
        sys.stderr.write("missing required file(s): %s\n" % ", ".join(missing))
        return 2

    with open(os.path.join(WORK_DIR, "test_context.txt"), "w",
              encoding="utf-8") as handle:
        handle.write("repacker=%s\nbaseline_repacker=%s\navbtool=%s\n"
                     "work_dir=%s\n" % (REPACKER, BASELINE_REPACKER, AVBTOOL, WORK_DIR))

    suite = unittest.TestLoader().loadTestsFromTestCase(DtProbeReportSlotsTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
