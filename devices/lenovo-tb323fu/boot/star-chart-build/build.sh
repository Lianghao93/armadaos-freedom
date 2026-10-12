#!/bin/sh
set -eu
cd /work
mkdir -p evidence
step() {
    label=$1
    shift
    printf '%s\n' "$label"
    set +e
    "$@" > "evidence/$label.log" 2>&1
    status=$?
    set -e
    printf '%s\n' "$status" > "evidence/$label.exit"
    if [ "$status" != 0 ]; then tail -n 60 "evidence/$label.log"; exit "$status"; fi
}
if [ "${RESUME_AT:-}" != handoff ]; then
tar -xf source.tar -C source
sed -i 's/\r$//' source/ports/crdk/patches/*.patch
step host-configure cmake -S source/tests -B out/host -G Ninja -DCMAKE_C_COMPILER=clang -DKSHIM_TEST_MENU_STYLE=SURFACE_DARK
step host-build cmake --build out/host -j6
step host-tests ctest --test-dir out/host --output-on-failure
step star-configure cmake -S source/tests -B out/star -G Ninja -DCMAKE_C_COMPILER=clang -DKSHIM_TEST_MENU_STYLE=STAR_CHART
step star-build cmake --build out/star -j6 --target tb323fu_menu_test tb323fu_ui_preview menu_design_contract_test menu_locale_test
step star-tests ctest --test-dir out/star -R '^(tb323fu_menu_test|menu_design_contract_test|menu_locale_test)$' --output-on-failure
step heap-configure cmake -S source/tests -B out/heap -G Ninja -DCMAKE_C_COMPILER=clang -DKSHIM_TEST_MENU_STYLE=STAR_CHART -DKSHIM_TEST_STANDALONE=OFF
step heap-build cmake --build out/heap -j6 --target tb323fu_menu_test tb323fu_ui_preview
step heap-tests ctest --test-dir out/heap -R '^tb323fu_menu_test$' --output-on-failure
mkdir -p preview/star preview/heap
step preview-star out/star/tb323fu_ui_preview preview/star --star-chart
step preview-heap out/heap/tb323fu_ui_preview preview/heap --star-chart
step preview-png-star python3 source/tools/preview_manifest.py preview/star
step preview-png-heap python3 source/tools/preview_manifest.py preview/heap
step runtime python3 source/tools/build_runtime.py --config source/configs/tb323fu_defconfig --out out/runtime --jobs 6
fi
step qemu python3 source/tests/manifest_qemu.py --runtime out/runtime/kShimC.bin --out qemu-manifest
step boot python3 -B build_offline_boot.py --stock-boot inputs/boot-stock.img --stock-image inputs/Image.stock --linux-image inputs/Image.linux-165 --runtime out/runtime/kShimC.bin --tf-dtb inputs/tf.dtb --internal-dtb inputs/internal.dtb --packer qemu-manifest/packer/MultiBootKernelPatcher --unpacker qemu-manifest/packer/UnpackTool --repacker repack_boot_v4.py --avbtool inputs/avbtool.py --out boot-candidate
cp out/runtime/validation.json evidence/runtime-validation.json
cp qemu-manifest/validation.json evidence/qemu-validation.json
cp out/runtime/.config evidence/runtime.config
cp out/host/tb323fu-generated/config.h evidence/host-config.h
cp out/host/Testing/Temporary/LastTest.log evidence/host-full-tests.log
cp out/star/Testing/Temporary/LastTest.log evidence/star-full-tests.log
cp out/heap/Testing/Temporary/LastTest.log evidence/heap-full-tests.log
aarch64-linux-gnu-size out/runtime/kShimC.elf > evidence/runtime-size.txt
printf '0\n' > build.exit
