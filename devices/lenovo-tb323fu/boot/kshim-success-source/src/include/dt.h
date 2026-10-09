/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <libfdt.h>
struct dt_range { uint64_t base, size; };
struct dt_spec { int node; unsigned count; uint32_t args[8]; };
/* All other APIs require a blob accepted by dt_validate first. */
int dt_validate(const void *fdt, size_t available);
bool dt_available(const void *fdt, int node);
int dt_u32(const void *fdt, int node, const char *property, uint32_t *value);
int dt_bool(const void *fdt, int node, const char *property, bool *value);
const char *dt_string(const void *fdt, int node, const char *property);
int dt_string_index(const void *fdt, int node, const char *property, const char *name);
int dt_phandle(const void *fdt, uint32_t handle);
int dt_reference(const void *fdt, int node, const char *property, int index);
int dt_specifier(const void *fdt, int node, const char *property,
                 const char *cells_property, unsigned index, struct dt_spec *spec);
int dt_reg(const void *fdt, int node, unsigned index, struct dt_range *range);
int dt_reg_named(const void *fdt, int node, const char *name, struct dt_range *range);
int dt_raw_reg(const void *fdt, int node, unsigned index,
               uint64_t *address, uint64_t *size);
bool dt_contains(struct dt_range range, uint64_t address, uint64_t size);
int dt_reservations(const void *fdt, struct dt_range *ranges, bool *no_map,
                    size_t capacity, size_t *count);
/* Contract: callers must dt_validate the blob first. The tree must stay
 * immutable and initialization is single-threaded, non-nested scope; call
 * dt_parent_index_end before any mutation. Larger or deeper trees fall back
 * unchanged; 8192 nodes / 64 levels are performance caps only. */
void dt_parent_index_begin(const void *fdt);
void dt_parent_index_end(void);
int dt_parent_offset(const void *fdt, int offset);
/* Shares the parent-index scope: uses the index only when one is active for
 * this exact blob, otherwise falls back to libfdt. Matches
 * fdt_node_offset_by_compatible semantics exactly. */
int dt_node_offset_by_compatible(const void *fdt, int start, const char *compatible);

int dt_probe_report(void *fdt, const char *value);
int dt_probe_report_slot(void *fdt, unsigned slot, const char *value);
