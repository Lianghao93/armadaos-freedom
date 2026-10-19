#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$ROOT/system_files/usr/libexec/armada/launch-steam"
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

STAMP=".armada-steam-stable-default-v1"
PUBLIC_BETA="steamdeck_publicbeta"
STABLE_CHANNEL="steamdeck_stable"
# Shares the public-beta prefix so only an exact channel match may replace it.
CUSTOM_CHANNEL="steamdeck_publicbeta_custom"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

CASE=
STEAM_ROOT=
LOG_DIR=

new_case() {
    CASE="$1"
    STEAM_ROOT="$WORK/$CASE/Steam Root with Spaces/Steam"
    LOG_DIR="$WORK/$CASE/log"
    mkdir -p "$LOG_DIR" "$STEAM_ROOT/steamrtarm64" "$STEAM_ROOT/package"
}

make_fake_steam() {
    cat >"$STEAM_ROOT/steamrtarm64/steam" <<'FAKE_STEAM'
#!/usr/bin/env bash
set -euo pipefail

log="${FAKE_STEAM_LOG_DIR:?FAKE_STEAM_LOG_DIR is required}"
root="${STEAM_ROOT:?STEAM_ROOT is required}"

: >"$log/argv"
for arg in "$@"; do
    printf '%s\0' "$arg" >>"$log/argv"
done

if [[ -e "$root/package/beta" ]]; then
    printf 'present\n' >"$log/beta-state"
    cp -- "$root/package/beta" "$log/beta"
else
    printf 'absent\n' >"$log/beta-state"
fi

if [[ -e "$root/.armada-steam-stable-default-v1" ]]; then
    printf 'present\n' >"$log/stamp-state"
else
    printf 'absent\n' >"$log/stamp-state"
fi
FAKE_STEAM
    chmod +x "$STEAM_ROOT/steamrtarm64/steam"
}

run_launcher() {
    env -u GAMESCOPE_WAYLAND_DISPLAY -u XDG_RUNTIME_DIR \
        STEAM_ROOT="$STEAM_ROOT" \
        FAKE_STEAM_LOG_DIR="$LOG_DIR" \
        "$LAUNCHER" "$@"
}

assert_state() {
    local path="$1" expected="$2" label="$3" actual

    actual="$(<"$path")"
    [[ "$actual" == "$expected" ]] \
        || fail "$label: expected '$expected', got '$actual'"
}

assert_argv() {
    local label="$1"
    shift
    local -a actual=()
    local -a expected=("$@")
    local i

    [[ -e "$LOG_DIR/argv" ]] || fail "$label: fake Steam was never executed"
    mapfile -d '' -t actual <"$LOG_DIR/argv" || true
    [[ "${#actual[@]}" -eq "${#expected[@]}" ]] \
        || fail "$label: expected ${#expected[@]} Steam arguments, got ${#actual[@]}"
    for i in "${!expected[@]}"; do
        [[ "${actual[$i]}" == "${expected[$i]}" ]] \
            || fail "$label: Steam argument $i: expected '${expected[$i]}', got '${actual[$i]}'"
    done
}

assert_marker_bytes() {
    local path="$1" channel="$2" label="$3"

    cmp -s -- "$path" <(printf '%s\n' "$channel") \
        || fail "$label: marker bytes changed (expected '$channel')"
}

assert_present() {
    [[ -e "$1" ]] || fail "$2"
}

assert_absent() {
    [[ ! -e "$1" ]] || fail "$2"
}

# 1. Legacy public beta with no stamp is replaced by the explicit stable channel.
new_case case1
printf '%s\n' "$PUBLIC_BETA" >"$STEAM_ROOT/package/beta"
make_fake_steam
args=(-applaunch 730 "-custom option with spaces")
run_launcher "${args[@]}" || fail "case 1: launcher failed to exec the fake Steam client"
assert_state "$LOG_DIR/beta-state" present "case 1: beta marker seen by Steam"
assert_marker_bytes "$LOG_DIR/beta" "$STABLE_CHANNEL" "case 1: marker bytes seen by Steam"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$STABLE_CHANNEL" "case 1: installed marker"
assert_state "$LOG_DIR/stamp-state" present "case 1: migration stamp seen by Steam"
assert_present "$STEAM_ROOT/$STAMP" "case 1: migration stamp was not created"
assert_argv "case 1" "${args[@]}"

# 2. Stable marker bytes survive and the migration is still marked.
new_case case2
printf '%s\n' "$STABLE_CHANNEL" >"$STEAM_ROOT/package/beta"
make_fake_steam
run_launcher || fail "case 2: launcher failed to exec the fake Steam client"
assert_state "$LOG_DIR/beta-state" present "case 2: beta marker seen by Steam"
assert_state "$LOG_DIR/stamp-state" present "case 2: migration stamp seen by Steam"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$STABLE_CHANNEL" "case 2: stable marker"
assert_present "$STEAM_ROOT/$STAMP" "case 2: migration stamp was not created"
assert_argv "case 2"

# 3. No beta file is not replaced with an invented one.
new_case case3
make_fake_steam
run_launcher || fail "case 3: launcher failed to exec the fake Steam client"
assert_state "$LOG_DIR/beta-state" absent "case 3: beta marker seen by Steam"
assert_state "$LOG_DIR/stamp-state" present "case 3: migration stamp seen by Steam"
assert_absent "$STEAM_ROOT/package/beta" "case 3: launcher invented a beta marker"
assert_present "$STEAM_ROOT/$STAMP" "case 3: migration stamp was not created"
assert_argv "case 3"

# 4. An unknown custom channel keeps its exact bytes.
new_case case4
printf '%s\n' "$CUSTOM_CHANNEL" >"$STEAM_ROOT/package/beta"
make_fake_steam
run_launcher || fail "case 4: launcher failed to exec the fake Steam client"
assert_state "$LOG_DIR/beta-state" present "case 4: beta marker seen by Steam"
assert_state "$LOG_DIR/stamp-state" present "case 4: migration stamp seen by Steam"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$CUSTOM_CHANNEL" "case 4: custom marker"
assert_present "$STEAM_ROOT/$STAMP" "case 4: migration stamp was not created"
assert_argv "case 4"

# 5. A previous migration must not override a later explicit beta choice.
new_case case5
printf '%s\n' "$PUBLIC_BETA" >"$STEAM_ROOT/package/beta"
touch "$STEAM_ROOT/$STAMP"
make_fake_steam
run_launcher || fail "case 5: launcher failed to exec the fake Steam client"
assert_state "$LOG_DIR/beta-state" present "case 5: beta marker seen by Steam"
assert_state "$LOG_DIR/stamp-state" present "case 5: migration stamp seen by Steam"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$PUBLIC_BETA" "case 5: user public beta marker"
assert_present "$STEAM_ROOT/$STAMP" "case 5: migration stamp disappeared"
assert_argv "case 5"

# 6. A missing or non-executable client fails before any marker or stamp write.
new_case case6
printf '%s\n' "$PUBLIC_BETA" >"$STEAM_ROOT/package/beta"

if run_launcher 2>"$LOG_DIR/missing-stderr"; then
    fail "case 6: launcher succeeded without a native Steam client"
else
    status=$?
fi
[[ "$status" -eq 127 ]] || fail "case 6: missing client exit status: expected 127, got $status"
grep -q 'native ARM Steam' "$LOG_DIR/missing-stderr" \
    || fail "case 6: missing client did not report the executable check"
assert_absent "$LOG_DIR/argv" "case 6: fake Steam ran without a client"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$PUBLIC_BETA" "case 6: beta marker after missing client"
assert_absent "$STEAM_ROOT/$STAMP" "case 6: missing client created a migration stamp"

printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"$STEAM_ROOT/steamrtarm64/steam"
chmod 0644 "$STEAM_ROOT/steamrtarm64/steam"

if run_launcher 2>"$LOG_DIR/nonexec-stderr"; then
    fail "case 6: launcher succeeded with a non-executable native Steam client"
else
    status=$?
fi
[[ "$status" -eq 127 ]] || fail "case 6: non-executable client exit status: expected 127, got $status"
grep -q 'native ARM Steam' "$LOG_DIR/nonexec-stderr" \
    || fail "case 6: non-executable client did not report the executable check"
assert_absent "$LOG_DIR/argv" "case 6: fake Steam ran without an executable bit"
assert_marker_bytes "$STEAM_ROOT/package/beta" "$PUBLIC_BETA" "case 6: beta marker after non-executable client"
assert_absent "$STEAM_ROOT/$STAMP" "case 6: non-executable client created a migration stamp"

printf 'Steam client channel migration tests passed\n'
