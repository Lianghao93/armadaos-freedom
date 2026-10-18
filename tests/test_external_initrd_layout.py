#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused layout tests for the optional external-initramfs boot mode.

These tests are pure stdlib and do not run the packer, dtc or any device
tool. They compare the legacy eight-entry table with the new nine-entry
table, check the generated blob config section, and rebuild a manifest
fixture from the documented ABI so the index/payload expectations used by
build_offline_boot.py are exercised without a toolchain.
"""

import importlib.util
import struct
import unittest
from pathlib import Path

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "devices" / "lenovo-tb323fu" / "boot" / "star-chart-build"
               / "build_offline_boot.py")
SPEC = importlib.util.spec_from_file_location("build_offline_boot",
                                              MODULE_PATH)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)

MANIFEST_MAGIC = 0x464D504B
HEADER_FORMAT = "<IHHIII"
ENTRY_FORMAT = "<QQIIQIIIQI"


def manifest_bytes(specs, default=1, timeout=8000, prefix=128):
    records = []
    for index, (name, entry_type, flags, alignment, _role) in enumerate(specs):
        name_bytes = name.encode("utf-8")
        record = struct.pack(
            ENTRY_FORMAT,
            0x1000 * index, 0x80, entry_type, flags, 0, 0, 0, alignment,
            0, len(name_bytes))
        record += name_bytes
        record += b"\x00" * (-len(record) % 4)
        records.append(record)
    header = struct.pack(HEADER_FORMAT, MANIFEST_MAGIC, 0, 1, len(specs),
                         default, timeout)
    header += struct.pack("<QQQQQ", 0, 0, 0, 0, 0)
    return b"\x00" * prefix + header + b"".join(records), prefix


class EntryTableTest(unittest.TestCase):
    def test_legacy_table_is_unchanged(self):
        specs = BOOT.expected_entries(False)
        self.assertIs(specs, BOOT.EXPECTED_ENTRIES)
        self.assertEqual(len(specs), 8)
        self.assertEqual([spec[4] for spec in specs],
                         ["base", "linux", "alias", "alias", "dtb", "dtb",
                          "dtb", "shim"])
        self.assertEqual(specs[7][0], "Attached Shim")
        self.assertEqual(specs[7][1], BOOT.MF_TYPE_SHIM)

    def test_external_table_preserves_prefix_and_moves_shim(self):
        legacy = BOOT.expected_entries(False)
        external = BOOT.expected_entries(True)
        self.assertEqual(len(external), 9)
        self.assertEqual(external[:7], legacy[:7])
        self.assertEqual(external[7][4], "initrd")
        self.assertEqual(external[7][0], BOOT.INITRD_ENTRY_NAME)
        self.assertEqual(external[7][1], BOOT.MF_TYPE_BLOB)
        self.assertEqual(external[7][2], 0)
        self.assertEqual(external[7][3], 16)
        self.assertEqual(external[8], legacy[7])

    def test_linux_to_dtb_references_do_not_move(self):
        for external in (False, True):
            specs = BOOT.expected_entries(external)
            self.assertEqual(specs[1][1], BOOT.MF_TYPE_LINUX)
            self.assertEqual(specs[2][1], BOOT.MF_TYPE_LINUX)
            self.assertEqual(specs[3][1], BOOT.MF_TYPE_LINUX)
            self.assertEqual(specs[4][1], BOOT.MF_TYPE_DTB)
            self.assertEqual(specs[5][1], BOOT.MF_TYPE_DTB)
            self.assertEqual(specs[6][1], BOOT.MF_TYPE_DTB)

    def test_initrd_dt_contract(self):
        self.assertEqual(BOOT.INITRD_ENTRY_INDEX, 7)
        self.assertEqual(BOOT.INITRD_DT_PROPERTY, "kshim,initrd-entry")
        self.assertEqual(BOOT.STALE_INITRD_PROPERTIES,
                         ("linux,initrd-start", "linux,initrd-end"))
        self.assertIn("linux,initrd-start", BOOT.STALE_INITRD_PROPERTIES)
        self.assertIn("linux,initrd-end", BOOT.STALE_INITRD_PROPERTIES)


class ConfigSectionTest(unittest.TestCase):
    def test_blob_section_uses_packer_type_and_alignment(self):
        lines = BOOT.external_image_section("/in/initrd.img")
        self.assertEqual(lines[0], "[Image-07]")
        self.assertIn("Name=" + BOOT.INITRD_ENTRY_NAME, lines)
        self.assertIn("Path=/in/initrd.img", lines)
        self.assertIn("Type=Blob", lines)
        self.assertNotIn("Type=BlobPath", lines)
        self.assertIn("BaseImage=false", lines)
        self.assertIn("Align=16", lines)
        self.assertNotIn("EntryOffset=0x0", lines)
        self.assertNotIn("PayloadFrom=Image-01", lines)
        self.assertEqual(lines[-1], "")


class ManifestFixtureTest(unittest.TestCase):
    def test_legacy_and_external_counts_and_indexes(self):
        legacy_raw, legacy_offset = manifest_bytes(
            BOOT.expected_entries(False))
        legacy = BOOT.parse_manifest(legacy_raw, legacy_offset)
        self.assertEqual(legacy["count"], 8)
        self.assertEqual(legacy["entries"][7]["type"], BOOT.MF_TYPE_SHIM)
        self.assertEqual(legacy["end"], len(legacy_raw))

        external_raw, external_offset = manifest_bytes(
            BOOT.expected_entries(True))
        external = BOOT.parse_manifest(external_raw, external_offset)
        self.assertEqual(external["count"], 9)
        blob = external["entries"][7]
        self.assertEqual(blob["type"], BOOT.MF_TYPE_BLOB)
        self.assertEqual(blob["flags"], 0)
        self.assertEqual(blob["alignment"], 16)
        self.assertEqual(blob["entry_offset"], 0)
        self.assertEqual(blob["dtb_index"], 0)
        self.assertEqual(blob["load_address"], 0)
        self.assertEqual(blob["copy_size_max"], 0)
        self.assertEqual(external["entries"][8]["type"], BOOT.MF_TYPE_SHIM)
        self.assertEqual(external["entries"][0]["type"], BOOT.MF_TYPE_LINUX)

    def test_manifest_end_matches_entry_records(self):
        specs = BOOT.expected_entries(True)
        raw, offset = manifest_bytes(specs)
        manifest = BOOT.parse_manifest(raw, offset)
        self.assertEqual(manifest["end"], len(raw))
        self.assertEqual(manifest["default"], 1)
        self.assertEqual(manifest["timeout"], 8000)


class BlobEntryTest(unittest.TestCase):
    def entry(self, **overrides):
        entry = {
            "offset": 0x10000, "size": 0x80, "type": BOOT.MF_TYPE_BLOB,
            "flags": 0, "load_address": 0, "entry_offset": 0,
            "dtb_index": 0, "alignment": 16, "copy_size_max": 0,
        }
        entry.update(overrides)
        return entry

    def test_accepts_exact_shared_blob(self):
        blob = b"\x1f\x8b" + b"\x00" * 0x7e
        self.assertEqual(BOOT.blob_entry_errors(self.entry(), blob), [])
        raw = b"\x00" * 0x10000 + blob
        self.assertEqual(BOOT.payload(raw, self.entry()), blob)

    def test_rejects_runtime_incompatible_entries(self):
        blob = b"\x01" * 0x80
        cases = {
            "type": {"type": BOOT.MF_TYPE_LINUX},
            "flags": {"flags": BOOT.FLAG_EXEC},
            "alignment": {"alignment": 2},
            "alignment_old": {"alignment": 3},
            "entry_offset": {"entry_offset": 4},
            "dtb_index": {"dtb_index": 4},
            "load_address": {"load_address": 0x40000000},
            "copy_size_max": {"copy_size_max": 0x1000},
        }
        for label, override in cases.items():
            errors = BOOT.blob_entry_errors(self.entry(**override), blob)
            self.assertTrue(errors, label)
        short = BOOT.blob_entry_errors(self.entry(size=0x40), blob)
        self.assertTrue(any(err.startswith("size=") for err in short))
        empty = BOOT.blob_entry_errors(self.entry(size=0), b"")
        self.assertIn("empty initrd", empty)

    def test_payload_is_byte_exact(self):
        blob = b"initrd-bytes"
        raw = b"\xff" * 4 + blob + b"\x00" * 8
        entry = self.entry(offset=4, size=len(blob))
        self.assertEqual(BOOT.payload(raw, entry), blob)
        entry["size"] += 1
        self.assertNotEqual(BOOT.payload(raw, entry), blob)


class DtDiffFilterTest(unittest.TestCase):
    def test_filters_only_initrd_properties(self):
        source = [
            "/dts-v1/;",
            "chosen {",
            "  linux,initrd-start = <0x1000>;",
            "  linux,initrd-end = <0x2000>;",
            "  bootargs = \"baldur.end=hold\";",
            "};",
        ]
        derived = [
            "/dts-v1/;",
            "chosen {",
            "  kshim,initrd-entry = <0x7>;",
            "  bootargs = \"baldur.end=hold\";",
            "};",
        ]
        self.assertEqual(BOOT.without_initrd_properties(source),
                         BOOT.without_initrd_properties(derived))
        derived.append("  unrelated = <0x1>;")
        self.assertNotEqual(BOOT.without_initrd_properties(source),
                            BOOT.without_initrd_properties(derived))


if __name__ == "__main__":
    unittest.main()
