# Armada RGB

`armada-rgb` controls RGB LEDs exposed through Linux's multicolor or individual
channel LED interfaces. It detects the device-tree model and loads the matching
hardware profile from `/usr/share/armada-rgb/profiles.json`.

The first version supports a solid color, brightness, persistent off, and
restoring the saved configuration:

```text
armada-rgb get
armada-rgb set --color FF8000 --brightness 25
armada-rgb off
armada-rgb apply
armada-rgb watch
armada-rgb supported
```

Settings are saved to `/etc/armada/rgb.json` after the hardware was
updated successfully. `armada-rgb watch` polls display brightness every 200 ms
by default and reapplies the settings when the brightness percentage changes.
The system image runs it as `armada-rgb-brightness-watch.service`. Only LED
names declared by the matched profile are used.
Profiles using the `channels` backend provide explicit target mappings such as
`red=l:r1`.

Profiles can provide conditional red, green, and blue channel reductions. The
saved correction can be changed with `armada-rgb set --correction`.

The versioned catalog groups exact device-tree model names with a `channels`,
`multicolor`, `serial`, or `gcmhid` backend, its targets or device, and an
optional default correction.

For tests, the profile catalog and device model paths can be overridden with
`ARMADA_RGB_PROFILES_PATH` and `ARMADA_RGB_MODEL_PATH`.

## Lenovo Legion G9 (2026)

The `gcmhid` backend drives the RGB lighting of the Lenovo Legion G9 controller
dock (USB `3537:1136`, the "Lenovo Legion Tab Y700 5th Gen" and its TB323FU
aliases) through its vendor HID interface. A sysfs `hidraw` node is used only
when its `HID_ID` matches the profile's vendor/product and its report descriptor
starts with the vendor usage page `0xFF7A`; it is then written through the
matching `/dev/hidrawN`.

The GCM packet format adapts the upstream protocol written for the older
`3537:1134` controller (enij90/armada-tb321fu commit `6207653`): the HSB
transform, byte-sum, 13-byte packet layout, and inter-strip delay match that
reference. On 2026-10-11, the user verified on/off, color and brightness with
the `3537:1136` G9 2026 on TB323FU. RGB restoration after hotplug, suspend or
reboot has not been validated.

The lighting lives in the detached controller, so `armada-rgb supported` reports
RGB as unavailable while the G9 is not docked and detected. Only the RGB
protocol above is ported; the upstream tablet's IMU and haptics support is not
included.
