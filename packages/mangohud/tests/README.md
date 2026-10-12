# MangoHud tests

`mangoapp_wire_time_test.cpp` is a standalone raw-wire regression check for the
MangoApp packed message decode. It writes raw bytes at the numeric wire offsets
of the 95-byte `mangoapp_msg_v1` message, `memcpy`s them into the actual
MangoHud header struct, and asserts the decoded fields. It exists to pin the
Gamescope v1 sender field order (offset 16 = application/app-ready ns, offset 26
= visible ns) against the receiver names in `src/app/mangoapp_proto.h`.

It uses only the standard library plus the real header; there is no test
framework, no new dependency and no mirrored-header fixture. The asserts assume
LP64 (`long` is 8 bytes), which is what the Linux Docker test environment uses.

## Build and run

The test must be compiled against the actual patched MangoHud 0.8.4 upstream
header, not a copy. Point `MANGOHUD_SOURCE` at that checkout and `TASK_OUTPUT`
at an existing NAS-task-backed output directory. Commands run on the
Linux Docker test environment, not the Windows host.

```sh
MANGOHUD_SOURCE=/path/to/patched/MangoHud
TASK_OUTPUT=/path/to/existing/task/output

g++ -std=c++17 \
    -I "$MANGOHUD_SOURCE/src/app" \
    packages/mangohud/tests/mangoapp_wire_time_test.cpp \
    -o "$TASK_OUTPUT/mangoapp_wire_time_test"
"$TASK_OUTPUT/mangoapp_wire_time_test"
```

Run from the repository root so the source path above resolves. No download,
build system or extra tooling is required.

## Expected result

Against the original (pre-`0010`) header the file still compiles, but the test
reports four failing app/visible assertions (the two fields are swapped). With
the corrected header from `packages/mangohud/patches/0010-mangoapp-align-frametime-fields-with-gamescope.patch`
it passes. The 2026-10-11 repository sync repeated this ARM64 RED/GREEN check
against the actual header and recorded four expected failures followed by zero
failures. See the TB323FU status record for the separate build and device evidence.

This is a raw-wire check only. It is not an actual-device, HUD or latency
acceptance, and passing it does not validate rendering or performance.
