"""Derive a stub 96 MiB boot image from the stock one.

The kShim packer requires a stock boot image as the base entry. For a
bring-up trial the Android entry is unused, so replacing its 36 MB kernel with
a 4 KiB ARM64 stub frees enough of the 128 MiB BL branch budget to fit a full
dracut initramfs.

Preserved from the stock kernel header: text_offset (0x08) and flags (0x18),
which the packer verifies on the repacked image.
"""

import struct
import sys

BOOT_MAGIC = b"ANDROID!"
AVB_MAGIC = b"AVBf"
PAGE = 4096
STUB_SIZE = 4096


def main(src, dst):
    data = bytearray(open(src, "rb").read())
    if data[:8] != BOOT_MAGIC:
        raise SystemExit("not an Android boot image: %r" % data[:8])

    kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
    if ramdisk_size != 0:
        raise SystemExit("stock ramdisk_size=%d, expected 0" % ramdisk_size)

    header = bytearray(data[PAGE:PAGE + 64])
    struct.pack_into("<Q", header, 16, STUB_SIZE)          # image_size
    stub = bytes(header) + b"\x00" * (STUB_SIZE - 64)

    out = bytearray(data)   # keep vbmeta blob and AVB footer untouched
    struct.pack_into("<I", out, 8, STUB_SIZE)    # kernel_size
    struct.pack_into("<I", out, 12, 0)           # ramdisk_size
    struct.pack_into("<I", out, 28, 0)           # signature_size
    out[PAGE:PAGE + STUB_SIZE] = stub
    out[-64:] = data[-64:]                       # keep AVB footer

    if out[-64:-60] != AVB_MAGIC:
        raise SystemExit("stock AVB footer missing")
    open(dst, "wb").write(out)
    print("stub: %d bytes, kernel_size=%d, original kernel was %d"
          % (len(out), STUB_SIZE, kernel_size))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
