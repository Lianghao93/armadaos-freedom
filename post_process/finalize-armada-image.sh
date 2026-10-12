#!/bin/bash
# Post-BIB: prepare the SD image and compress.

set -euxo pipefail

RAW_IMAGE="${1:-output/raw/disk.raw}"
OUT="${OUT:-output/armada-$(TZ='America/New_York' date +%Y%m%d).img.gz}"

if [[ ! -f "${RAW_IMAGE}" ]]; then
    echo "ERROR: raw image not found at ${RAW_IMAGE}"
    echo "Run 'just build-raw' first."
    exit 1
fi

WORK=$(mktemp -d)
trap "sudo umount '${WORK}/mnt' 2>/dev/null || true; sudo losetup -d \"\$(cat ${WORK}/loop 2>/dev/null)\" 2>/dev/null || true; rm -rf '${WORK}'" EXIT

LOOP=$(sudo losetup -fP --show "${RAW_IMAGE}")
echo "${LOOP}" > "${WORK}/loop"
sleep 1

ESP="${LOOP}p1"
if ! sudo blkid "${ESP}" | grep -q 'TYPE="vfat"'; then
    echo "ERROR: ${ESP} is not vfat. BIB partition layout may have changed."
    sudo blkid "${LOOP}"*
    exit 1
fi

mkdir -p "${WORK}/mnt"
sudo mount "${ESP}" "${WORK}/mnt"

# Disable GRUB so ABL falls through to /KERNEL.
if [ -d "${WORK}/mnt/EFI" ]; then sudo mv "${WORK}/mnt/EFI" "${WORK}/mnt/EFI.disabled"; fi
sudo sync
sudo umount "${WORK}/mnt"

# The ESP carries the ARMADA label; internal installs are migrated to it by
# armada-esp-rename.
sudo fatlabel "${ESP}" ARMADA

# MBR, not GPT: a fixed-size GPT image flashed to a larger card strands the backup
# GPT mid-disk and Android's vold rejects the card. MBR has no end-of-disk
# structure, so it reads on any card. SD image only; internal installs stay GPT.
TABLE=$(sudo sfdisk -J "${LOOP}")
mapfile -t PARTS < <(jq -r '.partitiontable.partitions[] | "\(.start) \(.size)"' <<<"${TABLE}")
[ "${#PARTS[@]}" -eq 3 ] || { echo "ERROR: expected 3 partitions, got ${#PARTS[@]}"; sudo sfdisk -l "${LOOP}"; exit 1; }
read -r P1_START P1_SIZE <<<"${PARTS[0]}"
read -r P2_START P2_SIZE <<<"${PARTS[1]}"
read -r P3_START P3_SIZE <<<"${PARTS[2]}"
SECTORS=$(sudo blockdev --getsz "${LOOP}")

# Zero the two GPT copies (primary LBA 1-33, backup last 33 LBAs); dd avoids a
# gdisk dependency. The guards refuse any layout where a zero could hit a partition.
[ "$(jq -r '.partitiontable.sectorsize // 512' <<<"${TABLE}")" = 512 ] \
    || { echo "ERROR: non-512-byte sectors; GPT-zero math assumes 512"; exit 1; }
[ "${P1_START}" -ge 34 ] || { echo "ERROR: p1 starts inside the primary-GPT span"; exit 1; }
[ "$((P3_START + P3_SIZE))" -le "$((SECTORS - 33))" ] || { echo "ERROR: p3 overlaps the backup-GPT span"; exit 1; }
sudo dd if=/dev/zero of="${LOOP}" bs=512 seek=1 count=33 conv=notrunc status=none
sudo dd if=/dev/zero of="${LOOP}" bs=512 seek=$((SECTORS - 33)) count=33 conv=notrunc status=none

sudo sfdisk --label dos "${LOOP}" <<EOF
${P1_START},${P1_SIZE},c,*
${P2_START},${P2_SIZE},da
${P3_START},${P3_SIZE},da
EOF

sudo sfdisk -J "${LOOP}" \
    | jq -e '.partitiontable.label=="dos" and (.partitiontable.partitions|length)==3' >/dev/null \
    || { echo "ERROR: MBR conversion verify failed"; sudo sfdisk -l "${LOOP}"; exit 1; }

sudo losetup -d "${LOOP}"
rm "${WORK}/loop"

GZIP_LEVEL="${GZIP_LEVEL:-6}"
mkdir -p "$(dirname "${OUT}")"
pigz -f "-${GZIP_LEVEL}" -p "$(nproc)" -c "${RAW_IMAGE}" > "${OUT}"
rm -f "${RAW_IMAGE}"

echo "Built: ${OUT}"
echo "Flash to SD with:  zcat ${OUT} | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress"
