#!/usr/bin/env python3
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system_files/usr/lib/armada"))
import armada_os as maintenance
REAL_SNAPSHOT = maintenance.snapshot


def fixture():
    def entry(version, checksum):
        return {"version": version, "image": "ghcr.io/mydanyi/armadaos-freedom:stable", "checksum": checksum,
                "serial": 0, "stateroot": "default", "incompatible": False}
    return {"booted": entry("current", "a" * 64), "rollback": entry("previous", "b" * 64),
            "staged": None, "rollback_queued": False, "channel": "stable",
            "repository": "ghcr.io/mydanyi/armadaos-freedom", "channels": ["stable", "beta", "preview"]}


def staged_entry(image, version="queued"):
    return {"version": version, "image": image, "checksum": "c" * 64, "serial": 0,
            "stateroot": "default", "incompatible": False}


def raw_state(staged=True):
    def entry(version, checksum):
        return {"image": {"version": version, "image": {"image": "ghcr.io/mydanyi/armadaos-freedom:stable"}},
                "ostree": {"checksum": checksum, "deploySerial": 0, "stateroot": "default"}}
    return {"status": {"booted": entry("current", "a" * 64), "rollback": entry("previous", "b" * 64),
                       "staged": entry("queued", "c" * 64) if staged else None, "rollbackQueued": False}}


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.state = fixture()
        patch.object(maintenance, "snapshot", side_effect=self.snapshot).start()
        self.command = patch.object(maintenance, "command", side_effect=self.fake_command).start()
        self.run = patch.object(maintenance.subprocess, "run", side_effect=self.fake_run).start()

    def snapshot(self):
        return copy.deepcopy(self.state)

    def fake_command(self, arguments, **kwargs):
        if arguments[0] == maintenance.SELECT:
            self.state["channel"] = arguments[1]
        return ""

    def fake_run(self, arguments, **kwargs):
        if arguments == ["/usr/bin/bootc", "rollback"]:
            self.state["rollback_queued"] = not self.state["rollback_queued"]
            self.state["staged"] = None
        elif arguments == [maintenance.UPDATE]:
            # The staged image follows the currently desired channel, not the
            # booted one: selecting beta then updating stages beta, and the
            # preview picker maps to the testing tag.
            channel = "testing" if self.state["channel"] == "preview" else self.state["channel"]
            self.state["staged"] = staged_entry(f"ghcr.io/mydanyi/armadaos-freedom:{channel}",
                                                version="next")
        return subprocess.CompletedProcess(arguments, 0)

    def perform(self, operation):
        checksum = (self.state["rollback"] or {}).get("checksum") if operation == "rollback" else None
        return maintenance.execute(operation, checksum)

    def test_versions_available_without_a_network_check(self):
        state = maintenance.status()
        self.assertEqual(state["booted"]["version"], "current")
        self.assertEqual(state["rollback"]["version"], "previous")
        self.command.assert_not_called()

    def test_bootc_deployment_metadata(self):
        self.command.side_effect = [json.dumps(raw_state()), "ghcr.io/mydanyi/armadaos-freedom testing\n"]
        state = REAL_SNAPSHOT()
        self.assertEqual(state["booted"]["version"], "current")
        self.assertEqual(state["rollback"]["version"], "previous")
        self.assertEqual(state["staged"]["version"], "queued")
        self.assertEqual(state["repository"], "ghcr.io/mydanyi/armadaos-freedom")
        self.assertEqual(state["channels"], ["stable", "beta", "preview"])
        self.assertEqual(state["channel"], "preview")
        self.assertEqual(self.command.call_count, 2)

    def test_snapshot_offers_no_channels_for_old_or_unknown_targets(self):
        for target in ("ghcr.io/armada-os/armada stable\n",
                       "ghcr.io/virtudude/armada beta\n",
                       "example.invalid/whatever stable\n"):
            with self.subTest(target=target):
                self.command.side_effect = [json.dumps(raw_state(staged=False)), target]
                self.assertEqual(REAL_SNAPSHOT()["channels"], [])

    def test_snapshot_target_resolution_failure_leaves_state_empty(self):
        self.command.side_effect = [json.dumps(raw_state(staged=False)), RuntimeError("resolver failed")]
        state = REAL_SNAPSHOT()
        self.assertEqual(state["channel"], "")
        self.assertEqual(state["repository"], "")
        self.assertEqual(state["channels"], [])

    def test_no_previous_version_refuses_rollback(self):
        self.state["rollback"] = None
        with self.assertRaisesRegex(RuntimeError, "No compatible previous"):
            self.perform("rollback")
        self.run.assert_not_called()

    def test_stale_confirmation_refuses_changes(self):
        checksum = self.state["rollback"]["checksum"]
        self.state["rollback"]["checksum"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "changed"):
            maintenance.execute("rollback", checksum)
        self.run.assert_not_called()

    def test_rollback_discards_staged_and_is_ready_for_shutdown_sync(self):
        self.state["staged"] = {**self.state["booted"], "version": "queued"}
        self.perform("rollback")
        self.assertEqual([call.args[0] for call in self.run.call_args_list],
                         [["/usr/bin/bootc", "rollback"]])
        self.assertIsNone(self.state["staged"])
        self.assertTrue(maintenance.status()["reboot_ready"])

    def test_already_queued_rollback_is_never_toggled_back(self):
        self.state["rollback_queued"] = True
        self.perform("rollback")
        self.run.assert_not_called()
        self.assertTrue(maintenance.status()["reboot_ready"])

    def test_channel_change_uses_existing_selector_and_invalid_channels_are_rejected(self):
        maintenance.select_channel("beta")
        self.assertEqual(self.state["channel"], "beta")
        self.assertEqual(self.command.call_args.args[0], [maintenance.SELECT, "beta"])
        maintenance.select_channel("preview")
        self.assertEqual(self.command.call_args.args[0], [maintenance.SELECT, "preview"])
        with self.assertRaises(ValueError):
            maintenance.select_channel("example.com/image")
        self.state["channels"] = []
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            maintenance.select_channel("stable")

    def test_update_uses_shared_hook(self):
        self.perform("update")
        self.assertTrue(maintenance.status()["reboot_ready"])
        self.assertEqual(self.run.call_args.args[0], [maintenance.UPDATE])

    def test_update_without_a_new_deployment_reports_result(self):
        self.run.side_effect = lambda arguments, **kwargs: subprocess.CompletedProcess(arguments, 0)
        result = self.perform("update")
        self.assertIn("No new image", result["message"])
        self.assertFalse(maintenance.status()["reboot_ready"])

    def test_restart_uses_live_pending_state(self):
        with self.assertRaisesRegex(RuntimeError, "no pending"):
            maintenance.restart()
        self.state["rollback_queued"] = True
        maintenance.restart()
        self.assertEqual(self.command.call_args.args[0], ["/usr/bin/systemctl", "reboot"])
        self.state["rollback_queued"] = False
        with self.assertRaisesRegex(RuntimeError, "no pending"):
            maintenance.restart()

    def test_reboot_ready_requires_a_migrated_staged_source(self):
        self.state["channel"] = "stable"
        for image in ("ghcr.io/armada-os/armada:stable", "ghcr.io/virtudude/armada:stable",
                      "example.invalid/whatever:stable"):
            with self.subTest(image=image):
                self.state["staged"] = staged_entry(image)
                state = maintenance.status()
                self.assertEqual(state["staged"]["version"], "queued")
                self.assertFalse(state["reboot_ready"])

    def test_reboot_ready_for_staged_freedom_matching_desired_channel(self):
        self.state["channel"] = "stable"
        self.state["staged"] = staged_entry("ghcr.io/mydanyi/armadaos-freedom:stable")
        self.assertTrue(maintenance.status()["reboot_ready"])

    def test_reboot_ready_maps_preview_desired_channel_to_testing_staged(self):
        self.state["channel"] = "preview"
        self.state["staged"] = staged_entry("ghcr.io/mydanyi/armadaos-freedom:testing")
        self.assertTrue(maintenance.status()["reboot_ready"])

    def test_reboot_ready_rejects_a_same_freedom_wrong_channel_source(self):
        self.state["channel"] = "stable"
        for image in ("ghcr.io/mydanyi/armadaos-freedom:beta",
                      "ghcr.io/mydanyi/armadaos-freedom:testing",
                      "ghcr.io/mydanyi/armadaos-freedom:preview"):
            with self.subTest(image=image):
                self.state["staged"] = staged_entry(image)
                state = maintenance.status()
                self.assertEqual(state["staged"]["version"], "queued")
                self.assertEqual(state["staged"]["image"], image)
                self.assertFalse(state["reboot_ready"])

    def test_reboot_ready_accepts_a_signed_docker_freedom_source(self):
        self.state["channel"] = "stable"
        image = "ostree-image-signed:docker://ghcr.io/mydanyi/armadaos-freedom:stable"
        self.state["staged"] = staged_entry(image)
        state = maintenance.status()
        self.assertEqual(state["staged"]["image"], image)
        self.assertTrue(state["reboot_ready"])

    def test_queued_rollback_is_ready_for_any_staged_source(self):
        self.state["rollback_queued"] = True
        for image in ("ghcr.io/armada-os/armada:stable", "example.invalid/whatever:stable"):
            with self.subTest(image=image):
                self.state["staged"] = staged_entry(image)
                self.assertTrue(maintenance.status()["reboot_ready"])

    def test_restart_rejects_an_old_upstream_staged_source(self):
        self.state["staged"] = staged_entry("ghcr.io/armada-os/armada:stable")
        with self.assertRaisesRegex(RuntimeError, "no pending"):
            maintenance.restart()

    def test_update_rejects_an_old_upstream_pending_source(self):
        def fake_run(arguments, **kwargs):
            if arguments == [maintenance.UPDATE]:
                self.state["staged"] = staged_entry("ghcr.io/armada-os/armada:stable", version="next")
            return subprocess.CompletedProcess(arguments, 0)
        self.run.side_effect = fake_run
        with self.assertRaisesRegex(RuntimeError, "(?i)unsupported|pending"):
            self.perform("update")

    def test_queued_rollback_allows_explicit_update_and_channel_change(self):
        self.state["rollback_queued"] = True
        maintenance.select_channel("beta")
        self.assertEqual(self.state["channel"], "beta")
        self.perform("update")
        self.assertEqual(self.run.call_args.args[0], [maintenance.UPDATE])

    def test_command_failure_is_reported_to_the_caller(self):
        self.run.side_effect = subprocess.CalledProcessError(7, maintenance.UPDATE)
        with self.assertRaises(subprocess.CalledProcessError):
            self.perform("update")

    def test_rollback_verifies_the_selected_deployment(self):
        self.run.side_effect = lambda *args, **kwargs: None
        with self.assertRaisesRegex(RuntimeError, "not selected"):
            self.perform("rollback")


if __name__ == "__main__":
    unittest.main()
