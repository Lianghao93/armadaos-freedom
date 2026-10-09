"""Tests for the TB323FU / SM8850P read-only runtime recorder.

The module under test is ``diagnostics/y700_runtime_record.py``.  Expected interface:
``collect_sample(sys_root, proc_root)`` returns one JSON-serialisable sample
dict; ``main(argv)`` runs the CLI.

sysfs/proc are faked with a TemporaryDirectory tree so the tests exercise real
file reads (and real missing-node handling) without touching a live device.
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "diagnostics")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import y700_runtime_record as rec  # noqa: E402


def _write_tree(base, files):
    for relpath, content in files.items():
        full = os.path.join(base, relpath.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)


def _fixture_sys_files():
    # Values mirror evidence/topology-thermal-power.log from the real device.
    return {
        "devices/system/cpu/online": "0-7\n",
        "devices/system/cpu/cpufreq/policy0/related_cpus": "0 1 2 3 4 5\n",
        "devices/system/cpu/cpufreq/policy0/scaling_cur_freq": "2112000\n",
        "devices/system/cpu/cpufreq/policy0/scaling_min_freq": "384000\n",
        "devices/system/cpu/cpufreq/policy0/scaling_max_freq": "2112000\n",
        "devices/system/cpu/cpufreq/policy0/scaling_governor": "conservative\n",
        "devices/system/cpu/cpufreq/policy6/related_cpus": "6 7\n",
        "devices/system/cpu/cpufreq/policy6/scaling_cur_freq": "2438400\n",
        "devices/system/cpu/cpufreq/policy6/scaling_min_freq": "768000\n",
        "devices/system/cpu/cpufreq/policy6/scaling_max_freq": "2438400\n",
        "devices/system/cpu/cpufreq/policy6/scaling_governor": "conservative\n",
        "class/devfreq/3d00000.gpu/cur_freq": "646000000\n",
        "class/devfreq/3d00000.gpu/min_freq": "160000000\n",
        "class/devfreq/3d00000.gpu/max_freq": "646000000\n",
        "class/devfreq/3d00000.gpu/governor": "simple_ondemand\n",
        "class/thermal/thermal_zone0/type": "qcom-battmgr-bat\n",
        "class/thermal/thermal_zone0/temp": "37100\n",
        "class/thermal/thermal_zone37/type": "gpuss-0-thermal\n",
        "class/thermal/thermal_zone37/temp": "45100\n",
        "class/thermal/cooling_device0/type": "cpufreq-cpu0\n",
        "class/thermal/cooling_device0/cur_state": "0\n",
        "class/thermal/cooling_device0/max_state": "27\n",
        "class/thermal/cooling_device2/type": "devfreq-3d00000.gpu\n",
        "class/thermal/cooling_device2/cur_state": "0\n",
        "class/thermal/cooling_device2/max_state": "16\n",
        "class/power_supply/qcom-battmgr-bat/uevent": (
            "POWER_SUPPLY_NAME=qcom-battmgr-bat\n"
            "POWER_SUPPLY_TYPE=Battery\n"
            "POWER_SUPPLY_STATUS=Charging\n"
            "POWER_SUPPLY_CAPACITY=73\n"
            "POWER_SUPPLY_VOLTAGE_NOW=4205000\n"
        ),
        "class/power_supply/qcom-battmgr-usb/uevent": (
            "POWER_SUPPLY_NAME=qcom-battmgr-usb\n"
            "POWER_SUPPLY_TYPE=USB\n"
            "POWER_SUPPLY_ONLINE=1\n"
            "POWER_SUPPLY_VOLTAGE_NOW=8718000\n"
        ),
    }


class CollectSampleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.sys_root = os.path.join(base, "sys")
        self.proc_root = os.path.join(base, "proc")
        os.makedirs(self.sys_root, exist_ok=True)
        _write_tree(self.sys_root, _fixture_sys_files())
        _write_tree(self.proc_root, {
            "sys/kernel/random/boot_id": "cbcc2dee-d72e-4ef6-86cc-93b0a4d588c2\n",
        })

    def test_cpufreq_policies_and_distinct_frequencies(self):
        sample = rec.collect_sample(self.sys_root, self.proc_root)
        policies = {entry["policy"]: entry for entry in sample["cpufreq"]}
        self.assertEqual(set(policies), {"policy0", "policy6"})

        self.assertEqual(policies["policy0"]["related_cpus"], "0 1 2 3 4 5")
        self.assertEqual(policies["policy0"]["cur_freq"], 2112000)
        self.assertEqual(policies["policy0"]["min_freq"], 384000)
        self.assertEqual(policies["policy0"]["max_freq"], 2112000)
        self.assertEqual(policies["policy0"]["governor"], "conservative")

        self.assertEqual(policies["policy6"]["related_cpus"], "6 7")
        self.assertEqual(policies["policy6"]["cur_freq"], 2438400)
        self.assertEqual(policies["policy6"]["min_freq"], 768000)
        self.assertNotEqual(
            policies["policy0"]["cur_freq"], policies["policy6"]["cur_freq"]
        )

    def test_gpu_thermal_cooling_power_fields(self):
        sample = rec.collect_sample(self.sys_root, self.proc_root)

        self.assertEqual(sample["boot_id"], "cbcc2dee-d72e-4ef6-86cc-93b0a4d588c2")
        self.assertEqual(sample["cpu_online"], "0-7")

        self.assertEqual(sample["gpu"]["cur_freq"], 646000000)
        self.assertEqual(sample["gpu"]["min_freq"], 160000000)
        self.assertEqual(sample["gpu"]["max_freq"], 646000000)
        self.assertEqual(sample["gpu"]["governor"], "simple_ondemand")

        zones = {entry["zone"]: entry for entry in sample["thermal_zones"]}
        self.assertEqual(zones["thermal_zone0"]["type"], "qcom-battmgr-bat")
        # Raw millidegree value must be preserved unchanged.
        self.assertEqual(zones["thermal_zone0"]["temp_milli"], 37100)
        self.assertEqual(zones["thermal_zone37"]["type"], "gpuss-0-thermal")
        self.assertEqual(zones["thermal_zone37"]["temp_milli"], 45100)

        coolers = {entry["device"]: entry for entry in sample["cooling_devices"]}
        self.assertEqual(coolers["cooling_device2"]["type"], "devfreq-3d00000.gpu")
        self.assertEqual(coolers["cooling_device2"]["cur_state"], 0)
        self.assertEqual(coolers["cooling_device2"]["max_state"], 16)
        self.assertEqual(coolers["cooling_device0"]["max_state"], 27)

        battery = sample["power_supply"]["qcom-battmgr-bat"]
        self.assertEqual(battery["POWER_SUPPLY_NAME"], "qcom-battmgr-bat")
        self.assertEqual(battery["POWER_SUPPLY_CAPACITY"], "73")
        self.assertEqual(battery["POWER_SUPPLY_VOLTAGE_NOW"], "4205000")
        usb = sample["power_supply"]["qcom-battmgr-usb"]
        self.assertEqual(usb["POWER_SUPPLY_ONLINE"], "1")

    def test_missing_nodes_do_not_crash(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        empty_sys = os.path.join(empty.name, "sys")
        empty_proc = os.path.join(empty.name, "proc")
        os.makedirs(empty_sys, exist_ok=True)
        os.makedirs(empty_proc, exist_ok=True)

        sample = rec.collect_sample(empty_sys, empty_proc)

        self.assertIsNone(sample["boot_id"])
        self.assertIsNone(sample["cpu_online"])
        self.assertEqual(sample["cpufreq"], [])
        self.assertEqual(sample["gpu"]["cur_freq"], None)
        self.assertEqual(sample["gpu"]["governor"], None)
        self.assertEqual(sample["thermal_zones"], [])
        self.assertEqual(sample["cooling_devices"], [])
        self.assertEqual(sample["power_supply"], {})
        self.assertIsInstance(sample["errors"], list)
        # Sample must always be JSON-serialisable even when everything is absent.
        json.dumps(sample)

    def test_unreadable_power_supply_is_recorded_as_null(self):
        broken = os.path.join(self.sys_root, "class/power_supply", "broken")
        os.makedirs(broken, exist_ok=True)
        sample = rec.collect_sample(self.sys_root, self.proc_root)
        self.assertIsNone(sample["power_supply"]["broken"])
        self.assertTrue(any("broken" in msg for msg in sample["errors"]))


class CliOnceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.sys_root = os.path.join(base, "sys")
        self.proc_root = os.path.join(base, "proc")
        os.makedirs(self.sys_root, exist_ok=True)
        _write_tree(self.sys_root, _fixture_sys_files())
        _write_tree(self.proc_root, {
            "sys/kernel/random/boot_id": "cbcc2dee-d72e-4ef6-86cc-93b0a4d588c2\n",
        })
        self.output = os.path.join(base, "out")

    def test_once_writes_parseable_jsonl(self):
        rc = rec.main([
            "--sys-root", self.sys_root,
            "--proc-root", self.proc_root,
            "--output", self.output,
            "--once",
        ])
        self.assertEqual(rc, 0)

        path = os.path.join(self.output, "runtime.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]

        self.assertEqual(len(lines), 1)
        sample = json.loads(lines[0])
        self.assertIn("timestamp_utc", sample)
        self.assertIn("monotonic", sample)
        policies = {entry["policy"]: entry for entry in sample["cpufreq"]}
        self.assertEqual(policies["policy6"]["cur_freq"], 2438400)
        self.assertEqual(sample["gpu"]["cur_freq"], 646000000)


if __name__ == "__main__":
    unittest.main()
