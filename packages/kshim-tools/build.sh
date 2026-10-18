#!/usr/bin/bash
# Runs inside the builder container. See ../build-local.sh for the contract.
#
# Builds the vendored MultiBootKernelPatcher/UnpackTool from source on the
# native ARM64 package runner (no shim assembly, no cross binutils), stages
# them with the existing v4 boot repack/AVB scripts, then runs the vendored
# regression tests against the staged binaries.
set -euxo pipefail

rm -rf out build test-out
mkdir -p out

dnf -y install cmake make gcc glibc-static binutils python3 dtc

cmake -S vendor/packer -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHIMS=OFF
cmake --build build --parallel

for tool in MultiBootKernelPatcher UnpackTool; do
    readelf -h "build/${tool}" | grep -q 'Machine:[[:space:]]*AArch64'
done

install -Dpm 0755 build/MultiBootKernelPatcher out/MultiBootKernelPatcher
install -Dpm 0755 build/UnpackTool out/UnpackTool
install -Dpm 0755 vendor/repack_boot_v4.py out/repack_boot_v4.py
install -Dpm 0755 vendor/avbtool.py out/avbtool.py
install -Dpm 0644 vendor/packer/LICENSE out/licenses/packer-LICENSE
install -Dpm 0644 vendor/packer/lib/inih/LICENSE.txt out/licenses/inih-LICENSE.txt

mkdir -p test-out
python3 vendor/packer/tests/payload_from.py \
    --packer out/MultiBootKernelPatcher \
    --unpacker out/UnpackTool \
    --out test-out/payload_from
python3 vendor/packer/tests/payload_from_nonbase.py \
    --packer out/MultiBootKernelPatcher \
    --unpacker out/UnpackTool \
    --out test-out/payload_from_nonbase
python3 vendor/packer/tests/dtb_alignment.py \
    --packer out/MultiBootKernelPatcher \
    --unpacker out/UnpackTool \
    --out test-out/dtb_alignment
