#!/usr/bin/env bash
#
# Regression tests for the accepted Y700 desktop touchscreen calibration
# persistence. They exercise the device-env profile selection and the
# armada_configure_desktop_touchscreen helper through fake sysfs and PATH
# stubs.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="$ROOT/system_files/usr/libexec/armada/desktop-bootstrap"
LIB="$ROOT/system_files/usr/lib/armada/desktop-bootstrap-lib"
DEVICE_ENV="$ROOT/system_files/usr/libexec/armada/device-env"
DEVICES_DIR="$ROOT/system_files/usr/lib/armada/devices"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

IDENTITY_CALIBRATION='1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1'
TOUCHSCREEN='NVTCapacitiveTouchScreen'

assert_eq() {
    local actual="$1" expected="$2" label="$3"

    if [[ "$actual" != "$expected" ]]; then
        printf '%s: expected %q, got %q\n' "$label" "$expected" "$actual" >&2
        exit 1
    fi
}

assert_contains() {
    local haystack="$1" needle="$2" label="$3"

    if [[ "$haystack" != *"$needle"* ]]; then
        printf '%s: expected %q to contain %q\n' "$label" "$haystack" "$needle" >&2
        exit 1
    fi
}

# Runs the real device-env against the repository profiles and prints one
# emitted variable value. device-env formats every value with printf %q, so the
# selected value is decoded by a trusted Bash evaluation of that escaped text
# (an empty value arrives as '' and must decode back to empty). A variable that
# is not emitted yields an empty string.
device_env_value() {
    local model="$1" var="$2"
    local line raw value

    ARMADA_MODEL="$model" ARMADA_DEVICE_DIR="$DEVICES_DIR" bash "$DEVICE_ENV" |
        while IFS= read -r line; do
            [[ "$line" == "$var="* ]] || continue
            raw="${line#"$var"=}"
            eval "value=${raw}"
            printf '%s' "$value"
        done
}

# --- device-env profile selection -------------------------------------------
# All accepted Y700 model strings must resolve to the calibrated profile.
for model in 'Lenovo Legion Y700 Gen 5 (TB323FU)' 'Lenovo Legion Tab Y700 (TB323FU)' 'Lenovo Legion Tab Y700 5th Gen'; do
    assert_eq "$(device_env_value "$model" ARMADA_DEVICE_ID)" lenovo-tb323fu \
        "$model device id"
    assert_eq "$(device_env_value "$model" ARMADA_PRIMARY_TOUCHSCREEN)" "$TOUCHSCREEN" \
        "$model primary touchscreen"
    assert_eq "$(device_env_value "$model" ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION)" \
        "$IDENTITY_CALIBRATION" "$model identity calibration"
done

# A different device must stay uncalibrated.
odin_calibration="$(device_env_value 'AYN Odin 2' ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION)"
odin_touchscreen="$(device_env_value 'AYN Odin 2' ARMADA_PRIMARY_TOUCHSCREEN)"
assert_eq "$(device_env_value 'AYN Odin 2' ARMADA_DEVICE_ID)" ayn-odin-2 'odin device id'
assert_eq "$odin_calibration" '' 'odin calibration empty'
assert_eq "$odin_touchscreen" '' 'odin touchscreen empty'

# --- fixtures ---------------------------------------------------------------
stub_bin="$TEST_ROOT/bin"
mkdir -p "$stub_bin"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf '\''%s\n'\'' "$*" >> "$KWRITE_LOG"' \
    'exit "${KWRITE_FAIL:-0}"' > "$stub_bin/kwriteconfig6"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf '\''%s\n'\'' "$*" >> "$GDBUS_LOG"' \
    'exit "${GDBUS_FAIL:-0}"' > "$stub_bin/gdbus"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$stub_bin/kscreen-doctor"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$stub_bin/dbus-update-activation-environment"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'exec bash "$ARMADA_REAL_DEVICE_ENV"' > "$stub_bin/device-env"
chmod +x "$stub_bin"/*

sys="$TEST_ROOT/sys/input"
mkdir -p "$sys/input10/id" "$sys/input10/event3" "$sys/input11/id" "$sys/input11/event4"
printf '%s\n' "$TOUCHSCREEN" > "$sys/input10/name"
printf '%s\n' '046d' > "$sys/input10/id/vendor"
printf '%s\n' 'c52b' > "$sys/input10/id/product"
printf '%s\n' 'Some Other Touchscreen' > "$sys/input11/name"
printf '%s\n' '0002' > "$sys/input11/id/vendor"
printf '%s\n' '0003' > "$sys/input11/id/product"

# shellcheck source=/dev/null
source "$LIB"

PATH="$stub_bin:/usr/bin:/bin"
ARMADA_INPUT_SYSFS_DIR="$sys"
kw_log="$TEST_ROOT/kwriteconfig6.log"
gdbus_log="$TEST_ROOT/gdbus.log"
export PATH ARMADA_INPUT_SYSFS_DIR
export KWRITE_LOG="$kw_log" GDBUS_LOG="$gdbus_log"
export KWRITE_FAIL=0 GDBUS_FAIL=0

# --- selected device: identity persistent and live calls --------------------
ARMADA_PRIMARY_TOUCHSCREEN="$TOUCHSCREEN"
ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION="$IDENTITY_CALIBRATION"
: > "$kw_log"
: > "$gdbus_log"
if ! armada_configure_desktop_touchscreen; then
    echo 'selected device calibration unexpectedly failed' >&2
    exit 1
fi
kwrite_calls="$(<"$kw_log")"
gdbus_calls="$(<"$gdbus_log")"
assert_contains "$kwrite_calls" 'kcminputrc' 'persistent config file'
assert_contains "$kwrite_calls" 'Libinput' 'persistent group'
assert_contains "$kwrite_calls" '1133' 'persistent decimal vendor'
assert_contains "$kwrite_calls" '50475' 'persistent decimal product'
assert_contains "$kwrite_calls" "$TOUCHSCREEN" 'persistent device name'
assert_contains "$kwrite_calls" 'CalibrationMatrix' 'persistent key'
assert_contains "$kwrite_calls" "$IDENTITY_CALIBRATION" 'persistent matrix'
assert_contains "$gdbus_calls" 'InputDevice/event3' 'live event object path'
assert_contains "$gdbus_calls" 'calibrationMatrix' 'live property'
assert_contains "$gdbus_calls" "$IDENTITY_CALIBRATION" 'live matrix'

# --- missing selected device: failure and no unrelated writes ---------------
ARMADA_PRIMARY_TOUCHSCREEN='No Such Touchscreen'
ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION="$IDENTITY_CALIBRATION"
: > "$kw_log"
: > "$gdbus_log"
if armada_configure_desktop_touchscreen; then
    echo 'missing selected device unexpectedly succeeded' >&2
    exit 1
fi
[[ ! -s "$kw_log" ]] || { echo 'wrote persistence for unrelated device' >&2; exit 1; }
[[ ! -s "$gdbus_log" ]] || { echo 'live write for unrelated device' >&2; exit 1; }

# --- empty calibration: successful no-op with no writes ---------------------
ARMADA_PRIMARY_TOUCHSCREEN="$odin_touchscreen"
ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION="$odin_calibration"
: > "$kw_log"
: > "$gdbus_log"
if ! armada_configure_desktop_touchscreen; then
    echo 'empty calibration no-op unexpectedly failed' >&2
    exit 1
fi
[[ ! -s "$kw_log" ]] || { echo 'empty calibration wrote persistence' >&2; exit 1; }
[[ ! -s "$gdbus_log" ]] || { echo 'empty calibration wrote live' >&2; exit 1; }

# --- persistence failure: failure and no live write -------------------------
ARMADA_PRIMARY_TOUCHSCREEN="$TOUCHSCREEN"
ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION="$IDENTITY_CALIBRATION"
: > "$kw_log"
: > "$gdbus_log"
KWRITE_FAIL=1
GDBUS_FAIL=0
if armada_configure_desktop_touchscreen; then
    echo 'persistence failure unexpectedly succeeded' >&2
    exit 1
fi
KWRITE_FAIL=0
[[ -s "$kw_log" ]] || { echo 'expected a persistence attempt' >&2; exit 1; }
[[ ! -s "$gdbus_log" ]] || { echo 'live write after persistence failure' >&2; exit 1; }

# --- live failure: failure after a successful persistence -------------------
: > "$kw_log"
: > "$gdbus_log"
KWRITE_FAIL=0
GDBUS_FAIL=1
if armada_configure_desktop_touchscreen; then
    echo 'live failure unexpectedly succeeded' >&2
    exit 1
fi
GDBUS_FAIL=0
[[ -s "$kw_log" ]] || { echo 'expected persistence before live call' >&2; exit 1; }
[[ -s "$gdbus_log" ]] || { echo 'expected a live call attempt' >&2; exit 1; }

# --- bootstrap integration: completed markers still recalibrate -------------
run_bootstrap() {
    local config="$1" model="${2:-Lenovo Legion Y700 Gen 5 (TB323FU)}"

    env \
        XDG_SESSION_TYPE=wayland \
        XDG_CONFIG_HOME="$config" \
        HOME="$TEST_ROOT/home" \
        PATH="$stub_bin:/usr/bin:/bin" \
        ARMADA_DEVICE_ENV="$stub_bin/device-env" \
        ARMADA_REAL_DEVICE_ENV="$DEVICE_ENV" \
        ARMADA_DEVICE_DIR="$DEVICES_DIR" \
        ARMADA_MODEL="$model" \
        ARMADA_INPUT_SYSFS_DIR="$sys" \
        KWRITE_LOG="$kw_log" \
        GDBUS_LOG="$gdbus_log" \
        KWRITE_FAIL="$KWRITE_FAIL" \
        GDBUS_FAIL="$GDBUS_FAIL" \
        "$BOOTSTRAP"
}

config="$TEST_ROOT/bootstrap"
: > "$kw_log"
: > "$gdbus_log"
run_bootstrap "$config"
for marker in desktop-rotation.done desktop-scale.done desktop-dual-screen.done; do
    [[ -s "$config/armada/$marker" ]] || { echo "missing completed marker $marker" >&2; exit 1; }
done
assert_eq "$(wc -l < "$kw_log")" 1 'first bootstrap persistent call'
assert_eq "$(wc -l < "$gdbus_log")" 1 'first bootstrap live call'

run_bootstrap "$config"
assert_eq "$(wc -l < "$kw_log")" 2 'repeated bootstrap persistent call'
assert_eq "$(wc -l < "$gdbus_log")" 2 'repeated bootstrap live call'

# A device without identity calibration must never be written during bootstrap.
odin_config="$TEST_ROOT/bootstrap-odin"
: > "$kw_log"
: > "$gdbus_log"
run_bootstrap "$odin_config" 'AYN Odin 2'
[[ ! -s "$kw_log" ]] || { echo 'odin bootstrap wrote persistence' >&2; exit 1; }
[[ ! -s "$gdbus_log" ]] || { echo 'odin bootstrap wrote live' >&2; exit 1; }

# A calibration failure must not block the desktop session.
fail_config="$TEST_ROOT/bootstrap-fail"
: > "$kw_log"
: > "$gdbus_log"
KWRITE_FAIL=1
if ! run_bootstrap "$fail_config"; then
    echo 'bootstrap blocked by calibration failure' >&2
    exit 1
fi
KWRITE_FAIL=0
[[ ! -s "$gdbus_log" ]] || { echo 'live write after failed persistence in bootstrap' >&2; exit 1; }

bash -n "$BOOTSTRAP" "$LIB" "$DEVICE_ENV"

printf 'Desktop touchscreen calibration tests passed\n'
