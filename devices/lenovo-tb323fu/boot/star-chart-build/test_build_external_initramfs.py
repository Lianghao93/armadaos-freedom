"""Focused tests for build_external_initramfs.py (Linux, real tools only).

Four cases:
  1. an absolute template member is rejected before cpio extraction;
  2. compressed modules and a missing required module are rejected;
  3. a small fixture template plus module tree is built end to end with the
     real gzip/cpio/depmod/mksquashfs (ZSTD) toolchain, and the resulting gzip
     newc archive is inspected for root ownership, exact /lib/modules
     replacement and preserved template payloads/metadata;
  4. the --early-only selection resolves a transitive fixture modules.dep,
     keeps the dependency closure and drops unrelated modules and their
     modules.order lines.

Nothing is stubbed: the failure cases still decompress a real gzip cpio, and
the success case runs depmod and mksquashfs.  If the container lacks a tool
the case is skipped instead of pretending the wiring passed.
"""

import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "build_external_initramfs.py")
KVER = "7.2.6-tb323fu-test"

INIT = b"#!/bin/sh\n# fixture init\nexec /bin/sh\n"
RESOLVER = b"#!/bin/sh\n# fixture root resolver\n"
BUSYBOX = b"\x7fELF fixture helper\n"
FIRMWARE = b"fixture firmware payload\n"
FONT = b"fixture console font\n"
POLICY = b"fixture root policy stays verbatim\n"


def missing(names):
    return [name for name in names if shutil.which(name) is None]


MISSING_BASIC = missing(("gzip", "cpio"))
MISSING_ALL = missing(("gzip", "cpio", "depmod", "mksquashfs"))


def cpio_member(name, mode, data=b"", rdev=(0, 0)):
    name_bytes = os.fsencode(name) + b"\x00"
    fields = [
        0, mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1],
        len(name_bytes), 0,
    ]
    header = b"070701" + b"".join(b"%08x" % value for value in fields)
    assert len(header) == 110
    name_pad = (-(len(header) + len(name_bytes))) % 4
    data_pad = (-len(data)) % 4
    return header + name_bytes + b"\x00" * name_pad + data + b"\x00" * data_pad


def directory(name, mode=0o755):
    return cpio_member(name, stat.S_IFDIR | mode)


def regular(name, data, mode=0o644):
    return cpio_member(name, stat.S_IFREG | mode, data)


def make_template(path, extra=()):
    entries = [
        directory("bin"),
        directory("etc"),
        directory("lib"),
        directory("lib/modules"),
        directory("lib/modules/6.1.0-old"),
        directory("lib/modules/6.1.0-old/kernel"),
        directory("usr"),
        directory("usr/lib"),
        directory("usr/lib/firmware"),
        directory("usr/share"),
        directory("usr/share/fonts"),
        regular("init", INIT, 0o755),
        regular("armada-root-resolve", RESOLVER, 0o755),
        regular("bin/busybox", BUSYBOX, 0o755),
        regular("etc/root-policy", POLICY, 0o644),
        regular("usr/lib/firmware/fixture.bin", FIRMWARE, 0o644),
        regular("usr/share/fonts/fixture.psf", FONT, 0o644),
        regular("lib/modules/6.1.0-old/kernel/old.ko", b"old module", 0o644),
    ]
    entries.extend(extra)
    entries.append(cpio_member("TRAILER!!!", 0))
    with open(path, "wb") as handle:
        handle.write(gzip.compress(b"".join(entries), compresslevel=9,
                                   mtime=0))


def parse_cpio(blob):
    members = []
    offset = 0
    while True:
        header = blob[offset:offset + 110]
        if len(header) != 110 or header[:6] not in (b"070701", b"070702"):
            raise AssertionError("bad cpio at %d" % offset)
        fields = [int(header[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        mode, uid, gid, filesize = fields[1], fields[2], fields[3], fields[6]
        namesize = fields[11]
        name_bytes = blob[offset + 110:offset + 110 + namesize]
        name = os.fsdecode(name_bytes[:-1])
        data_start = offset + 110 + namesize + (
            (-(110 + namesize)) % 4)
        payload = blob[data_start:data_start + filesize]
        offset = data_start + filesize + (-filesize % 4)
        if name == "TRAILER!!!":
            break
        members.append({
            "name": name,
            "mode": stat.S_IMODE(mode),
            "kind": stat.S_IFMT(mode),
            "uid": uid,
            "gid": gid,
            "data": payload,
        })
    return members


def ko_bytes():
    for candidate in ("/bin/true", os.path.realpath("/bin/sh"), sys.executable):
        try:
            with open(candidate, "rb") as handle:
                return handle.read()
        except OSError:
            continue
    raise unittest.SkipTest("no ELF binary available for module fixtures")


def load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_external_initramfs_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load %s" % SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildExternalInitramfsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = self._tmp.name
        self.elf = ko_bytes()

    def _write(self, path, data, mode=0o644):
        if isinstance(data, str):
            data = data.encode("utf-8")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        os.chmod(path, mode)

    def _modules(self, name, omit=(), compressed=None):
        root = os.path.join(self.base, name)
        module_dir = os.path.join(root, "staging", "lib", "modules", KVER)
        files = {
            "kernel/drivers/soc/qcom/qcom_q6v5_pas.ko": self.elf,
            "kernel/drivers/input/touchscreen/nt36536_ts.ko": self.elf,
            "kernel/drivers/misc/fixture.ko": self.elf,
        }
        for relative in omit:
            files.pop(relative, None)
        for relative, payload in files.items():
            self._write(os.path.join(module_dir, relative), payload)
        self._write(os.path.join(module_dir, "vmlinuz"), b"kernel image")
        self._write(os.path.join(module_dir, "dtb", "fixture.dtb"), b"dtb")
        self._write(os.path.join(module_dir, "modules.order"),
                    "\n".join(sorted(files)) + "\n")
        self._write(os.path.join(module_dir, "modules.builtin"), b"")
        self._write(os.path.join(module_dir, "modules.alias"), b"")
        if compressed:
            self._write(os.path.join(module_dir, compressed), b"compressed")
        os.symlink("/tmp", os.path.join(module_dir, "build"))
        os.symlink("/tmp", os.path.join(module_dir, "source"))
        return module_dir

    def _run(self, out, template, module_dir):
        return subprocess.run(
            [sys.executable, "-B", SCRIPT,
             "--template-initrd", template,
             "--module-dir", module_dir,
             "--kernel-release", KVER,
             "--out", out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=240)

    def _closure_modules(self):
        module_dir = os.path.join(self.base, "closure", "staging", "lib",
                                  "modules", KVER)
        deps = {
            "kernel/drivers/soc/qcom/qcom_q6v5_pas.ko": [
                "kernel/drivers/soc/qcom/qcom_common.ko",
                "kernel/drivers/soc/qcom/qcom_q6v5.ko",
                "kernel/drivers/soc/qcom/qcom_sysmon.ko",
                "kernel/drivers/soc/qcom/qcom_pil_info.ko",
            ],
            "kernel/drivers/input/touchscreen/nt36536_ts.ko": [],
            "kernel/drivers/soc/qcom/qcom_common.ko": [
                "kernel/drivers/firmware/qcom_scm_helper.ko",
            ],
            "kernel/drivers/soc/qcom/qcom_q6v5.ko": [],
            "kernel/drivers/soc/qcom/qcom_sysmon.ko": [],
            "kernel/drivers/soc/qcom/qcom_pil_info.ko": [],
            "kernel/drivers/firmware/qcom_scm_helper.ko": [],
            "kernel/drivers/misc/unrelated.ko": [],
        }
        for relative in deps:
            self._write(os.path.join(module_dir, relative),
                        b"\x7fELF fixture module\n")
        lines = ["%s:%s" % (relative, "".join(" " + dep for dep in listed))
                 for relative, listed in deps.items()]
        self._write(os.path.join(module_dir, "modules.order"),
                    "\n".join(deps) + "\n")
        self._write(os.path.join(module_dir, "modules.dep"),
                    "\n".join(reversed(lines)) + "\n")
        self._write(os.path.join(module_dir, "modules.builtin"), b"")
        return module_dir

    @unittest.skipIf(MISSING_BASIC, "missing tools: %s" % MISSING_BASIC)
    def test_absolute_template_member_is_rejected_before_extraction(self):
        template = os.path.join(self.base, "template.cpio.gz")
        make_template(template, extra=[
            regular("/etc/escape", b"must not be written")])
        out = os.path.join(self.base, "out")

        result = self._run(out, template, self._modules("modules-ok"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe template path", result.stderr)
        self.assertIn("/etc/escape", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "initramfs.cpio.gz")))
        self.assertFalse(os.path.exists(
            os.path.join(out, "work", "stage", "etc", "escape")))

    @unittest.skipIf(MISSING_BASIC, "missing tools: %s" % MISSING_BASIC)
    def test_compressed_and_missing_modules_are_rejected(self):
        template = os.path.join(self.base, "template.cpio.gz")
        make_template(template)

        compressed_out = os.path.join(self.base, "out-compressed")
        result = self._run(
            compressed_out, template,
            self._modules("modules-compressed", compressed="kernel/bad.ko.xz"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compressed modules", result.stderr)
        self.assertIn("bad.ko.xz", result.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(compressed_out, "initramfs.cpio.gz")))

        missing_out = os.path.join(self.base, "out-missing")
        result = self._run(
            missing_out, template,
            self._modules("modules-missing",
                          omit=("kernel/drivers/input/touchscreen/"
                                "nt36536_ts.ko",)))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required module", result.stderr)
        self.assertIn("nt36536_ts.ko", result.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(missing_out, "initramfs.cpio.gz")))

    @unittest.skipIf(MISSING_ALL, "missing tools: %s" % MISSING_ALL)
    def test_build_replaces_only_modules_and_preserves_template(self):
        template = os.path.join(self.base, "template.cpio.gz")
        make_template(template)
        module_dir = self._modules("modules-ok")
        out = os.path.join(self.base, "out")

        result = self._run(out, template, module_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        gz_path = os.path.join(out, "initramfs.cpio.gz")
        artifact_path = os.path.join(out, "artifact.json")
        self.assertTrue(os.path.isfile(gz_path))
        self.assertTrue(os.path.isfile(artifact_path))

        with open(artifact_path, encoding="utf-8") as handle:
            artifact = json.load(handle)

        self.assertEqual(artifact["kernel_release"], KVER)
        self.assertIs(artifact["hardware_validated"], False)
        self.assertIn("TF", artifact["module_policy"])
        self.assertTrue(artifact["template"]["preserved_verified"])
        self.assertEqual(artifact["modules"]["ko_count"], 3)
        self.assertEqual(artifact["modules"]["depmod"], "ok")
        self.assertEqual(artifact["modules"]["squashfs_compression"], "zstd")
        self.assertIn("vmlinuz", artifact["modules"]["excluded_top_level"])

        with open(gz_path, "rb") as handle:
            blob = handle.read()
        self.assertEqual(blob[:2], b"\x1f\x8b")
        self.assertEqual(blob[4:8], b"\x00\x00\x00\x00")
        self.assertEqual(artifact["initramfs"]["sha256"],
                         hashlib.sha256(blob).hexdigest())

        members = parse_cpio(gzip.decompress(blob))
        by_name = {member["name"]: member for member in members}
        self.assertEqual([member["name"] for member in members],
                         sorted(by_name, key=os.fsencode))
        self.assertTrue(all(member["uid"] == 0 and member["gid"] == 0
                            for member in members))

        self.assertEqual(by_name["init"]["data"], INIT)
        self.assertEqual(by_name["init"]["mode"], 0o755)
        self.assertEqual(by_name["armada-root-resolve"]["data"], RESOLVER)
        self.assertEqual(by_name["bin/busybox"]["data"], BUSYBOX)
        self.assertEqual(by_name["etc/root-policy"]["data"], POLICY)
        self.assertEqual(by_name["usr/lib/firmware/fixture.bin"]["data"],
                         FIRMWARE)
        self.assertEqual(by_name["usr/lib/firmware/fixture.bin"]["mode"],
                         0o644)
        self.assertEqual(by_name["usr/share/fonts/fixture.psf"]["data"], FONT)

        squashfs_name = "lib/modules/%s.sqfs" % KVER
        self.assertIn(squashfs_name, by_name)
        squashfs = by_name[squashfs_name]
        self.assertEqual(squashfs["data"][:4], b"hsqs")
        self.assertEqual(artifact["squashfs"]["sha256"],
                         hashlib.sha256(squashfs["data"]).hexdigest())
        module_files = [name for name, member in by_name.items()
                        if name.startswith("lib/modules/")
                        and member["kind"] == stat.S_IFREG]
        self.assertEqual(module_files, [squashfs_name])
        self.assertNotIn("lib/modules/6.1.0-old/kernel/old.ko", by_name)
        self.assertFalse(any(name.endswith((".ko", ".ko.xz", ".ko.zst",
                                            ".ko.gz"))
                             for name in by_name))
        self.assertNotIn("vmlinuz", by_name)

    def test_early_only_closure_keeps_dependencies_and_drops_others(self):
        script = load_build_module()
        module_dir = self._closure_modules()
        dep_path = os.path.join(module_dir, "modules.dep")
        modules = script.scan_modules(module_dir, "fixture module tree")

        selected = script.resolve_early_modules(dep_path, modules)

        expected = {
            "kernel/drivers/soc/qcom/qcom_q6v5_pas.ko",
            "kernel/drivers/input/touchscreen/nt36536_ts.ko",
            "kernel/drivers/soc/qcom/qcom_common.ko",
            "kernel/drivers/soc/qcom/qcom_q6v5.ko",
            "kernel/drivers/soc/qcom/qcom_sysmon.ko",
            "kernel/drivers/soc/qcom/qcom_pil_info.ko",
            "kernel/drivers/firmware/qcom_scm_helper.ko",
        }
        self.assertEqual(selected, expected)
        self.assertNotIn("kernel/drivers/misc/unrelated.ko", selected)

        script.remove_unselected_modules(module_dir, modules, selected)
        script.filter_modules_order(module_dir, selected)

        remaining = script.scan_modules(module_dir, "pruned fixture tree")
        self.assertEqual(sorted(remaining), sorted(expected))
        self.assertFalse(os.path.exists(
            os.path.join(module_dir, "kernel", "drivers", "misc",
                         "unrelated.ko")))

        with open(os.path.join(module_dir, "modules.order"),
                  encoding="utf-8") as handle:
            order = [line for line in handle.read().splitlines() if line]
        self.assertEqual(sorted(order), sorted(expected))
        self.assertEqual(order[:2], [
            "kernel/drivers/soc/qcom/qcom_q6v5_pas.ko",
            "kernel/drivers/input/touchscreen/nt36536_ts.ko",
        ])
        self.assertNotIn("kernel/drivers/misc/unrelated.ko", order)


if __name__ == "__main__":
    unittest.main()
