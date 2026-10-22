#!/bin/sh
# dracut pre-mount diagnostic hook: dump block device inventory to the console
# so a failed root mount is diagnosable from a photo of the screen.

exec >/dev/console 2>&1

echo
echo "=== KSHIM DIAG: block devices ==="
cat /proc/partitions 2>/dev/null

echo "=== KSHIM DIAG: lsblk ==="
lsblk -a 2>/dev/null || echo "lsblk unavailable"

echo "=== KSHIM DIAG: blkid ==="
blkid 2>/dev/null || echo "blkid unavailable"

echo "=== KSHIM DIAG: /sys/block ==="
ls /sys/block 2>/dev/null

echo "=== KSHIM DIAG: ufs modules ==="
grep -i ufs /proc/modules 2>/dev/null || echo "no ufs modules loaded"

echo "=== KSHIM DIAG: end ==="
