#!/usr/bin/env python3
"""Read-only source integration checks for the Steam OOBE lifecycle.

These tests only read tracked build sources and unit files.  They never
execute the build scripts, the fixup helper, the launcher, or a systemd unit;
behavioral coverage belongs to the three fixture suites:

  steam-oobe-test.py            fixup marker synchronization
  steam-oobe-restart-test.py    exit-42 supervision and --steam-oobe-only
  steam-oobe-path-test.sh       installed path/service activation

Run: steam-oobe-image-integration-test.py [--repo REPO_ROOT] [unittest.args...]
"""

import argparse
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]

SESSION_BUILD = pathlib.Path("build_files/30-install-steam-session.sh")
VENDOR_BUILD = pathlib.Path("build_files/40-vendor-system-files.sh")
LAUNCHER = pathlib.Path("system_files/usr/libexec/armada/launch-steam")
GENERATE_BOOTSTRAP = pathlib.Path("packages/steam-bootstrap/generate.sh")

UNIT_DIRS = (
    pathlib.Path("system_files/usr/lib/systemd/system"),
    pathlib.Path("system_files/etc/systemd/system"),
)
SERVICE_UNIT = "armada-steam-oobe.service"
PATH_UNIT = "armada-steam-oobe.path"

MARKER = "/etc/steamos-oobe-image"
HOME_DIR = "/var/home/armada"
HELPER = "/usr/libexec/armada/armada-fixups"

# The three files Steam actually rewrites when OOBE progress or an account
# appears; the path unit must watch each one.
WATCHED_FILES = (
    f"{HOME_DIR}/.steam/registry.vdf",
    f"{HOME_DIR}/.local/share/Steam/registry.vdf",
    f"{HOME_DIR}/.local/share/Steam/config/loginusers.vdf",
)

STABLE_STAMP = ".armada-steam-stable-default-v1"


def logical_lines(text):
    """Join backslash continuations so one shell command is one string."""
    lines = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
        else:
            lines.append(buffer + line)
            buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


class SourceTest(unittest.TestCase):
    def read_source(self, relative):
        path = REPO / relative
        self.assertTrue(path.is_file(), f"missing expected source file: {relative}")
        return path.read_text(encoding="utf-8", errors="replace")

    def unit_text(self, name):
        candidates = [REPO / directory / name for directory in UNIT_DIRS]
        for path in candidates:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        self.fail(
            f"missing unit source {name} (looked under "
            + ", ".join(str(directory) for directory in UNIT_DIRS)
            + ")"
        )

    @staticmethod
    def directives(text, key):
        values = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() == key:
                values.append(value.strip())
        return values

    def assertOrderedBefore(self, text, earlier, later):
        earlier_at = text.find(earlier)
        self.assertNotEqual(earlier_at, -1, f"missing {earlier!r} in source")
        later_at = text.find(later, earlier_at + len(earlier))
        self.assertNotEqual(
            later_at, -1, f"missing {later!r} after {earlier!r}"
        )
        self.assertLess(
            earlier_at,
            later_at,
            f"expected {earlier!r} to appear before {later!r}",
        )


class ImageBuildTests(SourceTest):
    def test_session_build_creates_oobe_image_marker(self):
        text = self.read_source(SESSION_BUILD)
        mentions = [
            line.strip() for line in logical_lines(text) if MARKER in line
        ]
        self.assertTrue(
            mentions, f"{SESSION_BUILD} must reference {MARKER}"
        )
        removals = [
            line for line in mentions if re.match(r"^rm\b", line.strip())
        ]
        self.assertEqual(
            removals,
            [],
            f"{SESSION_BUILD} must not delete {MARKER}; the new image has to "
            "ship the marker so first boot shows the OOBE",
        )
        creations = [
            line
            for line in mentions
            if re.search(r"\b(touch|install)\b", line) or re.search(r">\s*['\"]?" + re.escape(MARKER), line)
        ]
        self.assertTrue(
            creations,
            f"{SESSION_BUILD} must explicitly create {MARKER} "
            "(touch/install/redirection), not leave it absent",
        )

    def test_vendor_build_enables_oobe_path_unit(self):
        text = self.read_source(VENDOR_BUILD)
        enables = [
            line.strip()
            for line in logical_lines(text)
            if re.match(r"^systemctl\b.*\benable\b", line.strip())
        ]
        self.assertTrue(
            any(PATH_UNIT in line for line in enables),
            f"{VENDOR_BUILD} must enable {PATH_UNIT}",
        )

    def test_bootstrap_generator_strips_both_registry_files(self):
        text = self.read_source(GENERATE_BOOTSTRAP)
        removals = [
            line.strip()
            for line in logical_lines(text)
            if re.match(r"^rm\b", line.strip()) and "registry.vdf" in line
        ]
        self.assertTrue(
            any(re.search(r"\$\{STEAM\}/registry\.vdf", line) for line in removals),
            f"{GENERATE_BOOTSTRAP} must strip ${{STEAM}}/registry.vdf from the seed",
        )
        self.assertTrue(
            any(re.search(r"\$\{DOT_STEAM\}/registry\.vdf", line) for line in removals),
            f"{GENERATE_BOOTSTRAP} must strip ${{DOT_STEAM}}/registry.vdf from the seed",
        )


class LauncherMigrationTests(SourceTest):
    def test_launcher_keeps_one_time_stable_channel_migration(self):
        text = self.read_source(LAUNCHER)
        assignment = re.search(
            r"^\s*(\w+)=[^\n]*" + re.escape(STABLE_STAMP), text, re.MULTILINE
        )
        self.assertIsNotNone(
            assignment,
            f"{LAUNCHER} must define the Freedom-only migration stamp "
            f"{STABLE_STAMP}",
        )
        stamp = assignment.group(1)

        self.assertRegex(
            text,
            r"!\s*-e\s+[\"']?\$\{" + re.escape(stamp) + r"\}[\"']?\s*\]",
            f"the migration must stay guarded by the absence of {STABLE_STAMP}",
        )
        self.assertIn(
            "steamdeck_publicbeta",
            text,
            f"{LAUNCHER} must still recognise the old steamdeck_publicbeta value",
        )
        self.assertRegex(
            text,
            r"printf\s+'%s\\n'\s+steamdeck_stable\s*>",
            f"{LAUNCHER} must rewrite the old beta marker to steamdeck_stable",
        )
        self.assertRegex(
            text,
            r"touch\s+(?:--\s+)?[\"']?\$\{" + re.escape(stamp) + r"\}",
            f"{LAUNCHER} must record the migration in {STABLE_STAMP}",
        )
        self.assertOrderedBefore(text, f"! -e \"${{{stamp}}}\"", "steamdeck_stable")
        self.assertOrderedBefore(
            text, "steamdeck_stable", f"touch -- \"${{{stamp}}}\""
        )


class UnitSourceTests(SourceTest):
    def test_service_disables_start_limit_and_runs_oobe_only(self):
        text = self.unit_text(SERVICE_UNIT)
        intervals = self.directives(text, "StartLimitIntervalSec")
        self.assertTrue(
            intervals, f"{SERVICE_UNIT} must set StartLimitIntervalSec=0"
        )
        self.assertEqual(
            [value for value in intervals if value != "0"],
            [],
            f"{SERVICE_UNIT} must never rate-limit the oneshot (got {intervals})",
        )
        exec_start = [
            value
            for value in self.directives(text, "ExecStart")
            if HELPER in value
        ]
        self.assertTrue(
            exec_start, f"{SERVICE_UNIT} must exec {HELPER}"
        )
        self.assertTrue(
            all("--steam-oobe-only" in value for value in exec_start),
            f"{SERVICE_UNIT} must invoke the helper with --steam-oobe-only",
        )

    def test_path_watches_all_three_steam_state_files(self):
        text = self.unit_text(PATH_UNIT)
        watched = set()
        for key in ("PathChanged", "PathModified"):
            watched.update(self.directives(text, key))
        missing = [path for path in WATCHED_FILES if path not in watched]
        self.assertEqual(
            missing,
            [],
            f"{PATH_UNIT} must watch every file Steam rewrites (missing: {missing})",
        )
        units = self.directives(text, "Unit")
        self.assertTrue(units, f"{PATH_UNIT} must set Unit={SERVICE_UNIT}")
        self.assertEqual(
            [value for value in units if value != SERVICE_UNIT],
            [],
            f"{PATH_UNIT} must trigger {SERVICE_UNIT}",
        )
        # The accepted ordering contract: the path unit is ordered against the
        # helper and the display manager, never against the service it starts.
        # Ordering the path unit before its own triggered service, or giving it
        # an After= on the helper, creates the known dependency cycle.
        before = " ".join(self.directives(text, "Before")).split()
        for target in ("armada-fixups.service", "display-manager.service"):
            self.assertIn(
                target,
                before,
                f"{PATH_UNIT} must be ordered Before={target}",
            )
        self.assertNotIn(
            SERVICE_UNIT,
            before,
            f"{PATH_UNIT} must not order itself Before={SERVICE_UNIT}; the "
            "triggered service already has After= on this unit's targets",
        )
        after = " ".join(self.directives(text, "After")).split()
        self.assertNotIn(
            "armada-fixups.service",
            after,
            f"{PATH_UNIT} must not list armada-fixups.service under After=; "
            "that pairs with the service's own ordering and forms a dependency "
            "cycle",
        )


def main():
    global REPO
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO), help="repo checkout root")
    args, rest = parser.parse_known_args()
    REPO = pathlib.Path(args.repo).resolve()
    unittest.main(argv=[sys.argv[0], *rest])


if __name__ == "__main__":
    main()
