#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 danyi and the armadaos-freedom contributors
"""Double-Home -> Steam Quick Access Menu helper.

Target hardware: Lenovo Legion Tab Y700 5th Gen / TB323FU with the GameSir
Legion Gaming Controller G9 2026 (3537:1136).

Design intent (deliberately narrow):

* Passive, read-only observer of a raw evdev gamepad plus, when present, its
  exact sibling Consumer Control node. No EVIOCGRAB, no HID writes, no new
  virtual controller, no forwarding of the controller.
* Reads are only possible because the existing Armada InputPlumber 0.81.0
  patch shares raw gamepad input after hiding it from Steam. The udev marker
  ``E:INPUTPLUMBER_SHARED_GAMEPAD=1`` gates that assumption; if it is absent we
  log and wait without intercepting or altering anything.
* On a completed double tap we enqueue one ``Gamepad:Button:QuickAccess`` chord
  through the already-existing InputPlumber composite device (``busctl``
  ``SendButtonChord as 1``). We do not map or synthesise the raw Guide button.
  ``SendButtonChord`` is used (never ``SendEvent``) because InputPlumber 0.81.0
  panics on ``SendEvent``'s async ``blocking_send``; a successful call means the
  chord was *queued*, not confirmed shown.
* The G9 2026 firmware does not always deliver a fast double as two BTN_MODE
  taps. It may deliver the fast double -- sometimes the whole gesture, with no
  BTN_MODE at all -- as a ~200 ms ``KEY_HOMEPAGE`` pulse on the sibling
  "Consumer Control" node of the same HID device. That sibling is discovered
  and observed read-only alongside the gamepad, and a matched first down is
  accepted on its own as the firmware double action; either path still produces
  at most one chord.
* Normal Guide events are never suppressed or modified, so a single Home press
  keeps its existing Steam behaviour. A double press may briefly open then
  close the Steam menu before Quick Access appears.

This helper is not device acceptance on its own: its unit tests exercise the
pure logic with injected fakes and never touch a device. Only this helper plus
its unit are added; rollback means disabling the service and deleting those two
files.
"""

import glob
import os
import re
import selectors
import signal
import struct
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Linux hosts cannot use the ioctl
    fcntl = None

# ---------------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------------

# Exact evdev device name of the physical controller.
EVDEV_NAME = "GameSir Legion Gaming Controller G9 2026"

# Exact evdev device name of the *Consumer Control* node on that same HID
# device. It is not a gamepad and carries no shared-gamepad marker; it is only
# observed as the alternate delivery path for the second tap of a fast double.
CONSUMER_EVDEV_NAME = (
    "GameSir Legion Gaming Controller G9 2026 Consumer Control"
)

# USB HID ids, stored as the zero-padded lowercase hex used by sysfs id files.
VID_HEX = "3537"
PID_HEX = "1136"

# The permitted device-tree model aliases for this tablet. Anything else is
# rejected before any listening begins.
MODEL_ALIASES = frozenset(
    {
        "Lenovo Legion Tab Y700 5th Gen",
        "Lenovo Legion Y700 Gen 5 (TB323FU)",
        "Lenovo Legion Tab Y700 (TB323FU)",
    }
)

# The device-tree model may be exposed under either of these paths.
MODEL_PATHS = (
    "/proc/device-tree/model",
    "/sys/firmware/devicetree/base/model",
)

# Expected composite device name after an InputPlumber composite matches this
# tablet + controller pair.
EXPECTED_COMPOSITE_NAME = "Lenovo Legion Tab Y700 5th Gen (TB323FU) + Legion G9 2026"

# udev marker proving the existing patch shares the raw gamepad to non-Steam
# readers. Without it we must not touch the input stream.
SHARED_MARKER = "E:INPUTPLUMBER_SHARED_GAMEPAD=1"
UDEV_DATA_DIR = "/run/udev/data"

# ---------------------------------------------------------------------------
# InputPlumber DBus contract
# ---------------------------------------------------------------------------

INPUTPLUMBER_SERVICE = "org.shadowblip.InputPlumber"
COMPOSITE_IFACE = "org.shadowblip.Input.CompositeDevice"

# The one capability we are allowed to emit.
QUICKACCESS_CAP = "Gamepad:Button:QuickAccess"

# Composite device object paths look like .../InputPlumber/CompositeDeviceN.
COMPOSITE_PATH_RE = re.compile(
    r"^(/org/shadowblip/InputPlumber/CompositeDevice[0-9]+)\s*$"
)

# All busctl calls are bounded so a stuck bus can never wedge the service.
BUSCTL_TIMEOUT = 2.0

# ---------------------------------------------------------------------------
# Linux input constants
# ---------------------------------------------------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0x00
SYN_DROPPED = 0x03

BTN_MODE = 316  # the "Guide"/"Home" button.
KEY_HOMEPAGE = 172  # the Consumer Control Home pulse the firmware emits.
ABS_HAT0X = 16
ABS_HAT0Y = 17

# Gamepad button codes 304..318 inclusive. 316 (BTN_MODE) is Home and is
# handled separately; the rest act as chord/held interference.
GAMEPAD_BUTTONS = frozenset(range(304, 319))
OTHER_GAMEPAD_BUTTONS = GAMEPAD_BUTTONS - {BTN_MODE}

# struct input_event on 64-bit Linux (arm64): timeval{long,long} + u16 + u16 +
# s32 == 24 bytes. This is the arm64 record size this helper targets.
EVENT = struct.Struct("@llHHi")  # (sec, usec, type, code, value)
RECORD_SIZE = EVENT.size

READ_BATCH = 64  # records per os.read call.
SCAN_INTERVAL = 1.0  # seconds between rescans; also the select timeout.

# EVIOCSCLOCKID: select CLOCK_MONOTONIC as the timestamp source for this fd
# only. This is a per-fd timestamp-selection ioctl -- not a grab and not an
# input write. It lets us use the kernel event timestamps instead of local
# processing time.
EVIOCSCLOCKID = 0x400445A0

ACTION_DELAY = 0.120  # let ordinary Guide processing run before Quick Access.

# When the firmware reports the same physical double on both the gamepad and the
# Consumer Control node, two triggers can land within a few tens of ms. A single
# emitted chord is enough, so further triggers inside this window (measured with
# the kernel event timestamps, never wall time) are ignored.
ACTION_COOLDOWN = 0.400

ACTION_QUICKACCESS = "quickaccess"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def log(message):
    """Timestamped single-line diagnostic to stderr."""
    sys_stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        sys.stderr.write("armada-g9-double-home: %s %s\n" % (sys_stamp, message))
        sys.stderr.flush()
    except Exception:  # pragma: no cover - logging must never crash the loop
        pass


def normalize_model(raw):
    """Strip trailing NUL bytes and surrounding whitespace from a model name."""
    return raw.replace("\x00", "").strip()


def model_permitted(model):
    """Return True only for an exact permitted device-tree model alias."""
    return normalize_model(model) in MODEL_ALIASES


def evdev_identity_matches(name, vendor, product):
    """Exact match on evdev name plus sysfs id/vendor and id/product."""
    return (
        (name or "").strip() == EVDEV_NAME
        and (vendor or "").strip().lower() == VID_HEX
        and (product or "").strip().lower() == PID_HEX
    )


def parse_string_list(output):
    """Extract the quoted strings from a busctl `as` or `s` property reply."""
    return re.findall(r'"([^"]*)"', output or "")


def match_composite(source_path, source_paths, names, target_caps):
    """True when a composite maps our exact node, has the expected name, and
    advertises the Quick Access capability."""
    if source_path not in source_paths:
        return False
    if EXPECTED_COMPOSITE_NAME not in names:
        return False
    if QUICKACCESS_CAP not in target_caps:
        return False
    return True


def decode_records(buf):
    """Split a byte buffer into complete input_event records.

    Returns ``(records, leftover)`` where each record is the unpacked struct
    tuple and ``leftover`` is the trailing partial record (possibly empty).
    """
    records = []
    offset = 0
    total = len(buf)
    while total - offset >= RECORD_SIZE:
        records.append(EVENT.unpack_from(buf, offset))
        offset += RECORD_SIZE
    return records, buf[offset:]


def _marker_in_lines(lines):
    """True if the udev data lines carry the shared-gamepad marker."""
    return any((line or "").strip() == SHARED_MARKER for line in lines)


def _read_text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def read_device_model():
    for path in MODEL_PATHS:
        try:
            return normalize_model(_read_text(path))
        except OSError:
            continue
    return ""


def shared_marker_present(node):
    """Check /run/udev/data/c13:MINOR for the shared-gamepad marker."""
    try:
        minor = os.minor(os.stat(node).st_rdev)
    except OSError:
        return False
    data_path = os.path.join(UDEV_DATA_DIR, "c13:%d" % minor)
    try:
        with open(data_path, "r") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return False
    return _marker_in_lines(lines)


def discover_evdev_node():
    """Return /dev/input/eventN for the G9 2026, or None.

    Matches on sysfs name plus id/vendor and id/product; never assumes a fixed
    event number.
    """
    for name_path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            name = _read_text(name_path)
        except OSError:
            continue
        dev_dir = os.path.dirname(os.path.dirname(name_path))
        try:
            vendor = _read_text(os.path.join(dev_dir, "device", "id", "vendor"))
            product = _read_text(os.path.join(dev_dir, "device", "id", "product"))
        except OSError:
            continue
        if evdev_identity_matches(name, vendor, product):
            return "/dev/input/" + os.path.basename(dev_dir)
    return None


def sysfs_identity_for_node(node):
    """Return (name, vendor, product) for an existing /dev/input/eventN.

    Returns None if the node's sysfs identity cannot be read (e.g. it was
    removed or replaced).
    """
    sysname = os.path.basename(node)
    sys_dir = os.path.join("/sys/class/input", sysname)
    try:
        name = _read_text(os.path.join(sys_dir, "device", "name"))
        vendor = _read_text(os.path.join(sys_dir, "device", "id", "vendor"))
        product = _read_text(os.path.join(sys_dir, "device", "id", "product"))
    except OSError:
        return None
    return (name, vendor, product)


def default_source_validator(node):
    """Re-check, just before acting, that this node is still the G9 2026 and
    that the shared-gamepad marker is still present."""
    identity = sysfs_identity_for_node(node)
    if identity is None:
        return False
    if not evdev_identity_matches(*identity):
        return False
    return shared_marker_present(node)


def consumer_identity_matches(name, vendor, product):
    """Exact match on the Consumer Control evdev name plus sysfs id/vendor and
    id/product. The consumer node is not a gamepad, so no marker is checked."""
    return (
        (name or "").strip() == CONSUMER_EVDEV_NAME
        and (vendor or "").strip().lower() == VID_HEX
        and (product or "").strip().lower() == PID_HEX
    )


def sibling_matches(primary_hid_parent, name, vendor, product, hid_parent):
    """True only when a node is the exact Consumer Control sibling of the
    primary gamepad: correct name/ids *and* the same resolved HID parent.

    The HID parent is the directory that owns the ``input/inputN`` children
    (e.g. ``.../0003:3537:1136.0001``), so event7 (input7) and event8 (input8)
    share it while a same-named device on another HID device does not.
    """
    return (
        primary_hid_parent is not None
        and hid_parent is not None
        and primary_hid_parent == hid_parent
        and consumer_identity_matches(name, vendor, product)
    )


def sysfs_hid_parent(node):
    """Return the resolved HID device directory for an evdev node, or None.

    ``/sys/class/input/eventN/device`` resolves to ``.../<hid>/input/inputN``,
    so the HID parent is two directories above the resolved input device.
    """
    sysname = os.path.basename(node)
    input_dev = os.path.realpath(
        os.path.join("/sys/class/input", sysname, "device")
    )
    if not os.path.isdir(input_dev):
        return None
    return os.path.dirname(os.path.dirname(input_dev))


def discover_consumer_node(primary_node):
    """Return /dev/input/eventN for the G9 2026 Consumer Control sibling of the
    primary gamepad on the same HID device, or None.

    Never assumes a fixed event number and never inspects the sharing marker on
    the consumer (it is not a gamepad).
    """
    primary_parent = sysfs_hid_parent(primary_node)
    if primary_parent is None:
        return None
    for name_path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            name = _read_text(name_path)
        except OSError:
            continue
        dev_dir = os.path.dirname(os.path.dirname(name_path))
        node = "/dev/input/" + os.path.basename(dev_dir)
        if node == primary_node:
            continue
        try:
            vendor = _read_text(os.path.join(dev_dir, "device", "id", "vendor"))
            product = _read_text(
                os.path.join(dev_dir, "device", "id", "product")
            )
        except OSError:
            continue
        if sibling_matches(
            primary_parent, name, vendor, product, sysfs_hid_parent(node)
        ):
            return node
    return None


def default_consumer_validator(primary_node, consumer_node):
    """Re-check, just before a consumer Home action, that the primary is still
    validated and the consumer is still its exact sibling Consumer Control."""
    if not default_source_validator(primary_node):
        return False
    identity = sysfs_identity_for_node(consumer_node)
    if identity is None:
        return False
    if not consumer_identity_matches(*identity):
        return False
    primary_parent = sysfs_hid_parent(primary_node)
    consumer_parent = sysfs_hid_parent(consumer_node)
    return primary_parent is not None and primary_parent == consumer_parent


# ---------------------------------------------------------------------------
# Deterministic double-Home state machine
# ---------------------------------------------------------------------------


class DoubleHomeDetector(object):
    """Pure, deterministic double-Home detector.

    All timing is supplied by the caller as injected monotonic floats, so the
    whole machine is testable without sleeping or touching a device.

    A valid double is two complete short (<= TAP_MAX) Home down/up taps where
    the second down falls within DOUBLE_GAP after the first up. The trigger is
    produced on the second release. Single taps, long holds, repeat events and
    duplicate edges never trigger.
    """

    TAP_MAX = 0.300
    DOUBLE_GAP = 0.450

    def __init__(self):
        self.reset()
        self.discarding = False

    def reset(self):
        """Forget all transient state (used on disconnect and SYN_DROPPED)."""
        self.home_down = False
        self.home_down_t = 0.0
        self.home_valid = False
        self.second_candidate = False
        self.pending = False
        self.pending_deadline = 0.0
        self.held = set()
        self.hat_x = 0
        self.hat_y = 0

    def _cancel_pending(self):
        self.pending = False
        self.second_candidate = False
        # A chord button or non-neutral hat while Home is itself down must also
        # invalidate the in-progress Home tap, otherwise a later clean Home tap
        # could be misread as the second half of a double.
        if self.home_down:
            self.home_valid = False

    def clear_pending(self):
        """Forget a pending first tap without touching held buttons or the hat.

        Called after the consumer path reports the firmware double action so a
        stale first tap cannot later pair with an unrelated Home press into a
        phantom double.
        """
        self.pending = False
        self.second_candidate = False

    def feed(self, ev_type, code, value, timestamp):
        """Feed one raw event; return ACTION_QUICKACCESS or None."""
        if self.discarding:
            # Drop everything until the resynchronising SYN_REPORT; a Home
            # release inside this window must not be mistaken for a tap.
            if ev_type == EV_SYN and code == SYN_REPORT:
                self.discarding = False
            return None

        if ev_type == EV_SYN:
            if code == SYN_DROPPED:
                self.reset()
                self.discarding = True
            return None

        if ev_type == EV_KEY:
            return self._key(code, value, timestamp)

        if ev_type == EV_ABS:
            self._abs(code, value)

        return None

    def _key(self, code, value, timestamp):
        if code == BTN_MODE:
            if value == 1:
                if self.home_down:
                    return None  # duplicate down edge
                self.home_down = True
                self.home_down_t = timestamp
                self.home_valid = (
                    not self.held and self.hat_x == 0 and self.hat_y == 0
                )
                self.second_candidate = (
                    self.pending and timestamp <= self.pending_deadline
                )
            elif value == 0:
                if not self.home_down:
                    return None  # duplicate up edge
                duration = timestamp - self.home_down_t
                self.home_down = False
                valid = self.home_valid
                second = self.second_candidate
                self.home_valid = False
                self.second_candidate = False
                if not valid or duration > self.TAP_MAX:
                    # Long hold or a tap taken while something else was held.
                    self.pending = False
                    return None
                if second:
                    self.pending = False
                    return ACTION_QUICKACCESS
                self.pending = True
                self.pending_deadline = timestamp + self.DOUBLE_GAP
            # value == 2 (auto-repeat) falls through and is ignored.
            return None

        if code in OTHER_GAMEPAD_BUTTONS:
            if value == 1:
                self.held.add(code)
                self._cancel_pending()
            elif value == 0:
                self.held.discard(code)
        return None

    def _abs(self, code, value):
        if code == ABS_HAT0X:
            self.hat_x = value
        elif code == ABS_HAT0Y:
            self.hat_y = value
        else:
            return
        if value != 0:
            self._cancel_pending()


class ConsumerHomeDetector(object):
    """Detects the Consumer Control ``KEY_HOMEPAGE`` pulse.

    The G9 2026 firmware turns a fast double Home into a fixed ~200 ms
    ``KEY_HOMEPAGE`` pulse on the sibling Consumer Control node. Sometimes this
    pulse is the whole gesture (no ``BTN_MODE`` is emitted at all), so a matched
    down edge must be accepted on its own. This detector reports exactly one
    action on the first down edge; the release, auto-repeat and duplicate downs
    produce nothing. Volume, Power and every other key are ignored.
    ``SYN_DROPPED`` discards events until the resynchronising ``SYN_REPORT``.

    This class only tracks the edge; the caller owns device/pair validation.
    """

    def __init__(self):
        self.reset()
        self.discarding = False

    def reset(self):
        """Forget the edge state (used on disconnect and SYN_DROPPED)."""
        self.home_down = False

    def feed(self, ev_type, code, value, timestamp):
        """Feed one raw consumer event; return ACTION_QUICKACCESS or None."""
        if self.discarding:
            if ev_type == EV_SYN and code == SYN_REPORT:
                self.discarding = False
            return None

        if ev_type == EV_SYN:
            if code == SYN_DROPPED:
                self.reset()
                self.discarding = True
            return None

        if ev_type == EV_KEY and code == KEY_HOMEPAGE:
            if value == 1:
                if self.home_down:
                    return None  # duplicate down edge
                self.home_down = True
                return ACTION_QUICKACCESS
            if value == 0:
                self.home_down = False  # release; no action
            # value == 2 (auto-repeat) is ignored.
        return None


# ---------------------------------------------------------------------------
# InputPlumber client
# ---------------------------------------------------------------------------


class InputPlumberError(Exception):
    """Raised when a busctl invocation fails or times out."""


def _busctl_runner(args, timeout):
    """Default runner: execute busctl without a shell and return stdout."""
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputPlumberError(str(exc))
    if proc.returncode != 0:
        raise InputPlumberError(
            proc.stderr.decode("utf-8", "replace").strip()
            or "busctl exited %d" % proc.returncode
        )
    return proc.stdout.decode("utf-8", "replace")


class InputPlumberClient(object):
    """Minimal wrapper around the existing InputPlumber composite DBus object."""

    def __init__(self, runner=None, timeout=BUSCTL_TIMEOUT):
        self._runner = runner or _busctl_runner
        self._timeout = timeout

    def _busctl(self, args):
        return self._runner(["busctl", "--system"] + list(args), self._timeout)

    def list_composite_paths(self):
        output = self._busctl(["tree", "--list", INPUTPLUMBER_SERVICE])
        paths = set()
        for line in output.splitlines():
            # busctl tree output may be indented; strip only the surrounding
            # whitespace, never loosen the path identity itself.
            found = COMPOSITE_PATH_RE.match(line.strip())
            if found:
                paths.add(found.group(1))
        return sorted(paths)

    def get_property(self, path, name):
        return self._busctl(
            ["get-property", INPUTPLUMBER_SERVICE, path, COMPOSITE_IFACE, name]
        )

    def find_for_source(self, source_path):
        """Return the composite path mapping exactly ``source_path``, else None.

        The identity is re-read on every call, so a re-plugged or replaced
        device with a different name/id is naturally rejected.
        """
        for path in self.list_composite_paths():
            try:
                sources = parse_string_list(
                    self.get_property(path, "SourceDevicePaths")
                )
                names = parse_string_list(self.get_property(path, "Name"))
                caps = parse_string_list(
                    self.get_property(path, "TargetCapabilities")
                )
            except (InputPlumberError, OSError):
                continue
            if match_composite(source_path, sources, names, caps):
                return path
        return None

    def tap_quickaccess(self, path):
        """Enqueue one full Quick Access press/release chord.

        Uses SendButtonChord (``as``). InputPlumber queues the press and the
        release 80 ms apart internally, so a successful call means the chord
        was *accepted for queueing*, not that Quick Access was shown on screen.
        We deliberately do not retry: a timeout may already have enqueued the
        chord.

        Note: the async-safe SendButtonChord path is used because the live
        tablet crashes InputPlumber v0.81.0 when SendEvent is called (its async
        D-Bus method does a blocking_send). SendEvent is intentionally not
        offered as a fallback.
        """
        try:
            self._busctl(
                [
                    "call",
                    INPUTPLUMBER_SERVICE,
                    path,
                    COMPOSITE_IFACE,
                    "SendButtonChord",
                    "as",
                    "1",
                    QUICKACCESS_CAP,
                ]
            )
        except (InputPlumberError, OSError):
            return False
        return True


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Service(object):
    """Owns the read loop, device gating and the Quick Access action."""

    def __init__(
        self,
        client,
        sleep=time.sleep,
        source_validator=default_source_validator,
        consumer_validator=default_consumer_validator,
        consumer_discovery=discover_consumer_node,
        logger=log,
    ):
        self.client = client
        self._sleep = sleep
        self._validator = source_validator
        self._consumer_validator = consumer_validator
        self._consumer_discovery = consumer_discovery
        self._log = logger
        self._stop = False
        # Event-timestamp of the last emitted chord, for the short duplicate
        # cooldown (both the gamepad and the consumer path can report one double).
        self._last_action_ts = None
        self._primary_node = None
        self._consumer_node = None
        self._primary_det = DoubleHomeDetector()
        self._consumer_det = ConsumerHomeDetector()

    def request_stop(self, signum=None, frame=None):
        self._stop = True

    def run(self):
        model = read_device_model()
        if not model_permitted(model):
            self._log("unsupported device model %r; not listening" % (model,))
            return 0
        self._log("device model ok: %s" % model)

        last_status = None
        while not self._stop:
            node = discover_evdev_node()
            if node is None:
                # Log the absence only when the state changes, not every scan.
                if last_status != "no-node":
                    self._log("GameSir G9 2026 evdev node not found; waiting")
                    last_status = "no-node"
                self._sleep(SCAN_INTERVAL)
                continue
            if not shared_marker_present(node):
                status = "no-marker:" + node
                if last_status != status:
                    self._log(
                        "shared-gamepad marker absent for %s; waiting, "
                        "not intercepting input" % node
                    )
                    last_status = status
                self._sleep(SCAN_INTERVAL)
                continue

            if last_status != "observing:" + node:
                self._log("observing %s" % node)
            last_status = "observing:" + node
            self._read_loop(node)
            if self._stop:
                break
            self._log("%s detached; rescanning" % node)
            last_status = None
            self._sleep(SCAN_INTERVAL)
        return 0

    def _make_selector(self):
        """Create the readiness selector (overridable in tests)."""
        return selectors.DefaultSelector()

    def _open_monotonic(self, node):
        """Open a node read-only and select CLOCK_MONOTONIC timestamps on it.

        Returns the fd, or None after logging why it could not be used. This is
        a per-fd timestamp-selection ioctl -- no grab and no input write.
        """
        if fcntl is None:
            self._log(
                "fcntl unavailable; cannot select CLOCK_MONOTONIC on %s; "
                "closing and waiting" % node
            )
            return None
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            self._log("open failed for %s: %s" % (node, exc))
            return None
        # Ask the kernel for CLOCK_MONOTONIC event timestamps on this fd so the
        # detector sees true inter-event timing rather than the time we happen
        # to process a buffered batch.
        try:
            fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", time.CLOCK_MONOTONIC))
        except OSError as exc:
            self._log(
                "cannot select CLOCK_MONOTONIC on %s (%s); closing and waiting"
                % (node, exc)
            )
            try:
                os.close(fd)
            except OSError:
                pass
            return None
        return fd

    def _read_loop(self, node):
        primary_fd = self._open_monotonic(node)
        if primary_fd is None:
            return

        self._primary_node = node
        self._consumer_node = None
        self._primary_det = DoubleHomeDetector()
        self._consumer_det = ConsumerHomeDetector()

        consumer_fd = None
        buffers = {primary_fd: b""}
        selector = None
        try:
            selector = self._make_selector()
            selector.register(primary_fd, selectors.EVENT_READ, "primary")

            # Optional verified sibling Consumer Control node. Its absence must
            # never stop the primary gamepad from working.
            try:
                candidate = self._consumer_discovery(node)
            except Exception:
                candidate = None
            if candidate is not None:
                cfd = self._open_monotonic(candidate)
                if cfd is not None:
                    consumer_fd = cfd
                    self._consumer_node = candidate
                    buffers[cfd] = b""
                    selector.register(cfd, selectors.EVENT_READ, "consumer")
                    self._log("observing consumer %s" % candidate)

            while not self._stop:
                ready = selector.select(timeout=SCAN_INTERVAL)
                if not ready:
                    continue
                # Process the gamepad before the consumer so a fast double's
                # first tap is always seen before its consumer second tap.
                tags = set(key.data for key, _mask in ready)
                if "primary" in tags and not self._pump_primary(
                    primary_fd, buffers
                ):
                    break
                if consumer_fd is not None and "consumer" in tags:
                    if not self._pump_consumer(consumer_fd, buffers):
                        break
        finally:
            for fd in (primary_fd, consumer_fd):
                if fd is None:
                    continue
                if selector is not None:
                    try:
                        selector.unregister(fd)
                    except Exception:
                        pass
                try:
                    os.close(fd)
                except OSError:
                    pass
            if selector is not None:
                try:
                    selector.close()
                except Exception:
                    pass
            self._primary_det.reset()
            self._consumer_det.reset()
            self._primary_node = None
            self._consumer_node = None

    def _pump(self, fd, node, buffers):
        """Read at most one batch. Return (records, ok); ok False means EOF/error.

        ``buffers`` carries the per-fd trailing partial record so the two fds
        never share a reassembly buffer.
        """
        try:
            chunk = os.read(fd, RECORD_SIZE * READ_BATCH)
        except BlockingIOError:
            return [], True
        except OSError as exc:
            self._log("read error on %s: %s" % (node, exc))
            return [], False
        if not chunk:
            return [], False  # device unplugged
        buffer = buffers[fd] + chunk
        records, buffer = decode_records(buffer)
        buffers[fd] = buffer
        return records, True

    def _pump_primary(self, fd, buffers):
        records, ok = self._pump(fd, self._primary_node, buffers)
        if not ok:
            return False
        for record in records:
            # (sec, usec, type, code, value) as delivered by the kernel.
            self._feed_primary(
                record[2], record[3], record[4], record[0] + record[1] / 1e6
            )
            if self._stop:
                break
        return True

    def _pump_consumer(self, fd, buffers):
        records, ok = self._pump(fd, self._consumer_node, buffers)
        if not ok:
            return False
        for record in records:
            self._feed_consumer(
                record[2], record[3], record[4], record[0] + record[1] / 1e6
            )
            if self._stop:
                break
        return True

    def _feed_primary(self, ev_type, code, value, timestamp):
        """Runtime dispatch for one primary gamepad record."""
        action = self._primary_det.feed(ev_type, code, value, timestamp)
        if action == ACTION_QUICKACCESS:
            self._emit_if_allowed(self._primary_node, timestamp)

    def _feed_consumer(self, ev_type, code, value, timestamp):
        """Runtime dispatch for one Consumer Control record.

        A machine-matched ``KEY_HOMEPAGE`` down on the exact sibling node *is*
        the firmware's fast-double action, so it triggers on its own -- the G9
        sometimes emits only this pulse with no BTN_MODE at all. Release,
        auto-repeat and duplicate downs emit nothing, the exact sibling pair is
        re-validated before acting, and the chord is always routed to the
        *primary* source's composite.
        """
        action = self._consumer_det.feed(ev_type, code, value, timestamp)
        if action != ACTION_QUICKACCESS:
            return
        if not self._consumer_validator(self._primary_node, self._consumer_node):
            self._log("consumer Home: source pair changed; ignoring")
            return
        # Drop any leftover gamepad first tap so it cannot later pair with an
        # unrelated Home press into a phantom double.
        self._primary_det.clear_pending()
        self._emit_if_allowed(self._primary_node, timestamp)

    def _emit_if_allowed(self, node, timestamp):
        """Emit one chord unless the same gesture was just emitted.

        The cooldown is measured with the kernel event timestamps, so it never
        depends on how quickly this process happens to run. Single Home produces
        no action at all, so ordinary Guide behaviour is untouched.
        """
        if (
            self._last_action_ts is not None
            and timestamp - self._last_action_ts < ACTION_COOLDOWN
        ):
            self._log("quickaccess: duplicate gesture within cooldown; ignoring")
            return
        self._last_action_ts = timestamp
        self._emit_quickaccess(node)

    def _emit_quickaccess(self, node):
        # Give ordinary Guide processing a moment before adding Quick Access.
        self._sleep(ACTION_DELAY)
        if self._stop:
            return  # shutting down; do not start an action

        # Re-check the node identity and sharing marker right before acting, so
        # a replaced/renumbered node can never fire Quick Access.
        if not self._validator(node):
            self._log(
                "double Home: source identity changed for %s; ignoring" % node
            )
            return

        path = self.client.find_for_source(node)
        if path is None:
            self._log(
                "double Home: no matching composite for %s; ignoring" % node
            )
            return

        # Exactly one SendButtonChord per accepted double tap. InputPlumber
        # queues the whole press/release chord itself (80 ms apart), so we make
        # a single request and never retry -- a timeout may already have
        # enqueued the chord.
        try:
            queued = self.client.tap_quickaccess(path)
        except Exception:
            queued = False

        if queued:
            self._log(
                "double Home: QuickAccess chord queued on %s "
                "(queued, not confirmed shown)" % path
            )
        else:
            self._log(
                "double Home: QuickAccess chord request failed on %s" % path
            )


def main():
    service = Service(InputPlumberClient())
    signal.signal(signal.SIGTERM, service.request_stop)
    signal.signal(signal.SIGINT, service.request_stop)
    try:
        return service.run()
    finally:
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
