#!/bin/sh
# dracut diagnostic hook: dump block device inventory + raw superblock probe.
# Installed at pre-mount and initqueue/timeout so it fires on failure too.

exec >/dev/console 2>&1

DEV=/dev/disk/by-partlabel/userdata

echo
echo "=== KSHIM DIAG ==="
echo "--- /proc/partitions ---"
cat /proc/partitions 2>/dev/null
echo "--- partlabel links ---"
ls -l /dev/disk/by-partlabel/ 2>/dev/null
echo "--- blkid ---"
blkid 2>/dev/null
echo "--- probe $DEV @0x10000 (btrfs magic sits at +0x40) ---"
if [ -e "$DEV" ]; then
    dd if="$DEV" bs=4096 skip=16 count=1 2>/dev/null | od -A x -t x1z | head -5
    echo "--- probe offset 0 (ext4/mbr area) ---"
    dd if="$DEV" bs=512 count=2 2>/dev/null | od -A x -t x1z | head -3
else
    echo "no $DEV node"
fi
echo "--- ufs in /proc/modules ---"
grep -i ufs /proc/modules 2>/dev/null || echo "(none: builtin or absent)"
echo "=== KSHIM DIAG: end ==="
