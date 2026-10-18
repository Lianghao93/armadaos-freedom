#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derive TB323FU Gen5 internal/TF root-selector DTBs from a unified DTB.

The newly compiled unified Baldur DTB is the sole source of hardware, memory,
reserved-memory and framebuffer nodes. Each output is a byte copy of that DTB
with exactly five /chosen string properties replaced:

  armada,boot-stage, armada,root-medium, armada,root-partuuid,
  armada,root-device-id and bootargs.

The four identity values are read from the matching accepted internal/TF
template at run time. They are never hard-coded and never copied from the
template DTB wholesale. bootargs comes from one explicit UTF-8 one-line file.

A revert-and-canonicalise check copies each derived DTB, restores only those
five /chosen properties from the new DTB (or deletes them when absent there),
then compares the complete sorted "dtc -O dts" text with the new DTB. That
proves every other node and property survives unchanged, without matching
property-name lines outside /chosen.

Offline only: no device access, no flashing, no network and no kernel build.
The external-initrd index (kshim,initrd-entry) is left to the boot packer.
Requires fdtget, fdtput and dtc from the device-tree-compiler package.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

BOOT_STAGE = "armada,boot-stage"
ROOT_MEDIUM = "armada,root-medium"
ROOT_PARTUUID = "armada,root-partuuid"
ROOT_DEVICE_ID = "armada,root-device-id"
BOOTARGS = "bootargs"

IDENTITY_PROPS = (BOOT_STAGE, ROOT_MEDIUM, ROOT_PARTUUID, ROOT_DEVICE_ID)
DERIVED_PROPS = IDENTITY_PROPS + (BOOTARGS,)

REQUIRED_COMPATIBLES = ("lenovo,baldur", "qcom,kaanapali")
FORBIDDEN_COMPATIBLE = "sm8750"
REQUIRED_BOOTARGS = ("baldur.end=hold",
                     "cpuidle_psci_domain.allow_cluster_off=0")
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

STAGE = "init"
OUT = None
WORK = None
LOGS = None
OUT_OWNED = False
ENV = None
FDTGET = None
FDTPUT = None
DTC = None


class BuildError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage
        self.message = message


def set_stage(stage):
    global STAGE
    STAGE = stage


def fail(message):
    raise BuildError(STAGE, message)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def last_stderr(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(4000)
    except OSError:
        return ""
    lines = [line.strip() for line in data.decode("utf-8", "replace").splitlines()
             if line.strip()]
    return lines[-1][:300] if lines else ""


def run(cmd, name, stdout_path=None, expect_success=True):
    """Run one helper and log its streams without storing its argv.

    Identity values and bootargs text are passed as argv, so they must not be
    copied into metadata, failure records or error text.
    """
    out_log = os.path.join(LOGS, name + ".stdout")
    err_log = os.path.join(LOGS, name + ".stderr")
    target = stdout_path or out_log
    try:
        with open(target, "wb") as fout, open(err_log, "wb") as ferr:
            proc = subprocess.run(cmd, cwd=WORK, env=ENV,
                                  stdout=fout, stderr=ferr)
    except OSError as error:
        fail("%s could not run: %s" % (name, error))
    if expect_success and proc.returncode != 0:
        detail = last_stderr(err_log)
        fail("%s failed rc=%d%s"
             % (name, proc.returncode, (": " + detail) if detail else ""))
    return proc.returncode, out_log, err_log


def require_tool(name):
    path = shutil.which(name)
    if not path:
        fail("required tool not found in PATH: %s" % name)
    return path


def read_text(dtb, node, prop, tag):
    """Read one string property; None means the property or node is absent."""
    rc, out_log, _ = run([FDTGET, "-t", "s", dtb, node, prop], tag,
                         expect_success=False)
    if rc != 0:
        return None
    with open(out_log, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("%s is not valid UTF-8" % prop)
    if text.endswith("\n"):
        text = text[:-1]
    return text


def read_required(dtb, prop, label):
    value = read_text(dtb, "/chosen", prop,
                      "read-%s-%s" % (label, prop.replace(",", "_")))
    if value is None:
        fail("%s is missing /chosen %s" % (label, prop))
    return value


def list_properties(dtb, node, label):
    rc, out_log, _ = run([FDTGET, "-p", dtb, node],
                         "list-%s" % label, expect_success=False)
    if rc != 0:
        fail("%s has no %s node" % (label, node))
    with open(out_log, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("%s property names are not valid UTF-8" % label)
    return {line for line in text.splitlines() if line}


def validate_compatible(dtb, label):
    compatible = read_text(dtb, "/", "compatible", "compatible-%s" % label)
    if compatible is None:
        fail("%s has no root compatible property" % label)
    tokens = compatible.split()
    missing = [token for token in REQUIRED_COMPATIBLES if token not in tokens]
    if missing:
        fail("%s root compatible is missing %s"
             % (label, ", ".join(missing)))
    for token in tokens:
        if FORBIDDEN_COMPATIBLE in token:
            fail("%s root compatible names Gen4 sm8750" % label)


def validate_identity(template, label, expected_medium):
    stage = read_required(template, BOOT_STAGE, label)
    if stage != "os":
        fail("%s %s is not 'os'" % (label, BOOT_STAGE))

    medium = read_required(template, ROOT_MEDIUM, label)
    if medium != expected_medium:
        fail("%s %s is not '%s'" % (label, ROOT_MEDIUM, expected_medium))

    partuuid = read_required(template, ROOT_PARTUUID, label)
    if not GUID_RE.fullmatch(partuuid):
        fail("%s %s is not a canonical GUID" % (label, ROOT_PARTUUID))

    device_id = read_required(template, ROOT_DEVICE_ID, label)
    if (not device_id or device_id != device_id.strip()
            or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in device_id)):
        fail("%s %s is empty or malformed" % (label, ROOT_DEVICE_ID))

    return {
        BOOT_STAGE: stage,
        ROOT_MEDIUM: medium,
        ROOT_PARTUUID: partuuid,
        ROOT_DEVICE_ID: device_id,
    }


def read_bootargs(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if b"\x00" in data:
        fail("bootargs file contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("bootargs file is not valid UTF-8")
    if text.startswith("\ufeff"):
        fail("bootargs file starts with a UTF-8 BOM")
    if text.endswith("\n"):
        text = text[:-1]
    if "\n" in text or "\r" in text:
        fail("bootargs file must contain exactly one line")
    if not text.strip():
        fail("bootargs file is empty")
    if any(ord(ch) < 0x20 and ch != "\t" for ch in text):
        fail("bootargs file contains a control character")
    tokens = text.split()
    for token in REQUIRED_BOOTARGS:
        if token not in tokens:
            fail("bootargs file is missing required token %r" % token)
    return text


def put_string(dtb, prop, value, tag):
    run([FDTPUT, "-t", "s", dtb, "/chosen", prop, value], tag)


def verify_derived(dtb, identity, bootargs, label):
    for prop in IDENTITY_PROPS:
        actual = read_required(dtb, prop, label)
        if actual != identity[prop]:
            fail("%s %s read-back differs from its template" % (label, prop))
    actual = read_required(dtb, BOOTARGS, label)
    if actual != bootargs:
        fail("%s bootargs read-back differs from the bootargs file" % label)
    tokens = actual.split()
    for token in REQUIRED_BOOTARGS:
        if token not in tokens:
            fail("%s bootargs lost required token %r" % (label, token))


def derive(new_dtb, identity, bootargs, label):
    target = os.path.join(WORK, label + ".dtb")
    shutil.copyfile(new_dtb, target)
    for prop in IDENTITY_PROPS:
        put_string(target, prop, identity[prop],
                   "put-%s-%s" % (label, prop.replace(",", "_")))
    put_string(target, BOOTARGS, bootargs, "put-%s-bootargs" % label)
    verify_derived(target, identity, bootargs, label)
    return target


def revert_and_compare(source_dtb, source_props, derived, label):
    revert = os.path.join(WORK, label + ".revert.dtb")
    shutil.copyfile(derived, revert)

    for prop in DERIVED_PROPS:
        if prop in source_props:
            value = read_text(source_dtb, "/chosen", prop,
                              "source-%s-%s"
                              % (label, prop.replace(",", "_")))
            if value is None:
                fail("new DTB lost /chosen %s before the revert check" % prop)
            put_string(revert, prop, value,
                       "revert-%s-%s" % (label, prop.replace(",", "_")))
        else:
            rc, _, _ = run([FDTPUT, "-d", revert, "/chosen", prop],
                           "revert-delete-%s-%s"
                           % (label, prop.replace(",", "_")),
                           expect_success=False)
            if rc != 0:
                fail("could not remove %s during the %s revert check"
                     % (prop, label))

    revert_dts = os.path.join(WORK, label + ".revert.dts")
    run([DTC, "-I", "dtb", "-O", "dts", "-s", revert],
        "dtc-%s-revert" % label, stdout_path=revert_dts)
    return revert_dts


def dts_lines(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def prepare(args):
    global OUT, WORK, LOGS, OUT_OWNED, ENV, FDTGET, FDTPUT, DTC

    set_stage("prepare")
    OUT = os.path.abspath(args.out)
    if os.path.islink(args.out) or os.path.islink(OUT):
        fail("output directory must not be a symlink: %s" % args.out)

    inputs = {
        "dtb": os.path.abspath(args.dtb),
        "internal_template": os.path.abspath(args.internal_template),
        "tf_template": os.path.abspath(args.tf_template),
        "bootargs_file": os.path.abspath(args.bootargs_file),
    }
    for key, path in inputs.items():
        if not os.path.isfile(path):
            fail("%s not found: %s" % (key, path))

    real_out = os.path.realpath(OUT)
    for key, path in inputs.items():
        real_in = os.path.realpath(path)
        if real_in == real_out or real_in.startswith(real_out + os.sep):
            fail("%s lives inside --out; refusing to overwrite inputs" % key)

    if os.path.exists(OUT):
        if not os.path.isdir(OUT):
            fail("--out exists and is not a directory: %s" % OUT)
        if os.listdir(OUT):
            fail("--out is not empty; refusing to delete existing contents")
    else:
        os.makedirs(OUT)
    OUT_OWNED = True

    WORK = os.path.join(OUT, "work")
    LOGS = os.path.join(OUT, "logs")
    os.makedirs(WORK)
    os.makedirs(LOGS)

    ENV = dict(os.environ)
    ENV["PYTHONDONTWRITEBYTECODE"] = "1"
    ENV["TMPDIR"] = WORK
    ENV["TEMP"] = WORK
    ENV["TMP"] = WORK

    FDTGET = require_tool("fdtget")
    FDTPUT = require_tool("fdtput")
    DTC = require_tool("dtc")
    return inputs


def main():
    parser = argparse.ArgumentParser(
        description="Derive TB323FU Gen5 internal/TF root-selector DTBs")
    parser.add_argument("--dtb", required=True,
                        help="newly compiled unified full Baldur DTB")
    parser.add_argument("--internal-template", required=True,
                        help="accepted internal identity template DTB")
    parser.add_argument("--tf-template", required=True,
                        help="accepted TF identity template DTB")
    parser.add_argument("--bootargs-file", required=True,
                        help="explicit UTF-8 one-line Gen5 bootargs")
    parser.add_argument("--out", required=True,
                        help="empty output directory")
    args = parser.parse_args()

    try:
        inputs = prepare(args)
        source_hashes = {key: sha256_file(path)
                         for key, path in inputs.items()}
        source_bytes = {key: os.path.getsize(path)
                        for key, path in inputs.items()}

        set_stage("validate-dts")
        validate_compatible(inputs["dtb"], "new-unified-dtb")
        validate_compatible(inputs["internal_template"], "internal-template")
        validate_compatible(inputs["tf_template"], "tf-template")

        source_props = list_properties(inputs["dtb"], "/chosen",
                                       "new-unified-dtb")
        identity_internal = validate_identity(
            inputs["internal_template"], "internal-template", "internal")
        identity_tf = validate_identity(
            inputs["tf_template"], "tf-template", "tf")
        bootargs = read_bootargs(inputs["bootargs_file"])

        # The new DTB is copied before any edit; only these five /chosen
        # properties are put on the copies.
        set_stage("derive")
        internal = derive(inputs["dtb"], identity_internal, bootargs, "internal")
        tf = derive(inputs["dtb"], identity_tf, bootargs, "tf")

        set_stage("preservation")
        source_dts = os.path.join(WORK, "source.dts")
        run([DTC, "-I", "dtb", "-O", "dts", "-s", inputs["dtb"]],
            "dtc-source", stdout_path=source_dts)
        source_lines = dts_lines(source_dts)
        for label, derived in (("internal", internal), ("tf", tf)):
            revert_dts = revert_and_compare(
                inputs["dtb"], source_props, derived, label)
            if dts_lines(revert_dts) != source_lines:
                fail("%s derived DTB changed content outside the five "
                     "/chosen properties" % label)

        set_stage("recheck-sources")
        for key, path in inputs.items():
            if sha256_file(path) != source_hashes[key]:
                fail("input changed during the run: %s" % key)

        set_stage("publish")
        outputs = {}
        for label, derived in (("internal", internal), ("tf", tf)):
            name = label + ".dtb"
            destination = os.path.join(OUT, name)
            shutil.copyfile(derived, destination)
            outputs[label] = {
                "file": name,
                "medium": label,
                "bytes": os.path.getsize(destination),
                "sha256": sha256_file(destination),
            }

        set_stage("metadata")
        metadata = {
            "artifact": "gen5-root-selector-dtbs",
            "hardware_validated": False,
            "offline_verified": True,
            "sources": {
                "dtb": {
                    "role": "new-unified-full-baldur",
                    "path": inputs["dtb"],
                    "bytes": source_bytes["dtb"],
                    "sha256": source_hashes["dtb"],
                },
                "internal_template": {
                    "role": "old-accepted-internal",
                    "path": inputs["internal_template"],
                    "bytes": source_bytes["internal_template"],
                    "sha256": source_hashes["internal_template"],
                },
                "tf_template": {
                    "role": "old-accepted-tf",
                    "path": inputs["tf_template"],
                    "bytes": source_bytes["tf_template"],
                    "sha256": source_hashes["tf_template"],
                },
                "bootargs_file": {
                    "role": "explicit-gen5-cmdline",
                    "path": inputs["bootargs_file"],
                    "bytes": source_bytes["bootargs_file"],
                    "sha256": source_hashes["bootargs_file"],
                },
            },
            "changed_allowlist": list(DERIVED_PROPS),
            "validation": {
                "compatibles_required": list(REQUIRED_COMPATIBLES),
                "compatibles_forbidden": [FORBIDDEN_COMPATIBLE],
                "compatibles_checked": [
                    "new-unified-dtb", "internal-template", "tf-template"
                ],
                "bootargs_utf8_one_line": True,
                "bootargs_required_tokens": list(REQUIRED_BOOTARGS),
                "identity_templates": {
                    "internal": "internal-template",
                    "tf": "tf-template",
                },
                "preservation_method": (
                    "copies of the derived DTBs have only these five /chosen "
                    "properties restored from the new DTB and their full "
                    "dtc -s text is compared with the new DTB"),
                "source_canonical_dts_sha256": sha256_file(source_dts),
                "canonical_revert_identical": True,
                "identity_values_recorded": False,
                "bootargs_text_recorded": False,
            },
            "outputs": outputs,
            "tools": {
                "fdtget": FDTGET,
                "fdtput": FDTPUT,
                "dtc": DTC,
            },
            "caveats": [
                "Offline derivation only; no real device boot or flashing "
                "has been performed.",
                "hardware_validated is false.",
                "Identity values and bootargs text are intentionally not "
                "recorded in this metadata or in the repository.",
                "kshim,initrd-entry is not touched here; the offline boot "
                "packer owns the external-initrd index.",
            ],
        }
        with open(os.path.join(OUT, "metadata.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

        sys.stdout.write("derived DTBs written: internal.dtb, tf.dtb\n")
        sys.stdout.write("metadata: %s\n"
                         % os.path.join(OUT, "metadata.json"))
        return 0
    except BuildError as error:
        stage, message = error.stage, error.message
    except Exception as error:  # still leave a compact failure record
        stage, message = STAGE, "%s: %s" % (type(error).__name__, error)

    if OUT_OWNED:
        failure = {
            "artifact": "gen5-root-selector-dtbs",
            "stage": stage,
            "error": message,
            "hardware_validated": False,
        }
        try:
            with open(os.path.join(OUT, "failure.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(failure, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError:
            pass
    sys.stderr.write("FAILED at stage '%s': %s\n" % (stage, message))
    return 1


if __name__ == "__main__":
    sys.exit(main())
