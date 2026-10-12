# Y700 (TB323FU) + GameSir G9 2026 integration

Scope: the GameSir Legion Gaming Controller G9 2026 (USB `3537:1136`) attached to
the Lenovo Legion Tab Y700 5th Gen / TB323FU. Only the three TB323FU device-tree
model aliases plus the `3537:1136` USB ids are matched; other Legion models and
PIDs are not covered by this integration.

## Provenance

- The basic controller map and the RGB reference are adapted from
  `enij90/armada-tb321fu` at commit
  `62076530bf1ff34295d671016658965e54e597f1` (the upstream 3537:1134 Legion G9
  profile). The 1134-specific firmware-timing notes were not verified on the
  1136 G9 2026 and are not carried over.
- The double-Home helper (`armada-g9-double-home.py`) and its systemd unit are a
  local-integration addition, not part of that upstream reference.

## Usage

Physical basic controls, the fan absence, RGB on/off/color/brightness and Home
double/single presses were accepted by the user on 2026-10-11/12.

Double-Home for Steam Quick Access:

- `system_files/usr/libexec/armada/armada-g9-double-home.py` runs as the
  `armada-g9-double-home.service` systemd unit, enabled in the image by
  `build_files/40-vendor-system-files.sh` right after `inputplumber.service`.
  `system_files/` is copied verbatim to `/`, so the unit runs
  `/usr/libexec/armada/armada-g9-double-home.py`.
- The helper is a passive, read-only listener. It never grabs the device, writes
  HID or creates a virtual controller. It relies on the existing Armada
  InputPlumber patch that shares raw gamepad input to non-Steam readers, proven
  by the `E:INPUTPLUMBER_SHARED_GAMEPAD=1` udev marker. Without that marker it
  logs and waits without touching the input stream.
- A double Home is recognised either as two primary `BTN_MODE` taps inside the
  helper's window, or as the same-HID Consumer Control `KEY_HOMEPAGE` (172)
  firmware pulse the G9 sometimes emits for a fast double. Either path queues at
  most one `Gamepad:Button:QuickAccess` chord through the already-existing
  InputPlumber composite device (`SendButtonChord`). Ordinary single Home is
  never suppressed or modified, so it keeps its existing Steam behaviour.
- On a non-matching tablet model the helper exits successfully. On TB323FU,
  it waits for the matching controller and sharing marker before listening.

## Validation limitations

- Repository integration was checked in Docker on the remote build server:
  65 helper/packaging tests; 45 frontend tests plus i18n, TypeScript and Rollup;
  controller profiles, Control settings/RGB and RGB sleep-hook checks.
  RGB additionally passed 20 unit tests, 27 integration tests and an AArch64
  release build. The permission-denial test was run without DAC override,
  because an unrestricted root container bypasses its read-only fixture.
- These checks complement the earlier device acceptance; this repository sync
  does not install a new system image or repeat a full image boot test.
- No claim is made about all back buttons, IMU, haptics, multimode switching or
  hotplug RGB behaviour: those were not validated. Independent back-button
  support (beyond the reported `BTN_C`/`BTN_Z` mappings) is unconfirmed. Only the
  ordinary single Home path and the double-Home gesture above are asserted.
