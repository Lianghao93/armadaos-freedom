#!/usr/bin/env python3
"""Unit tests for armada-g9-double-home.py.

Standard library only. These exercise the pure detector, the identity/model
matching helpers, the busctl reply parsing and the emit/release sequence with
injected fakes. They do not touch any device, DBus or the network and are not
device acceptance; they are intended to be run remotely by Codex in Docker.
"""

import importlib.util
import os
import struct
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_MODULE_PATH = os.path.join(
    _ROOT,
    "system_files",
    "usr",
    "libexec",
    "armada",
    "armada-g9-double-home.py",
)
_SERVICE_PATH = os.path.join(
    _ROOT,
    "system_files",
    "usr",
    "lib",
    "systemd",
    "system",
    "armada-g9-double-home.service",
)
_VENDOR_BUILD = os.path.join(
    _ROOT, "build_files", "40-vendor-system-files.sh"
)

_spec = importlib.util.spec_from_file_location("g9_double_home", _MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# --- event construction helpers -------------------------------------------


def key(code, value, t):
    return (mod.EV_KEY, code, value, t)


def syn(code, t):
    return (mod.EV_SYN, code, 0, t)


def hat(code, value, t):
    return (mod.EV_ABS, code, value, t)


def feed_all(detector, events):
    """Feed events, collecting only the actions produced."""
    actions = []
    for ev_type, code, value, t in events:
        result = detector.feed(ev_type, code, value, t)
        if result is not None:
            actions.append(result)
    return actions


HOME = mod.BTN_MODE
SOUTH = 304  # a representative "other gamepad button"
NORTH = 307


class FakeBusctl(object):
    """Replays canned busctl stdout keyed by the argv tuple."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, args, timeout):
        argv = tuple(args)
        self.calls.append(argv)
        if argv in self.outputs:
            return self.outputs[argv]
        raise mod.InputPlumberError("no canned output for %r" % (argv,))


class RecordingClient(object):
    """Fake InputPlumberClient recording tap_quickaccess(path) requests."""

    def __init__(self, path="/org/shadowblip/InputPlumber/CompositeDevice0",
                 ok=True):
        self.path = path
        self.ok = ok
        self.calls = []

    def find_for_source(self, node):
        return self.path

    def tap_quickaccess(self, path):
        self.calls.append(path)
        return self.ok


# --- detector: basic tap semantics ----------------------------------------


class DetectorTapTests(unittest.TestCase):
    def test_event_struct_is_24_bytes(self):
        # arm64 struct input_event: timeval(16) + u16 + u16 + s32.
        self.assertEqual(mod.EVENT.size, 24)

    def test_single_home_produces_no_action(self):
        det = mod.DoubleHomeDetector()
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                syn(mod.SYN_REPORT, 0.00),
                key(HOME, 0, 0.05),
                syn(mod.SYN_REPORT, 0.05),
            ],
        )
        self.assertEqual(actions, [])
        # A single completed tap leaves a pending first tap, nothing else.
        self.assertTrue(det.pending)
        self.assertFalse(det.home_down)

    def test_valid_double_triggers_on_second_release(self):
        det = mod.DoubleHomeDetector()
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                key(HOME, 0, 0.05),  # first tap, pending
                key(HOME, 1, 0.10),  # second down within the window
                key(HOME, 0, 0.15),  # second release -> trigger
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])
        self.assertFalse(det.pending)

    def test_second_down_too_slow_no_trigger(self):
        det = mod.DoubleHomeDetector()
        # First up at 0.05, window closes at 0.05 + 0.45 = 0.50.
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                key(HOME, 0, 0.05),
                key(HOME, 1, 0.60),  # too late
                key(HOME, 0, 0.65),
            ],
        )
        self.assertEqual(actions, [])
        # The late tap becomes the new pending first tap.
        self.assertTrue(det.pending)

    def test_home_hold_longer_than_tap_max_is_invalid(self):
        det = mod.DoubleHomeDetector()
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                key(HOME, 0, 0.40),  # held 400ms > 300ms
            ],
        )
        self.assertEqual(actions, [])
        self.assertFalse(det.pending)

    def test_repeat_and_duplicate_edges_ignored(self):
        det = mod.DoubleHomeDetector()
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),  # down
                key(HOME, 2, 0.02),  # auto-repeat, ignored
                key(HOME, 1, 0.03),  # duplicate down, ignored
                key(HOME, 0, 0.05),  # up -> pending
                key(HOME, 0, 0.06),  # duplicate up, ignored
            ],
        )
        self.assertEqual(actions, [])
        self.assertTrue(det.pending)
        # A genuine second tap still works afterwards.
        actions = feed_all(
            det, [key(HOME, 1, 0.10), key(HOME, 0, 0.15)]
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])

    def test_triple_press_one_double_then_pending_single(self):
        det = mod.DoubleHomeDetector()
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                key(HOME, 0, 0.05),  # tap 1 -> pending
                key(HOME, 1, 0.08),  # tap 2 down (in window)
                key(HOME, 0, 0.15),  # tap 2 release -> double
                key(HOME, 1, 0.18),  # tap 3 down (no pending)
                key(HOME, 0, 0.25),  # tap 3 release -> new pending single
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])
        self.assertTrue(det.pending)


# --- detector: chord / held interference ----------------------------------


class DetectorChordTests(unittest.TestCase):
    def test_other_button_press_clears_pending_double(self):
        det = mod.DoubleHomeDetector()
        feed_all(det, [key(HOME, 1, 0.00), key(HOME, 0, 0.05)])
        self.assertTrue(det.pending)
        feed_all(det, [key(NORTH, 1, 0.07)])  # chord during the window
        self.assertFalse(det.pending)
        # Completing a second Home tap must not now trigger.
        actions = feed_all(det, [key(HOME, 1, 0.10), key(HOME, 0, 0.15)])
        self.assertEqual(actions, [])

    def test_home_while_other_button_held_cannot_count(self):
        det = mod.DoubleHomeDetector()
        feed_all(det, [key(NORTH, 1, 0.00)])  # already held before Home
        actions = feed_all(det, [key(HOME, 1, 0.02), key(HOME, 0, 0.06)])
        self.assertEqual(actions, [])
        self.assertFalse(det.pending)
        # Release the held button; a fresh double must then trigger.
        feed_all(det, [key(NORTH, 0, 0.10)])
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.20),
                key(HOME, 0, 0.25),
                key(HOME, 1, 0.30),
                key(HOME, 0, 0.35),
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])

    def test_hat_nonneutral_clears_pending_and_blocks_tap(self):
        det = mod.DoubleHomeDetector()
        feed_all(det, [key(HOME, 1, 0.00), key(HOME, 0, 0.05)])
        self.assertTrue(det.pending)
        feed_all(det, [hat(mod.ABS_HAT0X, -1, 0.07)])  # d-pad move
        self.assertFalse(det.pending)
        # Home while the hat is non-neutral must not count.
        actions = feed_all(det, [key(HOME, 1, 0.10), key(HOME, 0, 0.14)])
        self.assertEqual(actions, [])
        self.assertFalse(det.pending)
        # Return the hat to neutral and confirm a double still works.
        feed_all(det, [hat(mod.ABS_HAT0X, 0, 0.18)])
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.20),
                key(HOME, 0, 0.25),
                key(HOME, 1, 0.30),
                key(HOME, 0, 0.35),
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])

    def test_button_chord_during_home_down_invalidates_tap(self):
        det = mod.DoubleHomeDetector()
        # A chord button pressed AND released entirely while Home is held:
        # that Home tap must not count as a first tap.
        feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                key(SOUTH, 1, 0.02),
                key(SOUTH, 0, 0.03),
                key(HOME, 0, 0.06),
            ],
        )
        self.assertFalse(det.pending)
        # A following lone clean Home tap must not be mistaken for the second
        # half of a double.
        actions = feed_all(det, [key(HOME, 1, 0.10), key(HOME, 0, 0.15)])
        self.assertEqual(actions, [])

    def test_hat_chord_during_home_down_invalidates_tap(self):
        det = mod.DoubleHomeDetector()
        feed_all(
            det,
            [
                key(HOME, 1, 0.00),
                hat(mod.ABS_HAT0X, -1, 0.02),
                hat(mod.ABS_HAT0X, 0, 0.03),
                key(HOME, 0, 0.06),
            ],
        )
        self.assertFalse(det.pending)
        actions = feed_all(det, [key(HOME, 1, 0.10), key(HOME, 0, 0.15)])
        self.assertEqual(actions, [])


# --- detector: reset / resync ---------------------------------------------


class DetectorResetTests(unittest.TestCase):
    def test_disconnect_reset_clears_state(self):
        det = mod.DoubleHomeDetector()
        # First Home tap leaves a pending first tap...
        feed_all(det, [key(HOME, 1, 0.00), key(HOME, 0, 0.05)])
        self.assertTrue(det.pending)
        # ...but pressing another gamepad button both holds and cancels it.
        feed_all(det, [key(SOUTH, 1, 0.06)])
        self.assertTrue(det.held)
        self.assertFalse(det.pending)
        # A reset clears the held set and all transient state.
        det.reset()
        self.assertFalse(det.pending)
        self.assertFalse(det.home_down)
        self.assertEqual(det.held, set())

    def test_reset_clears_pending_only(self):
        det = mod.DoubleHomeDetector()
        feed_all(det, [key(HOME, 1, 0.00), key(HOME, 0, 0.05)])
        self.assertTrue(det.pending)
        det.reset()
        self.assertFalse(det.pending)
        self.assertFalse(det.home_down)
        self.assertEqual(det.held, set())
        # After a reset a fresh double still works.
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.10),
                key(HOME, 0, 0.15),
                key(HOME, 1, 0.20),
                key(HOME, 0, 0.25),
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])

    def test_syn_dropped_discards_until_syn_report(self):
        det = mod.DoubleHomeDetector()
        feed_all(det, [key(HOME, 1, 0.00)])  # Home is down and valid
        # Kernel drops events: everything until the next SYN_REPORT is ignored.
        feed_all(det, [syn(mod.SYN_DROPPED, 0.01)])
        self.assertTrue(det.discarding)
        # The release below must NOT be interpreted as a completed tap.
        result = det.feed(mod.EV_KEY, HOME, 0, 0.02)
        self.assertIsNone(result)
        self.assertFalse(det.pending)
        self.assertFalse(det.home_down)
        # Additional events are still dropped until SYN_REPORT re-syncs.
        det.feed(mod.EV_KEY, HOME, 1, 0.03)
        self.assertTrue(det.discarding)
        feed_all(det, [syn(mod.SYN_REPORT, 0.04)])
        self.assertFalse(det.discarding)
        # After resync a normal double press works.
        actions = feed_all(
            det,
            [
                key(HOME, 1, 0.10),
                key(HOME, 0, 0.15),
                key(HOME, 1, 0.20),
                key(HOME, 0, 0.25),
            ],
        )
        self.assertEqual(actions, [mod.ACTION_QUICKACCESS])


# --- identity / model gating ----------------------------------------------


class IdentityTests(unittest.TestCase):
    def test_model_aliases_accepted(self):
        for name in (
            "Lenovo Legion Tab Y700 5th Gen",
            "Lenovo Legion Y700 Gen 5 (TB323FU)",
            "Lenovo Legion Tab Y700 (TB323FU)",
        ):
            self.assertTrue(mod.model_permitted(name))
        # Trailing NUL, as read from the device tree.
        self.assertTrue(mod.model_permitted("Lenovo Legion Tab Y700 5th Gen\x00"))

    def test_model_rejected(self):
        for name in (
            "Lenovo Legion Tab Y700",
            "Lenovo Legion Go",
            "Lenovo Legion Y700 Gen 5",
            "",
            "Lenovo Legion Tab Y700 5th Gen (TB323FU)",  # not an allowed alias
        ):
            self.assertFalse(mod.model_permitted(name))

    def test_evdev_identity_exact_match(self):
        self.assertTrue(
            mod.evdev_identity_matches(mod.EVDEV_NAME, "3537", "1136")
        )
        # sysfs id files may carry surrounding whitespace or newlines.
        self.assertTrue(
            mod.evdev_identity_matches(mod.EVDEV_NAME + "\n", "3537\n", "1136\n")
        )
        # A 0x-prefixed id is not the sysfs form and must not match.
        self.assertFalse(
            mod.evdev_identity_matches(mod.EVDEV_NAME, "0x3537", "1136")
        )

    def test_evdev_identity_rejections(self):
        # Wrong name (e.g. the Consumer Control node).
        self.assertFalse(
            mod.evdev_identity_matches("Consumer Control", "3537", "1136")
        )
        # Wrong product (the older 1134).
        self.assertFalse(
            mod.evdev_identity_matches(mod.EVDEV_NAME, "3537", "1134")
        )
        # Wrong vendor.
        self.assertFalse(
            mod.evdev_identity_matches(mod.EVDEV_NAME, "1134", "1136")
        )

    def test_expected_composite_name(self):
        self.assertEqual(
            mod.EXPECTED_COMPOSITE_NAME,
            "Lenovo Legion Tab Y700 5th Gen (TB323FU) + Legion G9 2026",
        )


# --- marker / parse helpers ------------------------------------------------


class HelperTests(unittest.TestCase):
    def test_marker_line_detection(self):
        self.assertTrue(
            mod._marker_in_lines(["E:ID_INPUT_JOYSTICK=1",
                                  "E:INPUTPLUMBER_SHARED_GAMEPAD=1"])
        )
        self.assertFalse(
            mod._marker_in_lines(["E:ID_INPUT_JOYSTICK=1", "I:1234"])
        )
        self.assertFalse(mod._marker_in_lines([]))

    def test_parse_string_list(self):
        self.assertEqual(
            mod.parse_string_list('as 1 "/dev/input/event7"'),
            ["/dev/input/event7"],
        )
        self.assertEqual(
            mod.parse_string_list('s "Some Name"'), ["Some Name"]
        )
        self.assertEqual(mod.parse_string_list("as 0"), [])

    def test_decode_records_partial_and_reassembly(self):
        rec_a = mod.EVENT.pack(1, 2, mod.EV_KEY, HOME, 1)
        rec_b = mod.EVENT.pack(3, 4, mod.EV_KEY, HOME, 0)
        buffer = rec_a + rec_b + rec_a[:12]  # trailing partial record
        records, leftover = mod.decode_records(buffer)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(leftover), 12)
        self.assertEqual(records[0][2:], (mod.EV_KEY, HOME, 1))
        self.assertEqual(records[1][2:], (mod.EV_KEY, HOME, 0))
        # Completing the partial record yields a third record.
        records2, leftover2 = mod.decode_records(leftover + rec_a[12:])
        self.assertEqual(len(records2), 1)
        self.assertEqual(leftover2, b"")

    def test_decode_records_empty_and_short(self):
        records, leftover = mod.decode_records(b"")
        self.assertEqual(records, [])
        self.assertEqual(leftover, b"")
        records, leftover = mod.decode_records(b"\x00" * 5)
        self.assertEqual(records, [])
        self.assertEqual(len(leftover), 5)

    def _feed_decoded(self, records, use_record_time=True):
        detector = mod.DoubleHomeDetector()
        actions = []
        for record in records:
            timestamp = (
                record[0] + record[1] / 1e6 if use_record_time else 0.0
            )
            result = detector.feed(record[2], record[3], record[4], timestamp)
            if result is not None:
                actions.append(result)
        return actions

    def test_buffered_tap_gap_over_450ms_never_triggers(self):
        # Four records delivered in one read() batch but with kernel timestamps
        # far apart: two valid 50ms taps separated by a 950ms gap. Using the
        # per-record kernel timestamps there is no double.
        records = [
            mod.EVENT.pack(10, 0, mod.EV_KEY, HOME, 1),      # 10.000 down
            mod.EVENT.pack(10, 50000, mod.EV_KEY, HOME, 0),  # 10.050 up
            mod.EVENT.pack(11, 0, mod.EV_KEY, HOME, 1),      # 11.000 down
            mod.EVENT.pack(11, 50000, mod.EV_KEY, HOME, 0),  # 11.050 up
        ]
        buffer = b"".join(records)
        decoded, leftover = mod.decode_records(buffer)
        self.assertEqual(len(decoded), 4)
        self.assertEqual(leftover, b"")
        self.assertEqual(self._feed_decoded(decoded), [])
        # Sanity: the same batch collapsed onto one processing time WOULD be a
        # false double, which is exactly what per-record timestamps prevent.
        self.assertEqual(
            self._feed_decoded(decoded, use_record_time=False),
            [mod.ACTION_QUICKACCESS],
        )

    def test_buffered_home_hold_over_300ms_never_triggers(self):
        records = [
            mod.EVENT.pack(20, 0, mod.EV_KEY, HOME, 1),       # 20.000 down
            mod.EVENT.pack(20, 500000, mod.EV_KEY, HOME, 0),  # 20.500 up (>300ms)
            mod.EVENT.pack(20, 600000, mod.EV_KEY, HOME, 1),  # 20.600 down
            mod.EVENT.pack(20, 650000, mod.EV_KEY, HOME, 0),  # 20.650 up
        ]
        decoded, _ = mod.decode_records(b"".join(records))
        self.assertEqual(self._feed_decoded(decoded), [])


# --- composite matching ----------------------------------------------------


class MatchCompositeTests(unittest.TestCase):
    GOOD_NAME = mod.EXPECTED_COMPOSITE_NAME
    GOOD_CAPS = ["Gamepad:Button:Guide", mod.QUICKACCESS_CAP, "Sync"]

    def test_accepts_exact_match(self):
        self.assertTrue(
            mod.match_composite(
                "/dev/input/event7",
                ["/dev/input/event7"],
                [self.GOOD_NAME],
                self.GOOD_CAPS,
            )
        )

    def test_rejects_source_mismatch(self):
        self.assertFalse(
            mod.match_composite(
                "/dev/input/event9",
                ["/dev/input/event7"],
                [self.GOOD_NAME],
                self.GOOD_CAPS,
            )
        )

    def test_rejects_name_mismatch(self):
        self.assertFalse(
            mod.match_composite(
                "/dev/input/event7",
                ["/dev/input/event7"],
                ["Some Other Device"],
                self.GOOD_CAPS,
            )
        )

    def test_rejects_missing_quickaccess_capability(self):
        self.assertFalse(
            mod.match_composite(
                "/dev/input/event7",
                ["/dev/input/event7"],
                [self.GOOD_NAME],
                ["Gamepad:Button:Guide", "Sync"],
            )
        )


# --- InputPlumber client via fake busctl -----------------------------------


class ClientDiscoveryTests(unittest.TestCase):
    def _client(self):
        service = mod.INPUTPLUMBER_SERVICE
        iface = mod.COMPOSITE_IFACE
        path0 = "/org/shadowblip/InputPlumber/CompositeDevice0"
        path1 = "/org/shadowblip/InputPlumber/CompositeDevice1"
        outputs = {
            ("busctl", "--system", "tree", "--list", service): (
                "/org/shadowblip/InputPlumber\n"
                "  " + path1 + "\n"
                "  " + path0 + "\n"
                "  /org/shadowblip/InputPlumber/Manager\n"
            ),
            ("busctl", "--system", "get-property", service, path0, iface,
             "SourceDevicePaths"): 'as 1 "/dev/input/event7"',
            ("busctl", "--system", "get-property", service, path0, iface,
             "Name"): 's "%s"' % mod.EXPECTED_COMPOSITE_NAME,
            ("busctl", "--system", "get-property", service, path0, iface,
             "TargetCapabilities"): (
                'as 3 "Gamepad:Button:Guide" "%s" "Sync"' % mod.QUICKACCESS_CAP
            ),
            ("busctl", "--system", "get-property", service, path1, iface,
             "SourceDevicePaths"): 'as 1 "/dev/input/event3"',
            ("busctl", "--system", "get-property", service, path1, iface,
             "Name"): 's "Unrelated Device"',
            ("busctl", "--system", "get-property", service, path1, iface,
             "TargetCapabilities"): 'as 1 "Gamepad:Button:Guide"',
        }
        fake = FakeBusctl(outputs)
        return mod.InputPlumberClient(runner=fake), fake

    def test_list_composite_paths_sorted_and_filtered(self):
        client, _ = self._client()
        self.assertEqual(
            client.list_composite_paths(),
            [
                "/org/shadowblip/InputPlumber/CompositeDevice0",
                "/org/shadowblip/InputPlumber/CompositeDevice1",
            ],
        )

    def test_find_for_source_selects_matching_composite(self):
        client, _ = self._client()
        self.assertEqual(
            client.find_for_source("/dev/input/event7"),
            "/org/shadowblip/InputPlumber/CompositeDevice0",
        )
        # A node that is not a source of any composite yields nothing.
        self.assertIsNone(client.find_for_source("/dev/input/event99"))

    def test_tap_quickaccess_uses_single_sendbuttonchord(self):
        calls = []

        def runner(args, timeout):
            calls.append(tuple(args))
            return ""

        client = mod.InputPlumberClient(runner=runner)
        path = "/org/shadowblip/InputPlumber/CompositeDevice0"
        self.assertTrue(client.tap_quickaccess(path))
        # Exactly one busctl call, carrying a one-element `as` payload.
        self.assertEqual(len(calls), 1)
        base = ("busctl", "--system", "call", mod.INPUTPLUMBER_SERVICE, path,
                mod.COMPOSITE_IFACE, "SendButtonChord", "as")
        self.assertEqual(calls[0], base + ("1", mod.QUICKACCESS_CAP))

    def test_tap_quickaccess_reports_failure(self):
        def runner(args, timeout):
            raise mod.InputPlumberError("boom")

        client = mod.InputPlumberClient(runner=runner)
        self.assertFalse(client.tap_quickaccess("/x"))


# --- emit sequencing -------------------------------------------------------


class EmitSequenceTests(unittest.TestCase):
    NODE = "/dev/input/event7"

    def _service(self, client, sleep=None, source_validator=None, logger=None):
        if sleep is None:
            sleep = lambda _seconds: None
        if source_validator is None:
            # The real runtime revalidates sysfs identity plus the sharing
            # marker from source_validator; tests inject an always-true check
            # unless a test wants to exercise rejection.
            source_validator = lambda _node: True
        if logger is None:
            logger = lambda _message: None
        return mod.Service(
            client,
            sleep=sleep,
            source_validator=source_validator,
            logger=logger,
        )

    def test_one_request_per_double_tap(self):
        client = RecordingClient()
        service = self._service(client)
        service._emit_quickaccess(self.NODE)
        # Exactly one tap request; no separate press/release pair.
        self.assertEqual(client.calls, [client.path])

    def test_success_log_says_queued_not_shown(self):
        client = RecordingClient(ok=True)
        logs = []
        service = self._service(client, logger=logs.append)
        service._emit_quickaccess(self.NODE)
        self.assertEqual(len(logs), 1)
        self.assertIn("queued", logs[0])
        self.assertIn("not confirmed", logs[0])

    def test_failed_tap_no_retry_no_success_log(self):
        client = RecordingClient(ok=False)
        logs = []
        service = self._service(client, logger=logs.append)
        service._emit_quickaccess(self.NODE)
        # One attempt only; a failed request is never retried.
        self.assertEqual(client.calls, [client.path])
        self.assertEqual(len(logs), 1)
        self.assertIn("failed", logs[0])
        self.assertNotIn("queued", logs[0])

    def test_raising_tap_no_retry_no_success_log(self):
        class RaisingTapClient(object):
            def __init__(self):
                self.path = "/org/shadowblip/InputPlumber/CompositeDevice0"
                self.calls = []

            def find_for_source(self, node):
                return self.path

            def tap_quickaccess(self, path):
                self.calls.append(path)
                raise RuntimeError("ipc failure")

        client = RaisingTapClient()
        logs = []
        service = self._service(client, logger=logs.append)
        service._emit_quickaccess(self.NODE)  # must not propagate
        self.assertEqual(client.calls, [client.path])
        self.assertEqual(len(logs), 1)
        self.assertIn("failed", logs[0])
        self.assertNotIn("queued", logs[0])

    def test_stopping_during_action_delay_emits_nothing(self):
        client = RecordingClient()
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            service._stop = True  # stop arrives during the 120ms delay

        service = self._service(client, sleep=sleep)
        service._emit_quickaccess(self.NODE)
        self.assertEqual(sleeps, [mod.ACTION_DELAY])
        self.assertEqual(client.calls, [])

    def test_validator_false_emits_nothing(self):
        client = RecordingClient()
        service = self._service(client, source_validator=lambda _node: False)
        service._emit_quickaccess(self.NODE)
        self.assertEqual(client.calls, [])

    def test_missing_composite_sends_nothing(self):
        client = RecordingClient()
        client.path = None  # find_for_source yields nothing
        service = self._service(client)
        service._emit_quickaccess(self.NODE)
        self.assertEqual(client.calls, [])


# --- consumer (Consumer Control) detector ----------------------------------

CONSUMER = mod.CONSUMER_EVDEV_NAME
HOMEPAGE = mod.KEY_HOMEPAGE


class ConsumerDetectorTests(unittest.TestCase):
    def test_key_homepage_code_is_172(self):
        self.assertEqual(mod.KEY_HOMEPAGE, 172)

    def test_first_down_triggers_release_does_not(self):
        det = mod.ConsumerHomeDetector()
        self.assertEqual(
            det.feed(mod.EV_KEY, HOMEPAGE, 1, 1.00), mod.ACTION_QUICKACCESS
        )
        self.assertIsNone(det.feed(mod.EV_KEY, HOMEPAGE, 0, 1.20))
        self.assertFalse(det.home_down)

    def test_repeat_and_duplicate_down_ignored_then_fresh_pulse(self):
        det = mod.ConsumerHomeDetector()
        self.assertEqual(
            det.feed(mod.EV_KEY, HOMEPAGE, 1, 1.00), mod.ACTION_QUICKACCESS
        )
        self.assertIsNone(det.feed(mod.EV_KEY, HOMEPAGE, 2, 1.01))  # repeat
        self.assertIsNone(det.feed(mod.EV_KEY, HOMEPAGE, 1, 1.02))  # dup down
        self.assertIsNone(det.feed(mod.EV_KEY, HOMEPAGE, 0, 1.20))  # release
        self.assertEqual(
            det.feed(mod.EV_KEY, HOMEPAGE, 1, 2.00), mod.ACTION_QUICKACCESS
        )

    def test_other_consumer_keys_ignored(self):
        det = mod.ConsumerHomeDetector()
        for code in (114, 115, 116):  # VOLUMEDOWN / VOLUMEUP / POWER
            self.assertIsNone(det.feed(mod.EV_KEY, code, 1, 1.00))
            self.assertIsNone(det.feed(mod.EV_KEY, code, 0, 1.10))
        self.assertIsNone(det.feed(0x04, 4, 0, 1.20))  # consumer MSC_SCAN

    def test_syn_dropped_discards_until_syn_report(self):
        det = mod.ConsumerHomeDetector()
        det.feed(mod.EV_SYN, mod.SYN_DROPPED, 0, 1.00)
        self.assertTrue(det.discarding)
        self.assertIsNone(det.feed(mod.EV_KEY, HOMEPAGE, 1, 1.01))
        det.feed(mod.EV_SYN, mod.SYN_REPORT, 0, 1.02)
        self.assertFalse(det.discarding)
        self.assertEqual(
            det.feed(mod.EV_KEY, HOMEPAGE, 1, 1.10), mod.ACTION_QUICKACCESS
        )


# --- consumer identity / sibling gating ------------------------------------


class ConsumerIdentityTests(unittest.TestCase):
    def test_consumer_identity_exact(self):
        self.assertTrue(mod.consumer_identity_matches(CONSUMER, "3537", "1136"))
        # sysfs id files may carry surrounding whitespace/newlines.
        self.assertTrue(
            mod.consumer_identity_matches(CONSUMER + "\n", "3537\n", "1136\n")
        )

    def test_consumer_identity_rejections(self):
        # The gamepad's own name is not the consumer name.
        self.assertFalse(
            mod.consumer_identity_matches(mod.EVDEV_NAME, "3537", "1136")
        )
        self.assertFalse(mod.consumer_identity_matches(CONSUMER, "3537", "1134"))
        self.assertFalse(mod.consumer_identity_matches(CONSUMER, "1134", "1136"))
        self.assertFalse(mod.consumer_identity_matches("", "3537", "1136"))
        self.assertFalse(mod.consumer_identity_matches(CONSUMER, "0x3537", "1136"))

    def test_sibling_requires_same_hid_parent(self):
        parent = "/sys/devices/pci/0003:3537:1136.0001"
        self.assertTrue(
            mod.sibling_matches(parent, CONSUMER, "3537", "1136", parent)
        )

    def test_sibling_rejects_other_hid_device(self):
        parent = "/sys/devices/pci/0003:3537:1136.0001"
        self.assertFalse(
            mod.sibling_matches(parent, CONSUMER, "3537", "1136", parent + ".0002")
        )
        # Same HID parent but not the consumer name.
        self.assertFalse(
            mod.sibling_matches(parent, mod.EVDEV_NAME, "3537", "1136", parent)
        )

    def test_sibling_rejects_missing_parent(self):
        parent = "/sys/devices/pci/0003:3537:1136.0001"
        self.assertFalse(mod.sibling_matches(None, CONSUMER, "3537", "1136", parent))
        self.assertFalse(mod.sibling_matches(parent, CONSUMER, "3537", "1136", None))


# --- primary detector pending helpers --------------------------------------


class PendingAccessTests(unittest.TestCase):
    def test_clear_pending_keeps_held_and_hat(self):
        det = mod.DoubleHomeDetector()
        det.feed(mod.EV_KEY, mod.BTN_MODE, 1, 1.00)
        det.feed(mod.EV_KEY, mod.BTN_MODE, 0, 1.05)
        det.held.add(SOUTH)
        det.hat_x = -1
        self.assertTrue(det.pending)
        det.clear_pending()
        self.assertFalse(det.pending)
        self.assertFalse(det.second_candidate)
        # Held buttons and the hat are untouched by clear_pending.
        self.assertEqual(det.held, {SOUTH})
        self.assertEqual(det.hat_x, -1)


# --- runtime dispatch: primary + consumer ----------------------------------


class ConsumerDispatchTests(unittest.TestCase):
    PRIMARY = "/dev/input/event7"
    CONSUMER = "/dev/input/event8"

    def _service(self, client, consumer_validator=None, logger=None):
        if consumer_validator is None:
            consumer_validator = lambda _primary, _consumer: True
        if logger is None:
            logger = lambda _message: None
        return mod.Service(
            client,
            sleep=lambda _seconds: None,
            source_validator=lambda _node: True,
            consumer_validator=consumer_validator,
            logger=logger,
        )

    def _start(self, service, consumer=True):
        service._primary_node = self.PRIMARY
        service._consumer_node = self.CONSUMER if consumer else None

    def _primary(self, service, events):
        for code, value, timestamp in events:
            service._feed_primary(mod.EV_KEY, code, value, timestamp)

    def _consumer(self, service, events):
        for code, value, timestamp in events:
            service._feed_consumer(mod.EV_KEY, code, value, timestamp)

    def test_real_capture_single_then_fast_double(self):
        # Exact captured timestamps: a lone single Home, then a fast double whose
        # second tap the firmware delivers as a consumer KEY_HOMEPAGE pulse.
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        self._primary(
            service,
            [(mod.BTN_MODE, 1, 3484.624971), (mod.BTN_MODE, 0, 3484.659065)],
        )
        self.assertEqual(client.calls, [])  # single Home must do nothing
        self._primary(
            service,
            [(mod.BTN_MODE, 1, 3488.445962), (mod.BTN_MODE, 0, 3488.460963)],
        )
        self.assertEqual(client.calls, [])  # first tap alone must do nothing
        self._consumer(
            service,
            [
                (mod.KEY_HOMEPAGE, 1, 3488.557964),
                (mod.KEY_HOMEPAGE, 0, 3488.758965),
            ],
        )
        self.assertEqual(client.calls, [client.path])  # exactly one QAM

    def test_slow_double_two_primary_taps_one_action(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        self._primary(
            service,
            [
                (mod.BTN_MODE, 1, 0.00),
                (mod.BTN_MODE, 0, 0.05),
                (mod.BTN_MODE, 1, 0.10),
                (mod.BTN_MODE, 0, 0.15),
            ],
        )
        self.assertEqual(client.calls, [client.path])

    def test_consumer_release_repeat_duplicate_no_extra(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        self._primary(service, [(mod.BTN_MODE, 1, 1.00), (mod.BTN_MODE, 0, 1.02)])
        self._consumer(service, [(mod.KEY_HOMEPAGE, 1, 1.10)])
        self.assertEqual(client.calls, [client.path])
        self._consumer(
            service,
            [
                (mod.KEY_HOMEPAGE, 2, 1.12),  # repeat
                (mod.KEY_HOMEPAGE, 1, 1.14),  # duplicate down
                (mod.KEY_HOMEPAGE, 0, 1.30),  # release
            ],
        )
        self.assertEqual(client.calls, [client.path])

    def test_captured_consumer_only_double_sequence(self):
        # raw-consumer-capture.log: the firmware reports a repeated double as
        # consumer KEY_HOMEPAGE pulses with no preceding BTN_MODE. Two pulses
        # more than the 400 ms cooldown apart give two requests; a later single
        # gamepad Home gives none.
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        self._consumer(
            service,
            [
                (mod.KEY_HOMEPAGE, 1, 1791763393.469),
                (mod.KEY_HOMEPAGE, 0, 1791763393.670),
            ],
        )
        self.assertEqual(client.calls, [client.path])
        self._consumer(
            service,
            [
                (mod.KEY_HOMEPAGE, 1, 1791763395.339),
                (mod.KEY_HOMEPAGE, 0, 1791763395.539),
            ],
        )
        self.assertEqual(client.calls, [client.path, client.path])
        self._primary(
            service,
            [
                (mod.BTN_MODE, 1, 1791763403.000),
                (mod.BTN_MODE, 0, 1791763403.050),
            ],
        )
        self.assertEqual(client.calls, [client.path, client.path])

    def test_consumer_other_keys_ignored_with_pending(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        self._primary(service, [(mod.BTN_MODE, 1, 1.00), (mod.BTN_MODE, 0, 1.02)])
        self._consumer(
            service,
            [(114, 1, 1.10), (115, 1, 1.11), (116, 1, 1.12)],
        )
        self.assertEqual(client.calls, [])
        self.assertTrue(service._primary_det.pending)  # pending left untouched

    def test_consumer_action_clears_pending_prevents_phantom_double(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        # A clean first tap, pending until 1.05 + 0.45 = 1.50.
        self._primary(service, [(mod.BTN_MODE, 1, 1.00), (mod.BTN_MODE, 0, 1.05)])
        self._consumer(service, [(mod.KEY_HOMEPAGE, 1, 1.10)])  # second tap
        self.assertEqual(client.calls, [client.path])
        self.assertFalse(service._primary_det.pending)
        # A later single Home inside the old window must not pair with the
        # consumed first tap into a phantom double.
        self._primary(service, [(mod.BTN_MODE, 1, 1.20), (mod.BTN_MODE, 0, 1.25)])
        self.assertEqual(client.calls, [client.path])

    def test_consumer_pair_revalidation_rejects_before_action(self):
        client = RecordingClient()
        logs = []
        service = self._service(
            client,
            consumer_validator=lambda _primary, _consumer: False,
            logger=logs.append,
        )
        self._start(service)
        self._primary(service, [(mod.BTN_MODE, 1, 1.00), (mod.BTN_MODE, 0, 1.02)])
        self._consumer(service, [(mod.KEY_HOMEPAGE, 1, 1.10)])
        self.assertEqual(client.calls, [])  # revalidation failed -> no chord
        self.assertTrue(any("pair changed" in line for line in logs))

    def test_consumer_absent_primary_still_works(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service, consumer=False)
        self._primary(
            service,
            [
                (mod.BTN_MODE, 1, 0.00),
                (mod.BTN_MODE, 0, 0.05),
                (mod.BTN_MODE, 1, 0.10),
                (mod.BTN_MODE, 0, 0.15),
            ],
        )
        self.assertEqual(client.calls, [client.path])

    def test_duplicate_action_suppressed_within_cooldown(self):
        client = RecordingClient()
        service = self._service(client)
        self._start(service)
        service._emit_if_allowed(self.PRIMARY, 10.000)
        service._emit_if_allowed(self.PRIMARY, 10.100)  # within 400ms -> ignored
        self.assertEqual(client.calls, [client.path])
        service._emit_if_allowed(self.PRIMARY, 10.500)  # past cooldown -> emitted
        self.assertEqual(client.calls, [client.path, client.path])


# --- read loop: primary + optional consumer --------------------------------


class FakeKey(object):
    def __init__(self, fileobj, data):
        self.fileobj = fileobj
        self.data = data


class FakeSelector(object):
    """Returns scripted readiness rounds, then asks the service to stop."""

    def __init__(self, service, rounds):
        self._service = service
        self._rounds = [list(round_) for round_ in rounds]
        self._registered = {}

    def register(self, fd, mask, data=None):
        self._registered[fd] = data

    def unregister(self, fd):
        self._registered.pop(fd, None)

    def close(self):
        pass

    def select(self, timeout=None):
        if self._rounds:
            fds = self._rounds.pop(0)
            return [(FakeKey(fd, self._registered.get(fd)), None) for fd in fds]
        self._service._stop = True
        return []


class LoopService(mod.Service):
    """Service with the raw io and selector swapped for scripted fakes."""

    PRIMARY_FD = 900001
    CONSUMER_FD = 900002

    def __init__(self, client, script, discovery):
        mod.Service.__init__(
            self,
            client,
            sleep=lambda _seconds: None,
            source_validator=lambda _node: True,
            consumer_validator=lambda _primary, _consumer: True,
            consumer_discovery=discovery,
            logger=lambda _message: None,
        )
        self._script = {fd: list(records) for fd, records in script.items()}
        self._rounds = []
        self._fake_selector = None

    def _make_selector(self):
        self._fake_selector = FakeSelector(self, self._rounds)
        return self._fake_selector

    def _open_monotonic(self, node):
        if node.endswith("event8"):
            return self.CONSUMER_FD
        return self.PRIMARY_FD

    def _pump(self, fd, node, buffers):
        records = self._script.get(fd)
        if not records:
            return [], True
        self._script[fd] = []
        return records, True


class ReadLoopTests(unittest.TestCase):
    PRIMARY = "/dev/input/event7"
    CONSUMER = "/dev/input/event8"

    @staticmethod
    def _rec(sec, usec, code, value):
        return (sec, usec, mod.EV_KEY, code, value)

    def test_loop_dispatches_consumer_second_tap(self):
        client = RecordingClient()
        script = {
            LoopService.PRIMARY_FD: [
                self._rec(3484, 624971, mod.BTN_MODE, 1),
                self._rec(3484, 659065, mod.BTN_MODE, 0),
                self._rec(3488, 445962, mod.BTN_MODE, 1),
                self._rec(3488, 460963, mod.BTN_MODE, 0),
            ],
            LoopService.CONSUMER_FD: [
                self._rec(3488, 557964, mod.KEY_HOMEPAGE, 1),
                self._rec(3488, 758965, mod.KEY_HOMEPAGE, 0),
            ],
        }
        service = LoopService(
            client, script, discovery=lambda _node: self.CONSUMER
        )
        service._rounds = [[LoopService.PRIMARY_FD], [LoopService.CONSUMER_FD]]
        service._read_loop(self.PRIMARY)
        self.assertEqual(client.calls, [client.path])

    def test_loop_without_consumer_still_emits(self):
        client = RecordingClient()
        script = {
            LoopService.PRIMARY_FD: [
                self._rec(1, 0, mod.BTN_MODE, 1),
                self._rec(1, 50000, mod.BTN_MODE, 0),
                self._rec(1, 100000, mod.BTN_MODE, 1),
                self._rec(1, 150000, mod.BTN_MODE, 0),
            ],
        }
        service = LoopService(client, script, discovery=lambda _node: None)
        service._rounds = [[LoopService.PRIMARY_FD]]
        service._read_loop(self.PRIMARY)
        self.assertEqual(client.calls, [client.path])


# --- packaging regression --------------------------------------------------


class PackagingRegressionTests(unittest.TestCase):
    """Static checks that the unit and the image build agree on the helper.

    These read repository files only. They never invoke systemd, a device or
    the network; they only assert the shipped unit and the vendor build step
    reference the one helper path that exists in the image tree.
    """

    def _exec_start(self):
        with open(_SERVICE_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("ExecStart="):
                    return line.strip()[len("ExecStart="):].split()
        self.fail("no ExecStart line in %s" % _SERVICE_PATH)

    def test_execstart_target_is_shipped(self):
        argv = self._exec_start()
        self.assertEqual(argv[0], "/usr/bin/python3")
        installed = argv[1]
        # system_files is copied verbatim to / by the vendor build step, so the
        # absolute path the unit runs must exist there.
        self.assertTrue(installed.startswith("/usr/"), installed)
        repo_path = os.path.join(_ROOT, "system_files", installed.lstrip("/"))
        self.assertTrue(
            os.path.isfile(repo_path),
            "unit ExecStart target is not shipped: %s" % repo_path,
        )

    def test_vendor_build_enables_the_service(self):
        with open(_VENDOR_BUILD, "r", encoding="utf-8") as handle:
            build = handle.read()
        self.assertIn(
            "systemctl enable armada-g9-double-home.service", build
        )


if __name__ == "__main__":
    unittest.main()
