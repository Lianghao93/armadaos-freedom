#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FREEDOM = "ghcr.io/mydanyi/armadaos-freedom"
UPSTREAM = ("ghcr.io/armada-os/armada", "ghcr.io/virtudude/armada")


def policy_document(docker, default=None):
    """Containers-policy document with the given docker transport map."""
    return {"default": [{"type": "insecureAcceptAnything"}] if default is None else default,
            "transports": {"docker": docker}}


class UpdateHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        bootc = self.bin / "bootc"
        bootc.write_text('#!/bin/sh\n[ "${BAD_BOOTC:-0}" = 0 ] || exit 1\ncat "$BOOTC_STATE"\n')
        bootc.chmod(0o755)
        (self.base / "bootc.json").write_text(json.dumps({"status": {"booted": {"image": {}}, "rollbackQueued": False}}))
        library = self.base / "update-lib"
        # Keep the real armada_update_target resolver; only the external
        # boundaries (booted/local state, storage, remote metadata, bootc) are
        # stubbed. The remote stub records repo+tag so wrong sources are visible.
        library.write_text((ROOT / "system_files/usr/lib/armada/update-lib").read_text() + '''
ARMADA_CHANNEL_STATE="$TEST_CHANNEL_STATE"
ARMADA_UPDATE_POLICY="$TEST_POLICY"
armada_update_log() { echo "$*" >&2; }
armada_booted_ref() { echo "${BOOTED_REF-ostree-image-signed:docker://ghcr.io/mydanyi/armadaos-freedom:stable}"; }
armada_update_reserve_bytes() { echo 0; }
armada_available_bytes() { echo "${AVAILABLE_BYTES:-1000}"; }
armada_local_state() { echo "sha256:booted ${STAGED_DIGEST:--} ${STAGED_REF:--} staged-version"; }
armada_staged_ref() { echo "${STAGED_REF:-}"; }
armada_remote_info() {
    echo "$*" >> "$REMOTE_CALLS"
    [[ "${NETWORK_FAILURE:-0}" = 0 ]] || return 1
    echo "100 ${REMOTE_DIGEST:-sha256:new} new-version"
}
armada_bootc_with_progress() { echo "$*" >> "$BOOTC_CALLS"; echo '70%'; }
''')
        self.hook = self.base / "update"
        self.hook.write_text((ROOT / "system_files/usr/libexec/armada/armada-update").read_text().replace(
            "source /usr/lib/armada/update-lib", f"source {library}"))
        self.hook.chmod(0o755)
        self.login = self.base / "loginusers.vdf"
        self.wrapper = self.base / "steamos-update"
        self.wrapper.write_text((ROOT / "system_files/usr/bin/steamos-update").read_text().replace(
            "/var/home/armada/.local/share/Steam/config/loginusers.vdf", str(self.login)).replace(
            "/usr/libexec/armada/armada-update", str(self.hook)))
        self.wrapper.chmod(0o755)
        self.selector = self.base / "select-branch"
        self.selector.write_text((ROOT / "system_files/usr/libexec/armada/armada-select-branch").read_text().replace(
            "source /usr/lib/armada/update-lib", f"source {library}"))
        # The OTA readiness gate requires the containers policy to pin the
        # Freedom image to a sigstoreSigned entry whose key files exist on disk.
        # The path is injected through TEST_POLICY after sourcing the library,
        # like the channel fixture. This is a filesystem check, not a
        # cryptographic one: the dummy key is never verified.
        self.policy_key = self.base / "armada.pub"
        self.policy_key.write_text("test-only public key placeholder\n")
        self.policy = self.base / "policy.json"
        self.policy.write_text(json.dumps(policy_document(
            {FREEDOM: [{"type": "sigstoreSigned", "keyPaths": [str(self.policy_key)]}]})))
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "BOOTC_STATE": str(self.base / "bootc.json"), "BOOTC_CALLS": str(self.base / "calls"),
                    "REMOTE_CALLS": str(self.base / "remote"),
                    "TEST_CHANNEL_STATE": str(self.base / "channel"),
                    "TEST_POLICY": str(self.policy)}

    def run_hook(self, *arguments, **kwargs):
        return subprocess.run(["bash", str(self.hook), *arguments], env=self.env,
                              text=True, capture_output=True, timeout=5, **kwargs)

    def run_wrapper(self, *arguments):
        return subprocess.run([str(self.wrapper), *arguments], env=self.env,
                              text=True, capture_output=True, timeout=5)

    def use_policy(self, document):
        path = self.base / "test-policy.json"
        path.write_text(document if isinstance(document, str) else json.dumps(document))
        self.env["TEST_POLICY"] = str(path)
        return path

    def test_direct_update_and_check_do_not_require_steam_login(self):
        result = self.run_hook("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "new-version")
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "calls").read_text().strip(), "bootc upgrade")

    def test_steam_suppresses_check_and_apply_without_saved_account(self):
        self.hook.write_text('#!/bin/sh\necho "Updater must not run"\nexit 99\n')
        for contents in (None, '"users" {}\n'):
            if contents is not None:
                self.login.write_text(contents)
            for arguments in ((), ("--enable-duplicate-detection", "check")):
                with self.subTest(contents=contents, arguments=arguments):
                    result = self.run_wrapper(*arguments)
                    self.assertEqual(result.returncode, 7)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "")

    def test_steam_capability_probe_works_before_login(self):
        self.hook.write_text('#!/bin/sh\nexit 99\n')
        for arguments in (("--supports-duplicate-detection",), ("check", "--supports-duplicate-detection")):
            result = self.run_wrapper(*arguments)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_steam_check_and_apply_work_with_saved_account(self):
        self.login.write_text('"users" { "123" { "AccountName" "test" } }\n')
        result = self.run_wrapper("--enable-duplicate-detection", "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "new-version")
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "70%\n")
        self.assertEqual((self.base / "calls").read_text().strip(), "bootc upgrade")

    def test_steam_wrapper_preserves_arguments_output_and_exit_status(self):
        self.login.write_text('"AccountName" "test"\n')
        self.hook.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                             'print(json.dumps(sys.argv[1:]))\nprint("diagnostic", file=sys.stderr)\n'
                             'sys.exit(int(os.environ["UPDATE_EXIT"]))\n')
        arguments = ("--enable-duplicate-detection", "check", "argument with spaces", "*")
        for code in (0, 1, 7):
            self.env["UPDATE_EXIT"] = str(code)
            result = self.run_wrapper(*arguments)
            self.assertEqual(result.returncode, code)
            self.assertEqual(json.loads(result.stdout), list(arguments))
            self.assertEqual(result.stderr, "diagnostic\n")

    def test_check_retains_steam_version_and_exit_code_contract(self):
        self.env["REMOTE_DIGEST"] = "sha256:booted"
        self.assertEqual(self.run_hook("check").returncode, 7)
        self.env.update(REMOTE_DIGEST="sha256:staged", STAGED_DIGEST="sha256:staged", STAGED_REF=f"{FREEDOM}:stable")
        self.assertEqual(self.run_hook("check").stdout.strip(), "staged-version")
        self.env["NETWORK_FAILURE"] = "1"
        result = self.run_hook("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.returncode, 7)

    def test_storage_failure_does_not_apply(self):
        self.env["AVAILABLE_BYTES"] = "1"
        self.assertNotEqual(self.run_hook().returncode, 0)
        self.assertFalse((self.base / "calls").exists())

    def test_desktop_apply_keeps_signature_policy(self):
        self.env["BOOTED_REF"] = f"ostree-unverified-registry:{FREEDOM}:stable"
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "calls").read_text().strip(), f"bootc switch --enforce-container-sigpolicy {FREEDOM}:stable")

    def test_queued_rollback_does_not_block_explicit_updates(self):
        (self.base / "bootc.json").write_text(json.dumps({"status": {"booted": {"image": {}}, "rollbackQueued": True}}))
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "calls").read_text().splitlines(), ["bootc upgrade"])

    def test_channel_selection_replaces_state_and_allows_queued_rollback(self):
        (self.base / "bootc.json").write_text(json.dumps({"status": {"booted": {"image": {}}, "rollbackQueued": True}}))
        for channel in ("beta", "testing"):
            result = subprocess.run(["bash", str(self.selector), channel], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.base / "channel").read_text(), channel + "\n")
        self.assertEqual((self.base / "channel").stat().st_mode & 0o777, 0o644)
        self.assertFalse(list(self.base.glob("channel.*")))

    def test_upstream_origins_migrate_to_freedom_without_upgrade(self):
        # Same-tag migration: even though the tag is unchanged, the repository
        # must move from the old upstream to Freedom via bootc switch.
        for origin in UPSTREAM:
            with self.subTest(origin=origin):
                self.env["BOOTED_REF"] = f"ostree-image-signed:docker://{origin}:stable"
                self.env["REMOTE_DIGEST"] = "sha256:booted"
                calls, remote = self.base / "calls", self.base / "remote"
                calls.unlink(missing_ok=True)
                remote.unlink(missing_ok=True)
                result = self.run_hook()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls.read_text().strip(),
                                 f"bootc switch --enforce-container-sigpolicy {FREEDOM}:stable")
                inspected = remote.read_text()
                self.assertIn(f"stable {FREEDOM}", inspected)
                self.assertNotIn(origin, inspected)

    def test_upstream_check_offers_migration_with_equal_digest(self):
        for origin in UPSTREAM:
            with self.subTest(origin=origin):
                self.env["BOOTED_REF"] = f"ostree-image-signed:docker://{origin}:stable"
                self.env["REMOTE_DIGEST"] = "sha256:booted"
                result = self.run_hook("check")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "new-version")

    def test_upstream_staged_image_does_not_mask_migration(self):
        self.env["STAGED_REF"] = "ghcr.io/armada-os/armada:stable"
        self.env["STAGED_DIGEST"] = "sha256:staged"
        self.env["REMOTE_DIGEST"] = "sha256:staged"
        result = self.run_hook("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "new-version")
        calls = self.base / "calls"
        calls.unlink(missing_ok=True)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().strip(),
                         f"bootc switch --enforce-container-sigpolicy {FREEDOM}:stable")

    def test_freedom_staged_returns_staged_version(self):
        self.env.update(STAGED_REF=f"{FREEDOM}:stable", STAGED_DIGEST="sha256:staged",
                        REMOTE_DIGEST="sha256:staged")
        result = self.run_hook("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "staged-version")

    def test_freedom_booted_without_staged_reports_no_update(self):
        self.env["REMOTE_DIGEST"] = "sha256:booted"
        self.assertEqual(self.run_hook("check").returncode, 7)

    def test_unknown_or_broken_origin_contacts_neither_remote_nor_bootc(self):
        broken = ("ostree-image-signed:docker://example.invalid/whatever:stable",
                  "ostree-image-signed:docker://ghcr.io/armada-os/armada:feature-a",
                  "containers-storage:localhost/armada:latest",
                  "")
        for ref in broken:
            for arguments in (("check",), ()):
                with self.subTest(ref=ref, arguments=arguments):
                    self.env["BOOTED_REF"] = ref
                    remote, calls = self.base / "remote", self.base / "calls"
                    remote.unlink(missing_ok=True)
                    calls.unlink(missing_ok=True)
                    result = self.run_hook(*arguments)
                    self.assertEqual(result.returncode, 7)
                    self.assertFalse(remote.exists())
                    self.assertFalse(calls.exists())

    def test_selected_channels_inspect_and_switch_to_freedom(self):
        # Exercise the real selector, then the real hook, on a migrating source.
        self.env["BOOTED_REF"] = "ostree-image-signed:docker://ghcr.io/armada-os/armada:stable"
        for token, channel in (("beta", "beta"), ("preview", "testing")):
            with self.subTest(token=token):
                result = subprocess.run(["bash", str(self.selector), token], env=self.env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.base / "channel").read_text(), channel + "\n")
                remote = self.base / "remote"
                remote.unlink(missing_ok=True)
                self.assertEqual(self.run_hook("check").returncode, 0)
                self.assertIn(f"{channel} {FREEDOM}", remote.read_text())
                calls = self.base / "calls"
                calls.unlink(missing_ok=True)
                self.assertEqual(self.run_hook().returncode, 0)
                self.assertEqual(calls.read_text().strip(),
                                 f"bootc switch --enforce-container-sigpolicy {FREEDOM}:{channel}")

    def assert_refused_without_inspection(self, name):
        for arguments in (("check",), ()):
            with self.subTest(name=name, arguments=arguments):
                remote, calls = self.base / "remote", self.base / "calls"
                remote.unlink(missing_ok=True)
                calls.unlink(missing_ok=True)
                result = self.run_hook(*arguments)
                self.assertEqual(result.returncode, 7)
                self.assertIn("Freedom updates not configured", result.stderr)
                self.assertFalse(remote.exists())
                self.assertFalse(calls.exists())

    def test_unconfigured_freedom_updates_refuse_before_inspection(self):
        missing = self.base / "absent.pub"
        for name, document in {
                "insecure fallback without the exact repository":
                    policy_document({"": [{"type": "insecureAcceptAnything"}]}),
                "exact repository with the wrong policy type":
                    policy_document({FREEDOM: [{"type": "insecureAcceptAnything"}]}),
                "sigstoreSigned without key paths":
                    policy_document({FREEDOM: [{"type": "sigstoreSigned", "keyPaths": []}]}),
                "sigstoreSigned with a missing key file":
                    policy_document({FREEDOM: [{"type": "sigstoreSigned", "keyPaths": [str(missing)]}]}),
                "malformed policy document": "{ not valid json",
        }.items():
            self.use_policy(document)
            self.assert_refused_without_inspection(name)

    def test_exact_policy_with_existing_key_reaches_remote_and_bootc(self):
        key = self.base / "explicit.pub"
        key.write_text("test-only public key placeholder\n")
        self.use_policy(policy_document(
            {FREEDOM: [{"type": "sigstoreSigned", "keyPaths": [str(key)]}]}))
        self.env["BOOTED_REF"] = f"ostree-unverified-registry:{FREEDOM}:stable"
        result = self.run_hook("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "new-version")
        self.assertIn(f"stable {FREEDOM}", (self.base / "remote").read_text())
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "calls").read_text().strip(),
                         f"bootc switch --enforce-container-sigpolicy {FREEDOM}:stable")

    def test_selector_reports_booted_channel_without_state_file(self):
        # With no channel state file the selector calls armada_desired_channel
        # with its argument absent, which must fall back to the booted Freedom
        # channel; an explicit empty argument would not. A signed Freedom:testing
        # origin maps to Steam's preview token.
        self.env["BOOTED_REF"] = f"ostree-image-signed:docker://{FREEDOM}:testing"
        result = subprocess.run(["bash", str(self.selector), "-c"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "preview")

    def test_channel_change_offers_new_version_with_equal_digest(self):
        # Same Freedom repository, different selected channel: even though the
        # remote digest equals the booted digest, moving stable->beta is a new
        # version and apply must switch to the beta channel.
        (self.base / "channel").write_text("beta\n")
        self.env["REMOTE_DIGEST"] = "sha256:booted"
        result = self.run_hook("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "new-version")
        self.assertIn(f"beta {FREEDOM}", (self.base / "remote").read_text())
        calls = self.base / "calls"
        calls.unlink(missing_ok=True)
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.read_text().strip(),
                         f"bootc switch --enforce-container-sigpolicy {FREEDOM}:beta")

    def test_upstream_origin_with_staged_freedom_is_restart_pending(self):
        # The staged image is already the correct Freedom channel, so the update
        # is restart-pending even though the booted origin stays the old upstream
        # until reboot; the migration flag must not force a new-version offer.
        for origin in UPSTREAM:
            with self.subTest(origin=origin):
                self.env["BOOTED_REF"] = f"ostree-image-signed:docker://{origin}:stable"
                self.env.update(STAGED_REF=f"{FREEDOM}:stable", STAGED_DIGEST="sha256:staged",
                                REMOTE_DIGEST="sha256:staged")
                result = self.run_hook("check")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "staged-version")

    def test_malformed_freedom_origin_is_refused_without_inspection(self):
        # A stored beta channel must not make a malformed Freedom origin valid: an
        # empty tag or a trailing '@' digest is not a supported image reference,
        # so neither check nor apply may reach the remote or bootc.
        (self.base / "channel").write_text("beta\n")
        malformed = (
            f"ostree-image-signed:docker://{FREEDOM}:",
            f"ostree-image-signed:docker://{FREEDOM}:stable@",
            f"ostree-image-signed:docker://{FREEDOM}:stable@broken",
        )
        for ref in malformed:
            for arguments in (("check",), ()):
                with self.subTest(ref=ref, arguments=arguments):
                    self.env["BOOTED_REF"] = ref
                    remote, calls = self.base / "remote", self.base / "calls"
                    remote.unlink(missing_ok=True)
                    calls.unlink(missing_ok=True)
                    result = self.run_hook(*arguments)
                    self.assertEqual(result.returncode, 7)
                    self.assertFalse(remote.exists())
                    self.assertFalse(calls.exists())


if __name__ == "__main__":
    unittest.main()
