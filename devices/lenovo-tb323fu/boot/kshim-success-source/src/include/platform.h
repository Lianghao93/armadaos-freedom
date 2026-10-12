#pragma once
#include <stdint.h>
#include <stddef.h>
#include <menu_locale.h>

int kshim_platform_main(uint64_t fdt, uint64_t arg1, uint64_t arg2, uint64_t arg3);
/* Names must be in runtime-owned memory. Returns -1 when UI is unavailable,
 * -2 on unsafe shutdown (caller must not boot), or a selected index via out. */
int kshim_platform_select(uint64_t fdt, const char *const *names, size_t count,
                          size_t initial, uint32_t timeout_ms, size_t *selected);
/* Each set bit moves that original payload index into the advanced page.
 * Confirmed payloads still return their original manifest index. Page changes
 * cancel auto boot and keep the UI alive; the old API always remains flat. */
int kshim_platform_select_advanced(uint64_t fdt, const char *const *names,
                                  size_t count, size_t initial,
                                  uint32_t timeout_ms, uint64_t advanced_mask,
                                  size_t *selected);
/* Optional runtime-owned descriptions follow the same original payload indexes
 * as names. The array and individual entries may be NULL. Without advanced
 * actions this uses a flat menu while retaining the description metadata. */
int kshim_platform_select_described(uint64_t fdt, const char *const *names,
                                   const char *const *descriptions, size_t count,
                                   size_t initial, uint32_t timeout_ms,
                                   uint64_t advanced_mask, size_t *selected);
/* Localized variant: `roles` carries one semantic role per original payload
 * index (NULL means all UNKNOWN). Known roles pick their label/description from
 * the runtime locale table, while UNKNOWN entries keep the caller's exact
 * strings. Under the language-switch config this entry point and the
 * described/advanced wrappers all offer the runtime language page; only the
 * simple kshim_platform_select compatibility wrapper stays flat with no
 * language. Advanced layout and handoff are otherwise identical. */
int kshim_platform_select_localized(uint64_t fdt, const char *const *names,
                                    const char *const *descriptions,
                                    const kshim_menu_role_t *roles, size_t count,
                                    size_t initial, uint32_t timeout_ms,
                                    uint64_t advanced_mask, size_t *selected);
int kshim_platform_prepare_handoff(void);
