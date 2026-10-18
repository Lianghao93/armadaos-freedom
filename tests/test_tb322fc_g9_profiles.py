#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 danyi and the armadaos-freedom contributors
"""Focused packaged-configuration checks for the TB322FC + old G9 pair.

Standard library only. These read repository files and never touch a device,
DBus, systemd or the network. They assert that:

* the new InputPlumber profile matches only the exact Gen4 model and the old
  3537:1134 G9, and ships the expected composite/capability map/targets;
* the new capability map reuses the existing Gen5 mappings unchanged under
  its own id/name;
* the RGB catalog carries two independent GCM profiles (1134 and 1136).

They deliberately do not re-check unrelated device profiles or mirror every
line of the YAML files.
"""

import json
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_DEVICES_DIR = os.path.join(
    _ROOT, "system_files", "usr", "share", "inputplumber", "devices"
)
_CAPABILITY_DIR = os.path.join(
    _ROOT, "system_files", "usr", "share", "inputplumber", "capability_maps"
)
_DEVICE_PROFILE = os.path.join(
    _DEVICES_DIR, "05-lenovo-tb322fc-legion-g9.yaml"
)
_CAPABILITY_MAP = os.path.join(_CAPABILITY_DIR, "lenovo_legion_g9.yaml")
_GEN5_CAPABILITY_MAP = os.path.join(
    _CAPABILITY_DIR, "lenovo_legion_g9_2026.yaml"
)
_RGB_PROFILES = os.path.join(
    _ROOT, "packages", "armada-rgb", "profiles.json"
)

_GEN5_MODELS = (
    "Lenovo Legion Tab Y700 5th Gen",
    "Lenovo Legion Y700 Gen 5 (TB323FU)",
    "Lenovo Legion Tab Y700 (TB323FU)",
)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _model_values(text):
    return re.findall(
        r"^\s+- name: model\s*\n\s+value: (.+?)\s*$", text, re.MULTILINE
    )


class DeviceProfileTests(unittest.TestCase):
    def setUp(self):
        self.text = _read(_DEVICE_PROFILE)

    def test_composite_identity_and_targets(self):
        self.assertIn("kind: CompositeDevice", self.text)
        self.assertRegex(
            self.text,
            r"(?m)^name: Lenovo Legion Y700 Gen4 \(TB322FC\) \+ Legion G9$",
        )
        self.assertIn("single_source: false", self.text)
        self.assertEqual(
            re.findall(r"(?m)^\s+- (deck-uhid|keyboard)$", self.text),
            ["deck-uhid", "keyboard"],
        )

    def test_matches_only_the_exact_gen4_model(self):
        self.assertEqual(_model_values(self.text), ["Lenovo Legion Y700 Gen4"])
        for model in _GEN5_MODELS:
            self.assertNotIn(model, self.text)

    def test_sources_exact_old_g9_gamepad(self):
        self.assertRegex(
            self.text,
            r"(?m)^\s+name: GameSir Legion Gaming Controller G9$",
        )
        self.assertEqual(
            re.findall(r'product_id:\s*"?([0-9a-fA-F]+)"?', self.text),
            ["1134"],
        )
        self.assertEqual(
            re.findall(r'vendor_id:\s*"?([0-9a-fA-F]+)"?', self.text),
            ["3537"],
        )
        self.assertNotIn("1136", self.text)
        self.assertIn("handler: event*", self.text)

    def test_capability_map_id_is_shipped(self):
        ids = re.findall(r"(?m)^\s+capability_map_id: (\S+)$", self.text)
        self.assertEqual(ids, ["lenovo_legion_g9"])
        self.assertTrue(
            os.path.isfile(os.path.join(_CAPABILITY_DIR, ids[0] + ".yaml"))
        )

    def test_no_other_profile_matches_gen4(self):
        matching = [
            name
            for name in sorted(os.listdir(_DEVICES_DIR))
            if name.endswith(".yaml")
            and "value: Lenovo Legion Y700 Gen4"
            in _read(os.path.join(_DEVICES_DIR, name))
        ]
        self.assertEqual(matching, ["05-lenovo-tb322fc-legion-g9.yaml"])


class CapabilityMapTests(unittest.TestCase):
    def test_distinct_id_and_name(self):
        text = _read(_CAPABILITY_MAP)
        self.assertIn("kind: CapabilityMap", text)
        self.assertEqual(
            re.findall(r"(?m)^id: (\S+)$", text), ["lenovo_legion_g9"]
        )
        self.assertEqual(
            re.findall(r"(?m)^name: (.+)$", text), ["Lenovo Legion G9"]
        )

    def test_mapping_unchanged_from_gen5_map(self):
        def mapping_body(path):
            text = _read(path)
            return text.split("\nmapping:\n", 1)[1].rstrip() + "\n"

        self.assertEqual(
            mapping_body(_CAPABILITY_MAP), mapping_body(_GEN5_CAPABILITY_MAP)
        )

    def test_gen5_map_keeps_its_own_identity(self):
        text = _read(_GEN5_CAPABILITY_MAP)
        self.assertEqual(
            re.findall(r"(?m)^id: (\S+)$", text),
            ["lenovo_legion_g9_2026"],
        )


class RgbProfileTests(unittest.TestCase):
    def setUp(self):
        with open(_RGB_PROFILES, "r", encoding="utf-8") as handle:
            self.catalog = json.load(handle)

    def _profiles_with(self, model):
        return [
            profile
            for profile in self.catalog["profiles"]
            if model in profile["models"]
        ]

    def test_gen4_profile_is_exact(self):
        profiles = self._profiles_with("Lenovo Legion Y700 Gen4")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["models"], ["Lenovo Legion Y700 Gen4"])
        self.assertEqual(
            profiles[0]["backend"],
            {
                "type": "gcmhid",
                "vendor": "3537",
                "product": "1134",
                "strips": [1, 2],
            },
        )

    def test_gen5_profile_is_unchanged(self):
        profiles = self._profiles_with("Lenovo Legion Tab Y700 5th Gen")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(
            profiles[0]["backend"],
            {
                "type": "gcmhid",
                "vendor": "3537",
                "product": "1136",
                "strips": [1, 2],
            },
        )

    def test_two_independent_gcm_profiles(self):
        gcm = [
            profile
            for profile in self.catalog["profiles"]
            if profile["backend"]["type"] == "gcmhid"
        ]
        self.assertEqual(len(gcm), 2)
        self.assertEqual(
            sorted(profile["backend"]["product"] for profile in gcm),
            ["1134", "1136"],
        )
        self.assertTrue(
            set(self._profiles_with("Lenovo Legion Y700 Gen4")[0]["models"]).isdisjoint(
                set(self._profiles_with("Lenovo Legion Tab Y700 5th Gen")[0]["models"])
            )
        )


if __name__ == "__main__":
    unittest.main()
