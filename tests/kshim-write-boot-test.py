#!/usr/bin/python3
"""Unit tests for kshim-write-boot.py transaction behaviour.

Function-level tests only: real small temporary files stand in for the block
partition, and the block-device preflight (root, blkid, BLKGETSIZE64) is
replaced at function level. The real preflight helpers are exercised by
kshim-write-loop-test.py on a loop device. No device, root, or network is
touched here.
"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "system_files/usr/libexec/armada/kshim-write-boot.py"

spec = importlib.util.spec_from_file_location("kshim_write_boot", SCRIPT)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)

SMALL = 4096
MAGIC = b"ANDROID!"
PARTUUID = "11111111-2222-3333-4444-555555555555"
ROOT_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
BOOT_UUID = "bbbbbbbb-1111-2222-3333-444444444444"
STOCK = "9" * 64
COMMIT = "c" * 64

OLD = MAGIC + b"O" * (SMALL - len(MAGIC))
CANDIDATE = MAGIC + b"C" * (SMALL - len(MAGIC))
FALLBACK = MAGIC + b"F" * (SMALL - len(MAGIC))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def report_for(image_sha, stock=STOCK, root=ROOT_UUID, boot=BOOT_UUID,
               layout="installed", offline=True, checks_ok=True,
               final_length=SMALL):
    return {
        "layout_mode": layout,
        "offline_verified": offline,
        "checks": [
            {"check": "installed:result-file", "ok": True},
            {"check": "installed:result-json", "ok": checks_ok},
        ],
        "inputs": {"stock_boot": {"path": "/historical/stock-boot.img",
                                  "length": SMALL, "sha256": stock}},
        "outputs": {
            "final_boot": {"path": "/historical/candidate/boot.img",
                           "length": final_length, "sha256": image_sha},
        },
        "installed_metadata": {
            "deployment_checksum": COMMIT,
            "filesystem_uuids": {"root": root, "boot": boot},
            "options": "root=UUID=%s boot=UUID=%s rw quiet" % (root, boot),
        },
    }


def write_json(path, document):
    Path(path).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def make_fixture(base):
    base = Path(base)
    base.mkdir(parents=True)
    paths = {
        "target": base / "target.img",
        "candidate": base / "candidate.img",
        "fallback": base / "fallback.img",
        "candidate_report": base / "candidate.json",
        "fallback_report": base / "fallback.json",
        "transaction": base / "tx",
    }
    paths["target"].write_bytes(OLD)
    paths["candidate"].write_bytes(CANDIDATE)
    paths["fallback"].write_bytes(FALLBACK)
    write_json(paths["candidate_report"], report_for(sha(CANDIDATE)))
    write_json(paths["fallback_report"], report_for(sha(FALLBACK)))
    return paths


class WriteBootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = make_fixture(self.root / "fixture")
        self.patch = mock.patch.object
        self.patch(writer, "BOOT_BYTES", SMALL).start()
        self.addCleanup(mock.patch.stopall)
        self.patch(writer, "require_root", lambda: None).start()
        self.patch(writer, "verify_locked", lambda fd, identity: None).start()
        self.patch(writer, "flush_block_cache", lambda fd: None).start()
        self.patch(writer, "probe_target",
                   lambda path, partuuid: {
                       "path": str(path), "partuuid": partuuid, "rdev": 0,
                       "major": 7, "minor": 0, "size": SMALL}).start()

    def invoke(self, fixture=None, transaction=None, **overrides):
        fixture = fixture or self.fx
        values = {
            "image": fixture["candidate"],
            "report": fixture["candidate_report"],
            "fallback_image": fixture["fallback"],
            "fallback_report": fixture["fallback_report"],
            "target": fixture["target"],
            "partuuid": PARTUUID,
            "expected-current-sha256": sha(OLD),
        }
        if "expected_current_sha256" in overrides:
            overrides["expected-current-sha256"] = overrides.pop("expected_current_sha256")
        values.update(overrides)
        argv = [
            "--image", str(values["image"]),
            "--report", str(values["report"]),
            "--fallback-image", str(values["fallback_image"]),
            "--fallback-report", str(values["fallback_report"]),
            "--target", str(values["target"]),
            "--partuuid", values["partuuid"],
            "--expected-current-sha256", values["expected-current-sha256"],
            "--transaction-dir", str(transaction or fixture["transaction"]),
        ]
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = writer.main(argv)
        return code, out.getvalue(), err.getvalue()

    def record(self, fixture=None):
        fixture = fixture or self.fx
        return json.loads((Path(fixture["transaction"]) / "transaction.json")
                          .read_text(encoding="utf-8"))

    def test_success_writes_candidate_and_keeps_backup(self):
        reports = (self.fx["candidate_report"].read_bytes(),
                   self.fx["fallback_report"].read_bytes())
        code, out, err = self.invoke()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fx["target"].read_bytes(), CANDIDATE)
        self.assertEqual((Path(self.fx["transaction"]) / "before.img").read_bytes(), OLD)
        record = self.record()
        self.assertEqual(record["status"], "written")
        self.assertEqual(record["backup"]["sha256"], sha(OLD))
        self.assertEqual(record["current_sha256"], sha(OLD))
        self.assertEqual(record["candidate_sha256"], sha(CANDIDATE))
        self.assertEqual(record["fallback_sha256"], sha(FALLBACK))
        self.assertEqual(record["target"]["partuuid"], PARTUUID)
        self.assertEqual(reports,
                         (self.fx["candidate_report"].read_bytes(),
                          self.fx["fallback_report"].read_bytes()))

    def test_unchanged_skips_backup_and_write(self):
        self.fx["target"].write_bytes(CANDIDATE)
        before = self.fx["target"].stat().st_mtime_ns
        code, out, err = self.invoke(expected_current_sha256=sha(CANDIDATE))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fx["target"].read_bytes(), CANDIDATE)
        self.assertEqual(self.fx["target"].stat().st_mtime_ns, before)
        self.assertFalse((Path(self.fx["transaction"]) / "before.img").exists())
        self.assertEqual(self.record()["status"], "unchanged")

    def test_unchanged_requires_matching_expected_hash(self):
        self.fx["target"].write_bytes(CANDIDATE)
        code, out, err = self.invoke(expected_current_sha256=sha(OLD))
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)
        self.assertEqual(self.fx["target"].read_bytes(), CANDIDATE)
        self.assertFalse((Path(self.fx["transaction"]) / "before.img").exists())
        self.assertFalse((Path(self.fx["transaction"]) / "transaction.json").exists())

    def test_preflight_rejections_happen_before_rw_write(self):
        cases = []

        def size_case(fixture):
            fixture["candidate"].write_bytes(CANDIDATE[:-1])

        def magic_case(fixture):
            fixture["candidate"].write_bytes(b"NOTMAGIC" + CANDIDATE[8:])

        def hash_case(fixture):
            write_json(fixture["candidate_report"], report_for("0" * 64))

        def check_case(fixture):
            write_json(fixture["candidate_report"],
                       report_for(sha(CANDIDATE), checks_ok=False))

        def offline_case(fixture):
            write_json(fixture["candidate_report"],
                       report_for(sha(CANDIDATE), offline=False))

        def layout_case(fixture):
            write_json(fixture["candidate_report"],
                       report_for(sha(CANDIDATE), layout="native"))

        def length_case(fixture):
            write_json(fixture["candidate_report"],
                       report_for(sha(CANDIDATE), final_length=SMALL - 1))

        def stock_case(fixture):
            write_json(fixture["fallback_report"],
                       report_for(sha(FALLBACK), stock="8" * 64))

        def uuid_case(fixture):
            write_json(fixture["fallback_report"],
                       report_for(sha(FALLBACK), boot="dddddddd-1111-2222-3333-444444444444"))

        cases.extend([size_case, magic_case, hash_case, check_case, offline_case,
                      layout_case, length_case, stock_case, uuid_case])
        for index, mutate in enumerate(cases):
            with self.subTest(case=mutate.__name__):
                fixture = make_fixture(self.root / ("case%d" % index))
                mutate(fixture)
                code, out, err = self.invoke(fixture)
                self.assertEqual(code, 1, err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(fixture["target"].read_bytes(), OLD)
                self.assertFalse((Path(fixture["transaction"]) / "before.img").exists())

    def test_partition_field_parser(self):
        good = {"PART_ENTRY_SCHEME": "gpt", "PART_ENTRY_NAME": "boot_a",
                "PART_ENTRY_UUID": PARTUUID}
        writer.parse_partition_fields(good, PARTUUID)
        # Only the PART_ENTRY_* fields are part of the contract: unrelated
        # NAME/UUID/PTTYPE values are ignored, not consulted.
        writer.parse_partition_fields(
            dict(good, PTTYPE="dos", NAME="boot_b",
                 UUID="22222222-2222-3333-4444-555555555555"), PARTUUID)
        for bad in (
                dict(good, PART_ENTRY_SCHEME="dos"),
                dict(good, PART_ENTRY_NAME="boot_b"),
                dict(good, PART_ENTRY_UUID="22222222-2222-3333-4444-555555555555"),
                {"PART_ENTRY_SCHEME": "gpt", "NAME": "boot_a", "UUID": PARTUUID},
                {"PART_ENTRY_SCHEME": "gpt", "PART_ENTRY_NAME": "boot_a",
                 "UUID": PARTUUID},
                {"PART_ENTRY_SCHEME": "gpt", "PART_ENTRY_NAME": "boot_a"},
        ):
            with self.subTest(fields=bad):
                with self.assertRaises(writer.WriteError):
                    writer.parse_partition_fields(bad, PARTUUID)

    def test_bad_uuid_and_expected_shape_rejected_before_rw(self):
        calls = []

        def forbidden(identity):
            calls.append(identity)
            raise AssertionError("read-write open must not happen")

        with mock.patch.object(writer, "open_target_rw", forbidden):
            code, out, err = self.invoke(partuuid="not-a-uuid")
            self.assertEqual(code, 1)
            self.assertEqual(calls, [])
            code, out, err = self.invoke(expected_current_sha256="zz")
            self.assertEqual(code, 1)
            self.assertEqual(calls, [])
        self.assertEqual(self.fx["target"].read_bytes(), OLD)

    def test_current_hash_mismatch_rejected_before_write(self):
        code, out, err = self.invoke(expected_current_sha256="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)
        self.assertEqual(self.fx["target"].read_bytes(), OLD)
        self.assertFalse((Path(self.fx["transaction"]) / "before.img").exists())

    def test_partial_write_restores_explicit_fallback(self):
        real = writer.write_image
        state = {"calls": 0}

        def flaky(fd, image_fd, length):
            if state["calls"] == 0:
                state["calls"] += 1
                os.lseek(image_fd, 0, os.SEEK_SET)
                os.lseek(fd, 0, os.SEEK_SET)
                writer.write_all(fd, os.read(image_fd, length // 2))
                raise writer.WriteError("deliberate partial write")
            return real(fd, image_fd, length)

        with mock.patch.object(writer, "write_image", flaky):
            code, out, err = self.invoke()
        self.assertEqual(code, 2, err)
        self.assertEqual(self.fx["target"].read_bytes(), FALLBACK)
        self.assertEqual((Path(self.fx["transaction"]) / "before.img").read_bytes(), OLD)
        record = self.record()
        self.assertEqual(record["status"], "fallback-restored")
        self.assertIn("deliberate partial write", record["error"])
        self.assertIsNone(record["recovery_error"])

    def test_readback_mismatch_restores_fallback(self):
        real = writer.verify_written
        state = {"calls": 0}

        def flaky(fd, expected):
            state["calls"] += 1
            if state["calls"] == 1:
                raise writer.WriteError("deliberate readback mismatch")
            return real(fd, expected)

        with mock.patch.object(writer, "verify_written", flaky):
            code, out, err = self.invoke()
        self.assertEqual(code, 2, err)
        self.assertEqual(self.fx["target"].read_bytes(), FALLBACK)
        self.assertEqual(self.record()["status"], "fallback-restored")

    def test_recovery_verification_failure_is_nonzero(self):
        real_write = writer.write_image
        state = {"calls": 0}

        def flaky_write(fd, image_fd, length):
            if state["calls"] == 0:
                state["calls"] += 1
                os.lseek(image_fd, 0, os.SEEK_SET)
                os.lseek(fd, 0, os.SEEK_SET)
                writer.write_all(fd, os.read(image_fd, length // 2))
                raise writer.WriteError("deliberate partial write")
            return real_write(fd, image_fd, length)

        def bad_verify(fd, expected):
            raise writer.WriteError("deliberate fallback readback mismatch")

        with mock.patch.object(writer, "write_image", flaky_write), \
                mock.patch.object(writer, "verify_written", bad_verify):
            code, out, err = self.invoke()
        self.assertEqual(code, 3)
        self.assertIn("fallback recovery failed", err)
        record = self.record()
        self.assertEqual(record["status"], "recovery-failed")
        self.assertIn("deliberate fallback readback mismatch", record["recovery_error"])
        self.assertEqual((Path(self.fx["transaction"]) / "before.img").read_bytes(), OLD)

    def test_recovery_failure_reports_concrete_error(self):
        def always_flaky(fd, image_fd, length):
            os.lseek(image_fd, 0, os.SEEK_SET)
            os.lseek(fd, 0, os.SEEK_SET)
            writer.write_all(fd, os.read(image_fd, length // 2))
            raise writer.WriteError("deliberate write failure")

        with mock.patch.object(writer, "write_image", always_flaky):
            code, out, err = self.invoke()
        self.assertEqual(code, 3)
        self.assertIn("fallback recovery failed", err)
        record = self.record()
        self.assertEqual(record["status"], "recovery-failed")
        self.assertIn("deliberate write failure", record["error"])
        self.assertIn("deliberate write failure", record["recovery_error"])
        self.assertEqual((Path(self.fx["transaction"]) / "before.img").read_bytes(), OLD)
        # Recovery overwrote the first half with the fallback before failing.
        target = self.fx["target"].read_bytes()
        self.assertEqual(target[:SMALL // 2], FALLBACK[:SMALL // 2])
        self.assertEqual(target[SMALL // 2:], OLD[SMALL // 2:])

    def test_backup_failure_leaves_target_untouched(self):
        def fail(path, fd, current):
            raise writer.WriteError("deliberate backup failure")

        with mock.patch.object(writer, "backup_target", fail):
            code, out, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("deliberate backup failure", err)
        self.assertEqual(self.fx["target"].read_bytes(), OLD)
        self.assertFalse((Path(self.fx["transaction"]) / "before.img").exists())
        self.assertEqual(self.record()["status"], "backup-failed")

    def test_corrupt_saved_backup_blocks_target_write(self):
        real = writer.verify_backup_file

        def corrupt_then_verify(path, expected):
            data = bytearray(Path(path).read_bytes())
            data[0] ^= 0xFF
            Path(path).write_bytes(bytes(data))
            return real(path, expected)

        with mock.patch.object(writer, "verify_backup_file", corrupt_then_verify):
            code, out, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("saved before.img hash", err)
        self.assertEqual(self.fx["target"].read_bytes(), OLD)
        self.assertEqual(self.record()["status"], "backup-failed")

    def test_prepared_record_exists_before_candidate_write(self):
        real = writer.write_image
        seen = {}

        def observe(fd, image_fd, length):
            path = Path(self.fx["transaction"]) / "transaction.json"
            seen["exists"] = path.exists()
            if seen["exists"]:
                seen["record"] = json.loads(path.read_text(encoding="utf-8"))
            return real(fd, image_fd, length)

        with mock.patch.object(writer, "write_image", observe):
            code, out, err = self.invoke()
        self.assertEqual(code, 0, err)
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["record"]["status"], "prepared")
        self.assertEqual(seen["record"]["backup"]["sha256"], sha(OLD))
        self.assertEqual(self.record()["status"], "written")

    def test_prepared_record_failure_aborts_before_write(self):
        real = writer.write_transaction

        def fail_prepared(path, record):
            if record.get("status") == "prepared":
                raise OSError("deliberate prepared record failure")
            return real(path, record)

        with mock.patch.object(writer, "write_transaction", fail_prepared):
            code, out, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("deliberate prepared record failure", err)
        self.assertEqual(self.fx["target"].read_bytes(), OLD)
        self.assertTrue((Path(self.fx["transaction"]) / "before.img").exists())
        self.assertFalse((Path(self.fx["transaction"]) / "transaction.json").exists())

    def test_written_record_failure_restores_fallback(self):
        real = writer.write_transaction

        def fail_written(path, record):
            if record.get("status") == "written":
                raise OSError("deliberate written record failure")
            return real(path, record)

        with mock.patch.object(writer, "write_transaction", fail_written):
            code, out, err = self.invoke()
        self.assertEqual(code, 2, err)
        self.assertEqual(self.fx["target"].read_bytes(), FALLBACK)
        record = self.record()
        self.assertEqual(record["status"], "fallback-restored")
        self.assertIn("deliberate written record failure", record["error"])
        self.assertEqual((Path(self.fx["transaction"]) / "before.img").read_bytes(), OLD)

    def test_record_failure_does_not_mask_fallback_restore(self):
        real = writer.write_transaction

        def fail_records(path, record):
            if record.get("status") in ("written", "fallback-restored"):
                raise OSError("deliberate record failure")
            return real(path, record)

        with mock.patch.object(writer, "write_transaction", fail_records):
            code, out, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("could not record fallback-restored status", err)
        self.assertEqual(self.fx["target"].read_bytes(), FALLBACK)

    def test_record_failure_does_not_mask_recovery_failure(self):
        def always_flaky(fd, image_fd, length):
            os.lseek(image_fd, 0, os.SEEK_SET)
            os.lseek(fd, 0, os.SEEK_SET)
            writer.write_all(fd, os.read(image_fd, length // 2))
            raise writer.WriteError("deliberate write failure")

        real = writer.write_transaction

        def fail_records(path, record):
            if record.get("status") == "recovery-failed":
                raise OSError("deliberate record failure")
            return real(path, record)

        with mock.patch.object(writer, "write_image", always_flaky), \
                mock.patch.object(writer, "write_transaction", fail_records):
            code, out, err = self.invoke()
        self.assertEqual(code, 3)
        self.assertIn("could not record recovery-failed status", err)
        self.assertIn("fallback recovery failed", err)

    def test_nonempty_transaction_dir_rejected(self):
        transaction = Path(self.fx["transaction"])
        transaction.mkdir()
        marker = transaction / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        code, out, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.fx["target"].read_bytes(), OLD)


if __name__ == "__main__":
    unittest.main()
