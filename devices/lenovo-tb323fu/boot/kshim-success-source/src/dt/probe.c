/* SPDX-License-Identifier: MIT */
#include "dt.h"
#include <string.h>

static int report_slot(void *fdt, const char *key, const char *value)
{
	const size_t keylen = strlen(key);
	const size_t slotlen = 80;
	const char marker[] = "pending";
	const size_t mlen = sizeof(marker) - 1;
	int chosen, plen;
	const char *bootargs, *p, *base;
	size_t vlen;
	int found = 0, offset = 0;
	char slot[80];
	size_t i;
	const char *v, *vend;
	size_t vlen2;

	if (!fdt || !key || !value)
		return -1;
	vlen = strlen(value);
	if (vlen == 0 || vlen > slotlen)
		return -1;
	for (i = 0; i < vlen; i++) {
		unsigned char c = (unsigned char)value[i];
		if (c < 33 || c > 126 || c == '"' || c == '\\')
			return -1;
	}

	chosen = fdt_path_offset(fdt, "/chosen");
	if (chosen < 0)
		return -1;
	bootargs = fdt_getprop(fdt, chosen, "bootargs", &plen);
	if (!bootargs || plen <= 0 || bootargs[plen - 1] != '\0')
		return -1;
	for (i = 0; i < (size_t)(plen - 1); i++)
		if (bootargs[i] == '\0')
			return -1;

	base = bootargs;
	p = bootargs;
	while (*p) {
		const char *end = strchr(p, ' ');
		size_t len = end ? (size_t)(end - p) : strlen(p);
		if (len >= keylen && memcmp(p, key, keylen) == 0) {
			if (found)
				return -1;
			found = 1;
			offset = (int)(p - base) + (int)keylen;
		}
		if (!end)
			break;
		p = end + 1;
	}
	if (!found)
		return -1;

	v = base + offset;
	vend = strchr(v, ' ');
	vlen2 = vend ? (size_t)(vend - v) : strlen(v);
	if (vlen2 != slotlen || memcmp(v, marker, mlen) != 0)
		return -1;
	for (i = mlen; i < slotlen; i++)
		if (v[i] != '_')
			return -1;

	memcpy(slot, value, vlen);
	memset(slot + vlen, '_', slotlen - vlen);
	return fdt_setprop_inplace_namelen_partial(fdt, chosen, "bootargs", 8,
						  (uint32_t)offset, slot, (int)slotlen);
}

int dt_probe_report(void *fdt, const char *value)
{
	return report_slot(fdt, "androidboot.kshim_probe=", value);
}

int dt_probe_report_slot(void *fdt, unsigned slot, const char *value)
{
	char key[] = "androidboot.kshim_s0=";

	if (slot > 8)
		return -1;
	key[sizeof(key) - 3] = (char)('0' + (int)slot);
	return report_slot(fdt, key, value);
}
