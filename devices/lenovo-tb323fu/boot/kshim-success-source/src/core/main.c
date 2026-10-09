#include <kshim.h>
#include <pl011_uart.h>
#include <kshim_console.h>
#include <timer.h>
#include <gicv3.h>
#include <arm64/boot.h>
#include <manifest.h>
#include <platform.h>
#include <dt.h>
#include <qcom_dt.h>
#include <dt_handoff.h>
#include <dt_simplefb.h>
#include <tb323fu_ramoops.h>
#include <tb323fu_ramoops_export.h>
#if defined(KSHIM_DIAGNOSTIC_MEMORY_ONLY) && (KSHIM_DIAGNOSTIC_MEMORY_ONLY != 0)
#include <diag_placement.h>
void arm64_diag_head_tail(uint64_t kernel_base, uint64_t dtb, uint64_t android_entry, uint64_t android_dtb, uint64_t flag_address) __attribute__((noreturn));
#endif
static uint64_t HandoffDtb[256 * 1024 / sizeof(uint64_t)]; /* bounded private DT scratch; 4 MiB linker assert applies */
/* Early, independent packed runtime-memory snapshot (32 KiB) and the DT
 * overlay/apply scratch (64 KiB); both bounded, aligned for FDT access, and
 * consumed only by the new dt_memory_capture/dt_memory_apply API. */
static __attribute__((aligned(64))) uint64_t
    RuntimeMemorySnapshot[DT_MEMORY_SNAPSHOT_BYTES / sizeof(uint64_t)];
static __attribute__((aligned(64))) uint64_t
    MemoryOverlay[DT_MEMORY_OVERLAY_BYTES / sizeof(uint64_t)];

#define PL011_UART_BASE 0x09000000ULL
#ifndef CONFIG_KSHIM_QEMU_PLATFORM
#define CONFIG_KSHIM_QEMU_PLATFORM 0
#endif

typedef struct {
    const MANIFEST *manifest;
    const MANIFEST_ENTRY *entries[MANIFEST_MAX_ENTRIES];
} MANIFEST_VIEW;

static bool is_executable_type(uint32_t type)
{
    return type == MF_ENTRY_TYPE_LINUX ||
        type == MF_ENTRY_TYPE_FREE_EXEC ||
        type == MF_ENTRY_TYPE_SHIM;
}

static bool is_flattened_device_tree(uint64_t address, uint64_t size)
{
    return address && size <= SIZE_MAX && dt_validate((const void *)(uintptr_t)address, (size_t)size) == 0;
}

static bool parse_manifest_entries(uint64_t manifest_address,
                                   uint64_t manifest_size,
                                   MANIFEST_VIEW *view)
{
    const MANIFEST *manifest = (const MANIFEST *)manifest_address;
    uint64_t cursor = sizeof(MANIFEST_HEADER);
    for (uint32_t index = 0; index < manifest->header.entry_count; index++) {
        if (cursor > manifest_size ||
            sizeof(MANIFEST_ENTRY) > manifest_size - cursor)
            return false;
        const MANIFEST_ENTRY *entry =
            (const MANIFEST_ENTRY *)(manifest_address + cursor);
        if (entry->name_len == 0)
            return false;
        uint64_t record_size = sizeof(MANIFEST_ENTRY) + entry->name_len;
        record_size = (record_size + MANIFEST_ENTRY_ALIGNMENT - 1) &
            ~(uint64_t)(MANIFEST_ENTRY_ALIGNMENT - 1);
        if (record_size > manifest_size - cursor)
            return false;
        for (uint32_t name_index = 0; name_index < entry->name_len;
             name_index++) {
            uint8_t character = (uint8_t)entry->name[name_index];
            if (character < 0x20 || character == 0x7f || character == ';')
                return false;
        }
        view->entries[index] = entry;
        cursor += record_size;
    }
    return cursor == manifest_size;
}

static bool get_manifest(uint64_t linux_base,
                         uint64_t manifest_address,
                         uint64_t manifest_size,
                         MANIFEST_VIEW *view)
{
    if (manifest_address < linux_base ||
        manifest_size < sizeof(MANIFEST_HEADER))
        return false;

    const MANIFEST *manifest = (const MANIFEST *)manifest_address;
    if (manifest->header.magic != MANIFEST_MAGIC ||
        manifest->header.version.major != MANIFEST_VERSION_MAJOR ||
        manifest->header.version.minor != MANIFEST_VERSION_MINOR ||
        manifest->header.entry_count < 2 ||
        manifest->header.entry_count > MANIFEST_MAX_ENTRIES ||
        manifest->header.default_entry >= manifest->header.entry_count)
        return false;

    uint64_t manifest_offset = manifest_address - linux_base;
    if (manifest_address > UINT64_MAX - manifest_size ||
        !parse_manifest_entries(manifest_address, manifest_size, view))
        return false;
    view->manifest = manifest;

    uint32_t attached_shim_index = manifest->header.entry_count - 1;
    const MANIFEST_ENTRY *attached_shim = view->entries[attached_shim_index];
    uint64_t attached_shim_offset =
        *(const uint64_t *)(linux_base + 0x30);
    if (manifest->header.default_entry == attached_shim_index ||
        attached_shim->type != MF_ENTRY_TYPE_SHIM ||
        attached_shim->flags != MF_ENTRY_FLAG_EXECUTABLE ||
        attached_shim->offset != attached_shim_offset ||
        attached_shim->size == 0 || attached_shim->entry_offset != 0)
        return false;

    const MANIFEST_ENTRY *base_entry = view->entries[0];
    if (base_entry->type != MF_ENTRY_TYPE_LINUX ||
        (base_entry->flags &
         (MF_ENTRY_FLAG_BASE_IMAGE | MF_ENTRY_FLAG_EXECUTABLE)) !=
            (MF_ENTRY_FLAG_BASE_IMAGE | MF_ENTRY_FLAG_EXECUTABLE) ||
        (base_entry->flags & ~(MF_ENTRY_FLAG_BASE_IMAGE |
                               MF_ENTRY_FLAG_EXECUTABLE |
                               MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB)) !=
            0 ||
        base_entry->offset != 0)
        return false;

    uint64_t runtime_image_size = *(const uint64_t *)(linux_base + 0x10);
    if (linux_base > UINT64_MAX - runtime_image_size)
        return false;
    uint64_t packed_image_end = linux_base + runtime_image_size;

    for (uint32_t index = 0; index < manifest->header.entry_count; index++) {
        const MANIFEST_ENTRY *entry = view->entries[index];
        bool executable =
            (entry->flags & MF_ENTRY_FLAG_EXECUTABLE) != 0;
        if (executable != is_executable_type(entry->type))
            return false;
        bool copy_image = (entry->flags & MF_ENTRY_FLAG_COPY) != 0;
        bool has_dtb =
            (entry->flags & MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB) != 0;
        uint32_t allowed_flags = index == 0
            ? MF_ENTRY_FLAG_BASE_IMAGE | MF_ENTRY_FLAG_EXECUTABLE |
              MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB
            : MF_ENTRY_FLAG_COPY | MF_ENTRY_FLAG_EXECUTABLE |
              MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB |
              MF_ENTRY_FLAG_HAS_QC_ABOOT_HEADER;
        if ((entry->flags & ~allowed_flags) != 0 ||
            entry->offset > manifest_offset ||
            entry->size > manifest_offset - entry->offset)
            return false;
        if (entry->alignment < MANIFEST_ALIGNMENT_SHIFT_MIN ||
            entry->alignment > MANIFEST_ALIGNMENT_SHIFT_MAX)
            return false;
        if ((entry->flags & MF_ENTRY_FLAG_HAS_QC_ABOOT_HEADER) &&
            entry->type != MF_ENTRY_TYPE_LINUX)
            return false;
        if (executable &&
            (entry->entry_offset > entry->size ||
             entry->size - entry->entry_offset < 4 ||
             (entry->entry_offset & 3) != 0))
            return false;
        if (!has_dtb && entry->device_tree_blob_entry != 0)
            return false;
        if (!executable &&
            (copy_image || entry->entry_offset != 0 || has_dtb))
            return false;
        if (copy_image) {
            if (!executable || entry->load_address == 0 ||
                (entry->load_address & 3) != 0 ||
                entry->copy_size_max == 0 ||
                entry->size > entry->copy_size_max)
                return false;
            if (entry->load_address > UINT64_MAX - entry->size)
                return false;
            uint64_t copy_end = entry->load_address + entry->size;
            if (entry->load_address < packed_image_end &&
                copy_end > linux_base)
                return false;
        } else if (entry->load_address != 0 || entry->copy_size_max != 0) {
            return false;
        }
        if (entry->type == MF_ENTRY_TYPE_DTB &&
            !is_flattened_device_tree(linux_base + entry->offset,
                                      entry->size)) {
            return false;
        }
        if (has_dtb) {
            if (!executable ||
                entry->device_tree_blob_entry >=
                    manifest->header.entry_count) {
                return false;
            }
            const MANIFEST_ENTRY *dtb =
                view->entries[entry->device_tree_blob_entry];
            if (dtb->type != MF_ENTRY_TYPE_DTB || dtb->flags != 0 ||
                !is_flattened_device_tree(linux_base + dtb->offset,
                                          dtb->size)) {
                return false;
            }
        }
    }

    if ((view->entries[manifest->header.default_entry]->flags &
            MF_ENTRY_FLAG_EXECUTABLE) == 0)
        return false;

    return true;
}

static void sync_image_cache(uint64_t address, uint64_t size)
{
    uint64_t ctr_el0 = 0;
    asm volatile("mrs %0, ctr_el0" : "=r"(ctr_el0));

    uint64_t data_line_size = 4ULL << ((ctr_el0 >> 16) & 0xf);
    uint64_t instruction_line_size = 4ULL << (ctr_el0 & 0xf);
    uint64_t end = address + size;

    for (uint64_t current = address & ~(data_line_size - 1);
         current < end; current += data_line_size)
        asm volatile("dc cvau, %0" : : "r"(current) : "memory");
    asm volatile("dsb ish" : : : "memory");

    for (uint64_t current = address & ~(instruction_line_size - 1);
         current < end; current += instruction_line_size)
        asm volatile("ic ivau, %0" : : "r"(current) : "memory");
    asm volatile("dsb ish\nisb" : : : "memory");
}

static void print_entry_name(const MANIFEST_ENTRY *entry)
{
    uint32_t remaining = entry->name_len;
    const char *current = entry->name;
    while (remaining != 0) {
        int chunk = remaining > 1024 ? 1024 : (int)remaining;
        kshim_console_write(current, chunk);
        current += chunk;
        remaining -= (uint32_t)chunk;
    }
}

static uint32_t select_image(const MANIFEST_VIEW *view, uint64_t fdt)
{
    uint32_t indexes[MANIFEST_MAX_ENTRIES];
    char labels[MANIFEST_MAX_ENTRIES][96];
    const char *names[MANIFEST_MAX_ENTRIES];
    size_t count = 0, initial = 0, selected = 0;
    for (uint32_t i = 0; i + 1 < view->manifest->header.entry_count; ++i) {
        const MANIFEST_ENTRY *entry = view->entries[i];
        if (!(entry->flags & MF_ENTRY_FLAG_EXECUTABLE)) continue;
        size_t n = entry->name_len < sizeof(labels[0]) ? entry->name_len : sizeof(labels[0]) - 1;
        memcpy(labels[count], entry->name, n); labels[count][n] = 0;
        indexes[count] = i; names[count] = labels[count];
        if (i == view->manifest->header.default_entry) initial = count;
        ++count;
    }
    if (kshim_platform_select(fdt, names, count, initial, view->manifest->header.timeout_ms, &selected)) {
        printf("Unsafe UI shutdown; refusing payload handoff\n");
        for (;;) (void)delay(100000);
    }
    return indexes[selected];
}

extern void _arm64_mmu_setup(void);

/* Last operation before leaving the runtime, after all boot diagnostics. */
static void prepare_boot(void)
{
    if (kshim_platform_prepare_handoff() || kshim_console_quiesce()) {
        printf("Device shutdown incomplete; refusing payload handoff\n");
        for (;;) (void)delay(100000);
    }
}

int kshim_main(
    uint64_t device_tree_address,
    uint64_t kernel_arg1,
    uint64_t kernel_arg2,
    uint64_t kernel_arg3,
    uint64_t linux_base,
    uint64_t manifest_address,
    uint64_t manifest_size)
{
#if KSHIM_DIAGNOSTIC_BASE_ONLY
    /* Diagnostic only: hand off to the packed Linux base image immediately,
     * before any DT, console, or peripheral work.  No menu is shown and no
     * peripherals are probed.  arm64_boot_image is declared noreturn and
     * performs the existing handoff (DAIF/timer masks). */
    if (linux_base)
        arm64_boot_image(linux_base + 4, device_tree_address,
                         kernel_arg1, kernel_arg2, kernel_arg3);
    for (;;)
        asm volatile("wfe");
#endif
    const void *runtime_fdt = (const void *)(uintptr_t)device_tree_address;
    if (!runtime_fdt || dt_validate(runtime_fdt, fdt_totalsize(runtime_fdt))) {
        runtime_fdt = NULL;
        device_tree_address = 0;
    }
    /* Early, read-only ramoops capture from the validated runtime DT, taken
     * exactly once before the memory snapshot/console/resources work.  The
     * layout starts zeroed (valid == false) so a failed capture never leaves a
     * consumable layout behind; the returned status is preserved for the
     * normal-Linux bind decision and the recovery-only entry below. */
    tb323fu_ramoops_layout ramoops_layout = {0};
    int ramoops_capture_status = tb323fu_ramoops_capture(
        runtime_fdt,
        runtime_fdt ? fdt_totalsize(runtime_fdt) : 0,
        &ramoops_layout);
#if KSHIM_RAMOOPS_RECOVERY_ONLY
    /* Recovery-only entry (compile-time): runs immediately after runtime DT
     * validation and the read-only capture and before the memory snapshot,
     * console, resources or UI exist.  No menu, no Linux, no prepare_boot and
     * no new MMIO; the only RAM traffic is the already-validated runtime FDT
     * edited by the export helper plus a physical console read that is issued
     * only when the capture succeeded.  The handoff reuses the BASE_ONLY
     * parameters.  A missing runtime FDT skips the report and never reads RAM;
     * a missing base image wfe-waits. */
    if (runtime_fdt) {
        const volatile uint8_t *region = NULL;
        size_t region_len = 0;
        if (ramoops_capture_status == TB323FU_RAMOOPS_OK) {
            region = (const volatile uint8_t *)(uintptr_t)ramoops_layout.base;
            region_len = (size_t)ramoops_layout.size;
        }
        (void)tb323fu_ramoops_export(
            (void *)runtime_fdt, region, region_len, &ramoops_layout,
            ramoops_capture_status,
            "kshim.diag_candidate=m2-pc1-20261006");
    }
    if (linux_base)
        arm64_boot_image(linux_base + 4, device_tree_address,
                         kernel_arg1, kernel_arg2, kernel_arg3);
    for (;;)
        asm volatile("wfe");
#endif
    /* Capture the packed runtime-memory snapshot exactly once, after runtime
     * DT validation and before console/resources/UI.  A NULL runtime FDT
     * (equivalent to dt_validate(NULL, 0)) is a clean failure: the snapshot is
     * only consumed by dt_memory_apply and no code path uses an invalid
     * snapshot or output. */
    int memory_status = dt_memory_capture(
        runtime_fdt,
        runtime_fdt ? fdt_totalsize(runtime_fdt) : 0,
        RuntimeMemorySnapshot,
        sizeof(RuntimeMemorySnapshot));
    (void)kshim_console_init(runtime_fdt);
    if (runtime_fdt && kshim_resources_init(runtime_fdt, fdt_totalsize(runtime_fdt))) {
        printf("Invalid runtime device resources\n");
        for (;;) (void)delay(100000);
    }
#if KSHIM_DIAGNOSTIC_RESOURCES_ONLY
    /* Diagnostic checkpoint after DT/console/resource probing: optional
     * resources may be absent and no menu is shown.  A valid runtime FDT and
     * base image are required, and the console must quiesce successfully
     * before the noreturn handoff to the packed base image. */
    if (runtime_fdt && linux_base) {
        const struct kshim_resources *r = kshim_resources_get();
        char report[81];
        int n;
#if CONFIG_KSHIM_FRAMEBUFFER
        n = kshim_splash_trace_report(kshim_splash_trace_get(), r->splash_status,
                                      report, sizeof(report));
#else
        n = snprintf(report, sizeof(report),
            "r14:s=%d:n=0:o=0:v=0,0,0,0,0", r->splash_status);
#endif
        if (n > 0 && n < (int)sizeof(report))
            (void)dt_probe_report((void *)runtime_fdt, report);
#if defined(KSHIM_DIAGNOSTIC_MENU_MMU) && KSHIM_DIAGNOSTIC_MENU_MMU
        {
            uint64_t raw_el = 0, sctlr = 0;
            struct dt_range reservations[128];
            bool no_map[128];
            size_t reserved_count = 0;
            int reserved_status;
            int config_status = -4;
#if defined(KSHIM_DIAGNOSTIC_MMU_CYCLE) && KSHIM_DIAGNOSTIC_MMU_CYCLE
            int cycle_enable = -5;
            int cycle_active = -1;
            int cycle_prepare = -5;
            int cycle_after = -1;
            uint64_t cycle_sctlr = 0;
#endif

            __asm__ volatile("mrs %0, CurrentEL" : "=r"(raw_el));
            if (raw_el == 4)
                __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
            else if (raw_el == 8)
                __asm__ volatile("mrs %0, sctlr_el2" : "=r"(sctlr));

            reserved_status = dt_reservations(runtime_fdt, reservations,
                                              no_map, 128,
                                              &reserved_count);

            if (raw_el == 4 || raw_el == 8) {
                if (sctlr & 1)
                    config_status = -3;
                else if (r->splash_status)
                    config_status = -2;
                else
                    config_status = kshim_mmu_configure_default(runtime_fdt);
            }

#if defined(KSHIM_DIAGNOSTIC_MMU_CYCLE) && KSHIM_DIAGNOSTIC_MMU_CYCLE
            /* Enable/disable cycle only: no FDT write, no print, no
             * framebuffer/keys/SPMI/menu/interrupt changes between enable
             * and prepare.  Prepare is called even if enable returned an
             * error, since a configured state may still need cleanup. */
            if (config_status == 0) {
                cycle_enable = kshim_mmu_enable_current_cpu();
                cycle_active = kshim_mmu_is_enabled();
                cycle_prepare = kshim_mmu_prepare_handoff();
                cycle_after = kshim_mmu_is_enabled();
                if (raw_el == 4)
                    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(cycle_sctlr));
                else if (raw_el == 8)
                    __asm__ volatile("mrs %0, sctlr_el2" : "=r"(cycle_sctlr));
            }
#endif

            n = snprintf(report, sizeof(report),
                         "r22:el=%u:ctl=%llx:cfg=%d:map=%u",
                         (unsigned)(raw_el >> 2),
                         (unsigned long long)sctlr, config_status,
                         (unsigned)r->mapping_count);
            if (n > 0 && n < (int)sizeof(report))
                (void)dt_probe_report_slot((void *)runtime_fdt, 0, report);

            n = snprintf(report, sizeof(report),
                         "r22:root=%llx:frq=%llu:fb=%llx:size=%llu",
                         (unsigned long long)kshim_mmu_root_table(),
                         (unsigned long long)arm64_read_cntfrq(),
                         (unsigned long long)r->framebuffer.render_address,
                         (unsigned long long)r->framebuffer.buffer_size);
            if (n > 0 && n < (int)sizeof(report))
                (void)dt_probe_report_slot((void *)runtime_fdt, 1, report);

            n = snprintf(report, sizeof(report),
                         "r22:rsv=%d:n=%u:fdt=%llx:sz=%u",
                         reserved_status, (unsigned)reserved_count,
                         (unsigned long long)(uintptr_t)runtime_fdt,
                         (unsigned)fdt_totalsize(runtime_fdt));
            if (n > 0 && n < (int)sizeof(report))
                (void)dt_probe_report_slot((void *)runtime_fdt, 2, report);

            n = snprintf(report, sizeof(report),
                         "r22:mmu=%d:spmi=%d:count=%u:splash=%d",
                         kshim_mmu_is_enabled(), r->spmi_status,
                         (unsigned)r->spmi_count, r->splash_status);
            if (n > 0 && n < (int)sizeof(report))
                (void)dt_probe_report_slot((void *)runtime_fdt, 3, report);

#if defined(KSHIM_DIAGNOSTIC_MMU_CYCLE) && KSHIM_DIAGNOSTIC_MMU_CYCLE
            n = snprintf(report, sizeof(report),
                         "r23:on=%d:m=%d:off=%d:m0=%d:ctl=%llx",
                         cycle_enable, cycle_active, cycle_prepare,
                         cycle_after, (unsigned long long)cycle_sctlr);
            if (n > 0 && n < (int)sizeof(report))
                (void)dt_probe_report_slot((void *)runtime_fdt, 4, report);
#endif
        }
#else
#if CONFIG_KSHIM_FRAMEBUFFER
        {
            unsigned slot;
            for (slot = 0; slot < 9; slot++) {
                n = kshim_splash_snapshot_report(
                    kshim_splash_snapshot_get(), slot, report,
                    sizeof(report));
                if (n > 0 && n < (int)sizeof(report))
                    (void)dt_probe_report_slot((void *)runtime_fdt, slot,
                                               report);
            }
        }
#endif
#endif
    }
    if (runtime_fdt && linux_base && kshim_console_quiesce() == 0) {
        arm64_boot_image(linux_base + 4, device_tree_address,
                         kernel_arg1, kernel_arg2, kernel_arg3);
    }
    for (;;)
        asm volatile("wfe");
#endif
#if CONFIG_ENABLE_BANNER
    printf(CONFIG_BANNER "\n");
#endif
#if KSHIM_STANDALONE
    return kshim_platform_main(device_tree_address, kernel_arg1, kernel_arg2, kernel_arg3);
#endif
    if (linux_base != 0) {
        MANIFEST_VIEW view = {0};
        if (!get_manifest(
                linux_base, manifest_address, manifest_size, &view)) {
            dt_probe_report((void *)runtime_fdt, "m14:manifest=bad");
            printf("Manifest not found, booting base image\n");
            prepare_boot();
            arm64_boot_linux(linux_base, device_tree_address, 4);
        }

        uint32_t selected_index = select_image(&view, device_tree_address);
        const MANIFEST_ENTRY *entry = view.entries[selected_index];
        if ((entry->flags & MF_ENTRY_FLAG_BASE_IMAGE) &&
            !(entry->flags & MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB)) {
            dt_probe_report((void *)runtime_fdt, "m14:base=selected");
        }
        uint64_t selected_device_tree = device_tree_address;
        if (entry->flags & MF_ENTRY_FLAG_HAS_DEVICE_TREE_BLOB) {
            const MANIFEST_ENTRY *dtb =
                view.entries[entry->device_tree_blob_entry];
            uint64_t payload_device_tree = linux_base + dtb->offset;
            selected_device_tree = payload_device_tree;

            /* Every entry that selects a custom DTB automatically uses the
             * new snapshot/overlay API -- no Linux-only opt-in property and
             * no kshim,inherit-runtime-memory gate.  Entries without a custom
             * DTB keep the original bootloader DT pointer. */
            int status = memory_status;
            if (status == 0) {
                status = dt_memory_apply(
                    RuntimeMemorySnapshot,
                    sizeof(RuntimeMemorySnapshot),
                    (const void *)(uintptr_t)payload_device_tree,
                    (size_t)dtb->size,
                    HandoffDtb,
                    sizeof(HandoffDtb),
                    MemoryOverlay,
                    sizeof(MemoryOverlay));
            }

            if (status == 0) {
                /* Use the HandoffDtb selected pointer only after successful apply. */
                selected_device_tree = (uint64_t)(uintptr_t)HandoffDtb;

                if (entry->type == MF_ENTRY_TYPE_LINUX) {
                    /* Only a successful early capture may add a persistent
                     * backend.  bind() is non-transactional, so a failed bind
                     * sets the original failure status and falls through to the
                     * existing Android base fallback instead of consuming the
                     * failed DT.  A failed capture reports the missing backend
                     * once and leaves the original Linux path untouched. */
                    if (ramoops_capture_status == TB323FU_RAMOOPS_OK) {
                        int bind_status = tb323fu_ramoops_bind(
                            HandoffDtb, sizeof(HandoffDtb),
                            &ramoops_layout);
                        if (bind_status != TB323FU_RAMOOPS_OK)
                            status = bind_status;
                    } else {
                        printf("No persistent console backend (capture %d); "
                               "continuing Linux path\n",
                               ramoops_capture_status);
                    }
                    /* Retain the EXISTING builtin-initramfs profile: drop any
                     * inherited linux,initrd-start/end.  A missing /chosen
                     * node or missing properties are ignored; other negative
                     * errors propagate. */
                    void *blob = (void *)(uintptr_t)HandoffDtb;
                    int chosen = fdt_path_offset(blob, "/chosen");
                    if (chosen == -FDT_ERR_NOTFOUND) {
                        chosen = -1;
                    } else if (chosen < 0) {
                        status = chosen;
                    }
                    if (status == 0 && chosen >= 0) {
                        int drop =
                            fdt_delprop(blob, chosen, "linux,initrd-start");
                        if (drop != 0 && drop != -FDT_ERR_NOTFOUND)
                            status = drop;
                    }
                    if (status == 0 && chosen >= 0) {
                        /* Refresh the /chosen offset before the second delete:
                         * the first deletion may have moved it. */
                        chosen = fdt_path_offset(blob, "/chosen");
                        if (chosen == -FDT_ERR_NOTFOUND) {
                            chosen = -1;
                        } else if (chosen < 0) {
                            status = chosen;
                        } else {
                            int drop =
                                fdt_delprop(blob, chosen, "linux,initrd-end");
                            if (drop != 0 && drop != -FDT_ERR_NOTFOUND)
                                status = drop;
                        }
                    }
                    if (status == 0) {
                        struct kshim_resources *r = kshim_resources_get();
                        status = dt_simplefb_apply(blob, sizeof(HandoffDtb),
                                                   r && r->splash_status == 0
                                                       ? &r->framebuffer
                                                       : NULL);
                    }
                    if (status == 0) {
                        /* Re-pack the modified DT, then validate the result. */
                        int packed = fdt_pack(blob);
                        if (packed != 0)
                            status = packed;
                        else
                            status = dt_validate(blob, sizeof(HandoffDtb));
                    }
                }
            }

#if defined(KSHIM_DIAGNOSTIC_MEMORY_ONLY) && KSHIM_DIAGNOSTIC_MEMORY_ONLY
            /* Diagnostic-only kernel probe (M30).
             * (KSHIM_DIAGNOSTIC_MEMORY_ONLY=1; undefined by default => OFF.)
             * Inspects the normalized Handoff DT /memory.reg no-map union,
             * the Linux payload header and the patched Image entry, then
             * actually EXECUTES the kernel through __cpu_setup and
             * __enable_mmu via arm64_diag_head_tail(): it branches into the
             * PATCHED Linux Image and runs the full early kernel CPU path -
             * the feature override, KASLR and, when available, LPA2, plus the
             * map and relocate of TTBR1 - through the actual start_kernel
             * into setup_arch, up to the M30 checkpoint at Image+0x1614c3c,
             * immediately after the inlined setup_machine_fdt() and before
             * jump_label_init / parse_early_param / the MMIO setup and idmap
             * removal.  Success requires the current kernel PC in a high VA
             * (bit 63 set), the captured SCTLR_EL1.M bit set, and nonzero
             * copied memblock count and bytes.  There is no printk return
             * condition in M30.  The inline 128-byte stub there restores the
             * caller's x25/x26 from SP+224 and x27/x28 from SP+240 (the
             * start_kernel 144-byte frame plus the setup_arch 160-byte
             * frame), reads the published kimage_voffset, computes
             * physical_text = VA_text - kimage_voffset, and BLs the real
             * kernel DC-clean helper over the static Image range
             * [_stext 0x10000, _end 0x1c40000) to flush all prior static
             * dirty data.  No writes occur after the clean with the MMU on;
             * it then switches to the physical alias 0x1614ca0 while the MMU
             * is on, followed by the known MMU/cache-off sequence
             * (SCTLR_EL1 0x30500800), barriers and a BR to the physical
             * callback.  No x0 physical assumption is made.  The seed gate
             * still ensures the FDT is only read (and rejects a present
             * rng-seed).  It does NOT run reserved-region processing,
             * allocations, full Linux, USB or tuning proof.  A successful
             * probe uses the MMU-off callback to export counts/bytes before
             * Android; rejected probes fall back through the common report
             * path.  The normal Linux handoff is
             * disabled in this test build.  The fallback below is unchanged. */
            if (status == 0) {
                const void *fdt = (const void *)HandoffDtb;
                char report[81];
                int rn = 0;
                int gate_ok = 0;
                uint64_t exec_kbase = 0;

                if (entry != NULL &&
                    entry->type == MF_ENTRY_TYPE_LINUX &&
                    entry->size >= 64u &&
                    (uint64_t)entry->offset <= (UINT64_MAX - (uint64_t)linux_base)) {
                    uintptr_t source =
                        (uintptr_t)((uint64_t)linux_base + entry->offset);
                    const uint8_t *img = (const uint8_t *)source;
                    uint32_t magic = (uint32_t)img[56]
                                   | ((uint32_t)img[57] << 8)
                                   | ((uint32_t)img[58] << 16)
                                   | ((uint32_t)img[59] << 24);
                    uint64_t image_size = (uint64_t)img[16]
                                        | ((uint64_t)img[17] << 8)
                                        | ((uint64_t)img[18] << 16)
                                        | ((uint64_t)img[19] << 24)
                                        | ((uint64_t)img[20] << 32)
                                        | ((uint64_t)img[21] << 40)
                                        | ((uint64_t)img[22] << 48)
                                        | ((uint64_t)img[23] << 56);

                    if (magic == 0x644d5241u && image_size > 0u) {
                        uint64_t kbase = (entry->flags & MF_ENTRY_FLAG_COPY)
                                       ? (uint64_t)entry->load_address
                                       : (uint64_t)source;
                        uint64_t dsize = (uint64_t)fdt_totalsize(HandoffDtb);
                        struct diag_placement kp, dp;
                        int krc, drc;

                        krc = diag_check_span(fdt, kbase, image_size, &kp);
                        if (dsize > 0u &&
                            dsize <= (uint64_t)sizeof(HandoffDtb)) {
                            drc = diag_check_span(fdt,
                                                  (uint64_t)(uintptr_t)HandoffDtb,
                                                  dsize, &dp);
                        } else {
                            drc = -1;
                        }

                        const char *rej = NULL;
                        if (entry->flags & MF_ENTRY_FLAG_COPY) {
                            rej = "placement";
                        } else if (entry->entry_offset != 0u) {
                            rej = "placement";
                        } else if ((kbase & 0x1fffffull) != 0ull) {
                            rej = "placement";
                        } else if (krc != 0 || drc != 0) {
                            rej = "placement";
                        } else if (!kp.covered || !dp.covered) {
                            rej = "placement";
                        } else if (kp.nomap_overlaps != 0u ||
                                   kp.header_overlaps != 0u ||
                                   dp.nomap_overlaps != 0u ||
                                   dp.header_overlaps != 0u) {
                            rej = "placement";
                        } else if (memcmp(img + 32, "KSHM30DT", 8) != 0) {
                            rej = "marker";
                        } else {
                            uint64_t off = (uint64_t)img[40]
                                         | ((uint64_t)img[41] << 8)
                                         | ((uint64_t)img[42] << 16)
                                         | ((uint64_t)img[43] << 24)
                                         | ((uint64_t)img[44] << 32)
                                         | ((uint64_t)img[45] << 40)
                                         | ((uint64_t)img[46] << 48)
                                         | ((uint64_t)img[47] << 56);
                            if (off != 0x1614c3cull) {
                                rej = "marker";
                            } else if (entry->size < off + 128u || image_size < off + 128u) {
                                rej = "marker";
                            } else {
                                const uint8_t *w = img + off;
                                static const uint8_t expected_stub[128] = {
                                    0x18, 0x10, 0x38, 0xd5, 0xea, 0xff, 0xff, 0x10,
                                    0x5f, 0x01, 0x41, 0xf2, 0x18, 0x13, 0x9f, 0x9a,
                                    0xf9, 0x6b, 0x4e, 0xa9, 0xfb, 0x73, 0x4f, 0xa9,
                                    0x14, 0x25, 0x00, 0xd0, 0x94, 0x62, 0x26, 0x91,
                                    0x95, 0x0a, 0x40, 0xf9, 0x96, 0x12, 0x40, 0xf9,
                                    0xbf, 0x02, 0x00, 0xf1, 0x18, 0x13, 0x9f, 0x9a,
                                    0xdf, 0x02, 0x00, 0xf1, 0x18, 0x13, 0x9f, 0x9a,
                                    0xb3, 0xfa, 0xff, 0x90, 0x73, 0x02, 0x40, 0xf9,
                                    0x69, 0x4f, 0xff, 0x90, 0x33, 0x01, 0x13, 0xcb,
                                    0xe0, 0x4f, 0xff, 0x90, 0x61, 0x31, 0x00, 0x90,
                                    0xf9, 0x7a, 0xa8, 0x97, 0x09, 0x94, 0x89, 0xd2,
                                    0x29, 0x2c, 0xa0, 0xf2, 0x69, 0x02, 0x09, 0x8b,
                                    0x20, 0x01, 0x1f, 0xd6, 0x09, 0x00, 0x81, 0xd2,
                                    0x09, 0x0a, 0xa6, 0xf2, 0x9f, 0x3f, 0x03, 0xd5,
                                    0xdf, 0x3f, 0x03, 0xd5, 0x09, 0x10, 0x18, 0xd5,
                                    0xdf, 0x3f, 0x03, 0xd5, 0x60, 0x03, 0x1f, 0xd6,
                                };
                                if (memcmp(w, expected_stub, 128) != 0) {
                                    rej = "marker";
                                } else {
                                    uint64_t el;
                                    __asm__ volatile("mrs %0, CurrentEL"
                                                     : "=r"(el));
                                    if (el != 4u) {
                                        rej = "cpu";
                                    } else {
                                        uint64_t sctlr;
                                        __asm__ volatile("mrs %0, sctlr_el1"
                                                         : "=r"(sctlr));
                                        if ((sctlr & ((1ull << 0) |
                                                      (1ull << 2) |
                                                      (1ull << 25))) != 0ull) {
                                            rej = "cpu";
                                        } else {
                                            /* Seed gate: the source DT must not
                                             * carry a kaslr-seed, so the target
                                             * FDT stays read-only through
                                             * kaslr_early_init and the memory
                                             * overlay never imports it.  The same
                                             * applies to rng-seed; note that
                                             * early_init_dt_scan_chosen() may NOP
                                             * a rng-seed, so the read-only DT is
                                             * still required for cache cleanup.
                                             * Only absent /chosen kaslr-seed and
                                             * rng-seed (with a /chosen that
                                             * resolves) arms the probe; seed
                                             * rejection is diagnostic-only and
                                             * falls back through the common report
                                             * path. */
                                            int chosen = fdt_path_offset(
                                                (const void *)HandoffDtb,
                                                "/chosen");
                                            int slen = 0;
                                            int rlen = 0;
                                            const void *seed = (chosen >= 0)
                                                ? fdt_getprop(
                                                      (const void *)HandoffDtb,
                                                      chosen, "kaslr-seed",
                                                      &slen)
                                                : NULL;
                                            const void *rseed = (chosen >= 0)
                                                ? fdt_getprop(
                                                      (const void *)HandoffDtb,
                                                      chosen, "rng-seed",
                                                      &rlen)
                                                : NULL;
                                            if (chosen < 0 || seed != NULL ||
                                                slen != -FDT_ERR_NOTFOUND ||
                                                rseed != NULL ||
                                                rlen != -FDT_ERR_NOTFOUND) {
                                                rej = "seed";
                                            } else {
                                                gate_ok = 1;
                                                exec_kbase = kbase;
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        if (rej != NULL) {
                            rn = snprintf(report, sizeof(report),
                                          "m30:reject=%s", rej);
                        }
                    } else {
                        rn = snprintf(report, sizeof(report),
                                      "m30:reject=header");
                    }
                } else {
                    rn = snprintf(report, sizeof(report),
                                  "m30:reject=header");
                }

                if (gate_ok) {
                    /* M30 gate: this branch never falls through to the common
                     * reject report below.  Install the incomplete record, resolve
                     * the single DT flag byte, then either arm the tail
                     * (noreturn) or fall back to the original Android boot.  The
                     * source-DT /chosen lookup was already checked by the seed
                     * gate; the report here records the kernel high-VA entry, and
                     * the callback publishes the copied memblock count/total. */
                    const char *flag = NULL;
                    uint64_t incoming_sp;

                    __asm__ volatile("mrs %0, spsel" : "=r"(incoming_sp));
                    rn = snprintf(report, sizeof(report),
                                  "m30:dt=0:n=0000000000000000:b=0000000000000000:sp=%llu",
                                  (unsigned long long)(incoming_sp & 1ull));

                    if (rn > 0 && rn < (int)sizeof(report) &&
                        dt_probe_report((void *)runtime_fdt, report) == 0) {
                        static const char m30_pre[] =
                            "androidboot.kshim_probe=m30:dt=";
                        static const char m30_tok[] =
                            "androidboot.kshim_probe=m30:dt=0:n=0000000000000000:b=0000000000000000:sp=";
                        int chosen = fdt_path_offset((const void *)runtime_fdt,
                                                     "/chosen");
                        int balen = 0;
                        const char *ba = (chosen >= 0)
                            ? (const char *)fdt_getprop((const void *)runtime_fdt,
                                                        chosen, "bootargs",
                                                        &balen)
                            : NULL;
                        if (ba != NULL && balen > 0) {
                            int toklen = (int)(sizeof(m30_tok) - 1);
                            int i;
                            int cnt = 0;
                            for (i = 0; i + toklen <= balen; i++) {
                                int ve;
                                if (i != 0 && ba[i - 1] != ' ') {
                                    continue;
                                }
                                if (memcmp(ba + i, m30_tok, toklen) != 0) {
                                    continue;
                                }
                                ve = i +
                                     (int)(sizeof("androidboot.kshim_probe=") - 1) +
                                     80;
                                if (ve >= balen) {
                                    continue;
                                }
                                if (ba[ve] == '\0' || ba[ve] == ' ') {
                                    cnt++;
                                    if (cnt == 1) {
                                        flag = ba + i +
                                               (int)(sizeof(m30_pre) - 1);
                                    }
                                }
                            }
                            if (cnt != 1) {
                                flag = NULL;
                            }
                            if (flag != NULL && flag[0] != '0') {
                                flag = NULL;
                            }
                        }
                    }

                    if (flag != NULL) {
                        printf("Memory checkpoint m30:armed\n");
                        prepare_boot();
                        arm64_diag_head_tail(exec_kbase,
                                             (uint64_t)HandoffDtb,
                                             (uint64_t)linux_base + 4,
                                             (uint64_t)device_tree_address,
                                             (uintptr_t)flag);
                        /* arm64_diag_head_tail() is __attribute__((noreturn)). */
                    }

                    printf("Memory checkpoint m30:reject=report\n");
                    prepare_boot();
                    arm64_boot_linux(linux_base, device_tree_address, 4);
                } else {
                    if (rn > 0 && rn < (int)sizeof(report)) {
                        printf("Memory checkpoint %s\n", report);
                        dt_probe_report((void *)runtime_fdt, report);
                    }

                    prepare_boot();
                    arm64_boot_linux(linux_base, device_tree_address, 4);
                }
            }
#endif

            if (status != 0) {
                char report[81];
                int n = snprintf(report, sizeof(report),
                                 "m15:c=%d:a=%d:d=%s",
                                 memory_status, status,
                                 dt_handoff_failure_detail());
                if (n > 0 && n < (int)sizeof(report))
                    dt_probe_report((void *)runtime_fdt, report);
                printf("Memory handoff failed; booting base image\n");
                prepare_boot();
                arm64_boot_linux(linux_base, device_tree_address, 4);
            }
        }
        if (entry->flags & MF_ENTRY_FLAG_COPY) {
            uint64_t source_address = linux_base + entry->offset;
            printf("Copying ");
            print_entry_name(entry);
            printf(" from 0x%lx to 0x%lx (0x%lx bytes)\n", source_address,
                   entry->load_address, entry->size);
            memmove((void *)entry->load_address, (const void *)source_address,
                    entry->size);
            sync_image_cache(entry->load_address, entry->size);
            prepare_boot();
            if (entry->type == MF_ENTRY_TYPE_LINUX) {
                arm64_boot_linux(entry->load_address, selected_device_tree,
                                 entry->entry_offset);
            }
            arm64_boot_image(entry->load_address + entry->entry_offset,
                             selected_device_tree, kernel_arg1, kernel_arg2,
                             kernel_arg3);
        }

        if (entry->type == MF_ENTRY_TYPE_LINUX) {
            uint64_t entry_offset = entry->flags & MF_ENTRY_FLAG_BASE_IMAGE
                ? 4 : entry->entry_offset;
            printf("Booting ");
            print_entry_name(entry);
            printf(" at 0x%lx with DTB at 0x%lx\n",
                   linux_base + entry->offset, selected_device_tree);
            prepare_boot();
            arm64_boot_linux(linux_base + entry->offset,
                             selected_device_tree, entry_offset);
        }
        prepare_boot();
        arm64_boot_image(linux_base + entry->offset + entry->entry_offset,
                         selected_device_tree, kernel_arg1, kernel_arg2,
                         kernel_arg3);
    }
    return kshim_platform_main(device_tree_address, kernel_arg1, kernel_arg2, kernel_arg3);
}
