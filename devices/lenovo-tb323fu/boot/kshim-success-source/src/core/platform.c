/* SPDX-License-Identifier: MIT */
#include <kshim.h>
#include <platform.h>
#include <qcom_dt.h>
#include <smp.h>
#include <smp_selftest.h>
#include <psci.h>
#include <kshim_mmu.h>
#include <st_ftm5.h>
#include <st_ftm4.h>
#include <kshim_lvgl_os.h>
#include <kshim_console.h>
#include <timer.h>
#include <config.h>
#include <framebuffer.h>
#include <lvgl_port.h>
#include <qcom_key_platform.h>
#include <qcom_keys.h>
#include <qcom_keys_fdt.h>
#include <uefi_boot.h>
#include <dt.h>

__attribute__((weak)) int kshim_menu_activate(kshim_menu_action_t action)
{ (void)action; return -1; }
static int SmpInitialized;
static QcomKeys Keys;
static int build_keys(const void *fdt)
{
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    (void)fdt; return -1;
#elif CONFIG_QCOM_KEYS
    QcomKeyDescriptor descriptors[QCOM_KEYS_MAX];
    size_t count = 0;
    if (!fdt || QcomKeysBuildFromDeviceTree(fdt, descriptors, QCOM_KEYS_MAX, &count) || !count)
        return -1;
    int status = QcomKeysInitEx(&Keys, descriptors, count, kshim_qcom_key_read32,
                                kshim_qcom_key_read32_status, NULL);
    QcomKeysSetDebounce(&Keys, CONFIG_QCOM_KEY_DEBOUNCE_SAMPLES);
    return status;
#else
    (void)fdt; return -1;
#endif
}
#if CONFIG_KSHIM_FRAMEBUFFER && CONFIG_KSHIM_LVGL
static kshim_framebuffer_t Framebuffer;
static kshim_lvgl_t Lvgl;
static uint64_t LastTime;
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
static int UiInit = -9;
static uint32_t UiFrames;
#if CONFIG_KSHIM_DIAGNOSTIC_KEYS_DESCRIPTORS_ONLY
static int UiKeysParse = -9;
static int UiKeysInit = -9;
static int UiKeysMmu = -9;
static uint64_t UiKeysMs;
static size_t UiKeysCount;
static QcomKeyDescriptor UiKeyDescriptors[QCOM_KEYS_MAX];
#if CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
static int UiGpioStatus = -9, UiGpioMmu = -9;
static uint32_t UiGpioValue, UiGpioReads;
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
static int UiSpmiCoreStatus = -9, UiSpmiCoreMmu = -9;
static uint32_t UiSpmiCoreRaw, UiSpmiCoreReads;
static uint32_t UiSpmiCoreCount, UiSpmiCoreController;
static uint64_t UiSpmiCoreAddress;
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
static int UiPonStatus = -9, UiPonMmu = -9, UiPonController = -1, UiPonBus = -1, UiPonSid = -1, UiPonInit = 0;
static uint32_t UiPonValue, UiPonReads, UiPonRegister;
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_MENU_KEYS && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ
static int UiInputStatus = -9, UiInputEnabled = 0, UiInputMmu = -9;
static uint32_t UiInputGpioReads, UiInputPonReads, UiInputErrors;
static int ui_menu_read32_status(void *context, uint64_t address, uint32_t *value)
{
    UiInputMmu = kshim_mmu_is_enabled();
    if (QCOM_KEY_IS_SPMI_TOKEN(address)) ++UiInputPonReads;
    else ++UiInputGpioReads;
    int status = kshim_qcom_key_read32_status(context, address, value);
    if (status) ++UiInputErrors;
    return status;
}
#endif
static void ui_prepare_key_descriptors(const void *fdt)
{
    UiKeysParse = -9;
    UiKeysInit = -9;
    UiKeysCount = 0;
    UiKeysMmu = kshim_mmu_is_enabled();
    uint64_t start = get_current_time();
    dt_parent_index_begin(fdt);
    UiKeysParse = QcomKeysBuildFromDeviceTree(fdt, UiKeyDescriptors, QCOM_KEYS_MAX, &UiKeysCount);
    dt_parent_index_end();
    if (UiKeysParse == 0 && UiKeysCount > 0 && UiKeysCount <= QCOM_KEYS_MAX)
        UiKeysInit = QcomKeysInitEx(&Keys, UiKeyDescriptors, UiKeysCount, NULL, NULL, NULL);
    UiKeysMs = get_current_time() - start;
#if CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
    UiGpioStatus = -9;
    UiGpioMmu = kshim_mmu_is_enabled();
    UiGpioValue = UINT32_MAX;
    UiGpioReads = 0;
    const QcomKeyDescriptor *gro = NULL;
    size_t gro_count = 0;
    if (UiKeysParse == 0 && UiKeysInit == 0 && UiKeysCount <= QCOM_KEYS_MAX) {
        for (size_t i = 0; i < UiKeysCount; ++i) {
            if (UiKeyDescriptors[i].Code == QCOM_KEYCODE_VOLUMEUP &&
                UiKeyDescriptors[i].Source == QCOM_KEY_SOURCE_GPIO) {
                gro = &UiKeyDescriptors[i];
                ++gro_count;
            }
        }
    }
    if (gro_count == 1 && gro && UiGpioMmu == 1 && !QCOM_KEY_IS_SPMI_TOKEN(gro->Base) &&
        gro->Base != 0 && gro->Base <= UINT64_MAX - gro->RegisterOffset) {
        uint64_t gro_address = gro->Base + gro->RegisterOffset;
        if (gro_address != 0 && (gro_address & 0x3) == 0) {
            UiGpioReads = 1;
            UiGpioStatus = kshim_qcom_key_read32_status(NULL, gro_address, &UiGpioValue);
        }
    }
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
    UiSpmiCoreStatus = -9;
    UiSpmiCoreMmu = kshim_mmu_is_enabled();
    UiSpmiCoreRaw = UINT32_MAX;
    UiSpmiCoreReads = 0;
    UiSpmiCoreCount = 0;
    UiSpmiCoreController = UINT32_MAX;
    UiSpmiCoreAddress = 0;
    struct kshim_resources *sr = kshim_resources_get();
    UiSpmiCoreCount = sr ? sr->spmi_count : 0;
    if (UiSpmiCoreMmu == 1 && UiKeysParse == 0 && UiKeysInit == 0 &&
        UiGpioStatus == 0 && UiGpioReads == 1 && sr != NULL && sr->spmi_status == 0 &&
        UiSpmiCoreCount > 0 && UiSpmiCoreCount <= KSHIM_SPMI_MAX) {
        uint32_t decoded = UINT32_MAX;
        size_t matches = 0;
        for (size_t i = 0; i < UiKeysCount && i < QCOM_KEYS_MAX; ++i) {
            const QcomKeyDescriptor *d = &UiKeyDescriptors[i];
            if (d->Code == QCOM_KEYCODE_POWER && d->Source == QCOM_KEY_SOURCE_PON &&
                QCOM_KEY_IS_SPMI_TOKEN(d->Base)) {
                decoded = QCOM_KEY_SPMI_TOKEN_CONTROLLER(d->Base);
                ++matches;
            }
        }
        if (matches == 1 && decoded < sr->spmi_count) {
            uint64_t base = sr->spmi[decoded].controller.Regions[SPMI_MEMORY_REGION_TYPE_CORE].BaseAddress;
            uint64_t size = sr->spmi[decoded].controller.Regions[SPMI_MEMORY_REGION_TYPE_CORE].Size;
            uint64_t offset = SPMI_PMIC_ARB_REG_VERSION;
            UiSpmiCoreController = decoded;
            if (base != 0 && size >= offset + sizeof(uint32_t) && base <= UINTPTR_MAX - offset) {
                uint64_t addr = base + offset;
                if (addr <= UINTPTR_MAX - 3 && (addr & 0x3) == 0) {
                    UiSpmiCoreAddress = addr;
                    UiSpmiCoreReads = 1;
                    UiSpmiCoreRaw = *(volatile uint32_t *)(uintptr_t)addr;
                    __asm__ volatile("dsb sy" ::: "memory");
                    UiSpmiCoreStatus = 0;
                }
            }
        }
    }
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
    UiPonStatus = -9;
    UiPonMmu = kshim_mmu_is_enabled();
    UiPonValue = UINT32_MAX;
    UiPonReads = 0;
    UiPonRegister = 0;
    UiPonInit = 0;
    UiPonController = -1;
    UiPonBus = -1;
    UiPonSid = -1;
    struct kshim_resources *pr = kshim_resources_get();
    if (UiPonMmu == 1 && UiKeysParse == 0 && UiKeysInit == 0 &&
        UiSpmiCoreStatus == 0 && UiSpmiCoreReads == 1 &&
        pr != NULL && pr->spmi_status == 0 &&
        pr->spmi_count > 0 && pr->spmi_count <= KSHIM_SPMI_MAX &&
        UiSpmiCoreRaw >= UINT32_C(0x80000000) && UiSpmiCoreRaw < UINT32_C(0x80050000)) {
        const QcomKeyDescriptor *pd = NULL;
        size_t pmatches = 0;
        for (size_t i = 0; i < UiKeysCount && i < QCOM_KEYS_MAX; ++i) {
            const QcomKeyDescriptor *d = &UiKeyDescriptors[i];
            if (d->Code == QCOM_KEYCODE_POWER && d->Source == QCOM_KEY_SOURCE_PON &&
                QCOM_KEY_IS_SPMI_TOKEN(d->Base)) {
                pd = d;
                ++pmatches;
            }
        }
        if (pmatches == 1 && pd != NULL) {
            uint32_t ctrl = (uint32_t)QCOM_KEY_SPMI_TOKEN_CONTROLLER(pd->Base);
            uint32_t bus = (uint32_t)QCOM_KEY_SPMI_TOKEN_BUS(pd->Base);
            uint32_t sid = (uint32_t)QCOM_KEY_SPMI_TOKEN_SID(pd->Base);
            uint32_t baseReg = (uint32_t)QCOM_KEY_SPMI_TOKEN_ADDRESS(pd->Base);
            if (!(pd->Base & QCOM_KEY_PMIC_GPIO_FLAG) &&
                ctrl == UiSpmiCoreController && ctrl < pr->spmi_count &&
                !pr->spmi[ctrl].controller.HasInterrupt &&
                sid <= 15 && baseReg <= UINT16_MAX &&
                pd->RegisterOffset <= UINT16_MAX - baseReg) {
                UiPonController = (int)ctrl;
                UiPonBus = (int)bus;
                UiPonSid = (int)sid;
                UiPonRegister = baseReg + pd->RegisterOffset;
                UiPonReads = 1;
                UiPonStatus = kshim_qcom_key_read32_status(NULL, pd->Base + pd->RegisterOffset, &UiPonValue);
                UiPonInit = pr->spmi[ctrl].controller.Initialized ? 1 : 0;
            }
        }
    }
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_MENU_KEYS && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ
    UiInputStatus = -9;
    UiInputEnabled = 0;
    UiInputGpioReads = 0;
    UiInputPonReads = 0;
    UiInputErrors = 0;
    UiInputMmu = kshim_mmu_is_enabled();
    if (UiInputMmu == 1 && UiKeysParse == 0 && UiKeysInit == 0 &&
        UiKeysCount > 0 && UiKeysCount <= QCOM_KEYS_MAX &&
        UiGpioStatus == 0 && UiGpioReads == 1 &&
        UiSpmiCoreStatus == 0 && UiSpmiCoreReads == 1 &&
        UiPonStatus == 0 && UiPonReads == 1 && UiPonInit == 1) {
        UiInputStatus = QcomKeysInitEx(&Keys, UiKeyDescriptors, UiKeysCount,
                                       kshim_qcom_key_read32, ui_menu_read32_status, NULL);
        if (UiInputStatus == 0)
            QcomKeysSetDebounce(&Keys, CONFIG_QCOM_KEY_DEBOUNCE_SAMPLES);
    }
#endif
}
static void ui_report_key_descriptors(void *fdt)
{
    char report[81];
    int n = snprintf(report, sizeof(report),
        "r25:parse=%d:init=%d:n=%u", UiKeysParse, UiKeysInit, (unsigned)UiKeysCount);
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 2, report);
    const uint32_t codes[3] = {QCOM_KEYCODE_VOLUMEUP, QCOM_KEYCODE_VOLUMEDOWN, QCOM_KEYCODE_POWER};
    size_t limit = UiKeysCount < QCOM_KEYS_MAX ? UiKeysCount : QCOM_KEYS_MAX;
    for (size_t slot = 0; slot < 3; ++slot) {
        const QcomKeyDescriptor *found = NULL;
        for (size_t i = 0; i < limit; ++i)
            if (UiKeyDescriptors[i].Code == codes[slot]) { found = &UiKeyDescriptors[i]; break; }
        if (found)
            n = snprintf(report, sizeof(report),
                "r25:k=%u:a=%u:s=%u:b=%llx:o=%x:m=%x:l=%u",
                (unsigned)found->Code, (unsigned)found->Action, (unsigned)found->Source,
                (unsigned long long)found->Base, (unsigned)found->RegisterOffset,
                (unsigned)found->Mask, (unsigned)found->ActiveLow);
        else
            n = snprintf(report, sizeof(report), "r25:k=%u:missing=1", (unsigned)codes[slot]);
        if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 3 + slot, report);
    }
#if CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
    n = snprintf(report, sizeof(report), "r32:rc=%d:v=%u:n=%u:mmu=%d:ctrl=%d:bus=%d:sid=%d:reg=%x:init=%d",
        UiPonStatus, (unsigned)UiPonValue, (unsigned)UiPonReads, UiPonMmu,
        UiPonController, UiPonBus, UiPonSid, (unsigned)UiPonRegister, UiPonInit);
#else
    n = snprintf(report, sizeof(report), "r26:mmu=%d:ms=%llu", UiKeysMmu, (unsigned long long)UiKeysMs);
#endif
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 6, report);
#if CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
#if CONFIG_KSHIM_DIAGNOSTIC_MENU_KEYS && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ
    n = snprintf(report, sizeof(report), "r33:rc=%d:en=%d:g=%u:p=%u:e=%u:mmu=%d:gr=%d:gv=%u:gn=%u",
        UiInputStatus, UiInputEnabled, (unsigned)UiInputGpioReads, (unsigned)UiInputPonReads,
        (unsigned)UiInputErrors, UiInputMmu, UiGpioStatus, (unsigned)UiGpioValue, (unsigned)UiGpioReads);
#else
    n = snprintf(report, sizeof(report), "r27:k=%u:rc=%d:v=%u:n=%u:mmu=%d",
        (unsigned)QCOM_KEYCODE_VOLUMEUP, UiGpioStatus, (unsigned)UiGpioValue,
        (unsigned)UiGpioReads, UiGpioMmu);
#endif
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 7, report);
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ
    n = snprintf(report, sizeof(report), "r30:rc=%d:n=%u:cnt=%u:id=%u:addr=%llx:raw=%x:mmu=%d",
        UiSpmiCoreStatus, (unsigned)UiSpmiCoreReads, (unsigned)UiSpmiCoreCount,
        (unsigned)UiSpmiCoreController, (unsigned long long)UiSpmiCoreAddress,
        UiSpmiCoreRaw, UiSpmiCoreMmu);
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 8, report);
#endif
}
#endif
static void ui_probe_report(void *fdt, int close, size_t selected)
{
    if (kshim_mmu_is_enabled() != 0) return;
    struct kshim_resources *r = kshim_resources_get();
    char report[81];
    int n = snprintf(report, sizeof(report),
        "r24:init=%d:f=%u:close=%d:mmu=%d:sel=%u",
        UiInit, UiFrames, close, kshim_mmu_is_enabled(), (unsigned)selected);
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 0, report);
    n = snprintf(report, sizeof(report),
        "r24:fb=%llx:w=%u:h=%u:st=%u:fmt=%u",
        (unsigned long long)r->framebuffer.render_address,
        (unsigned)r->framebuffer.width, (unsigned)r->framebuffer.height,
        (unsigned)r->framebuffer.stride, (unsigned)r->framebuffer.format);
    if (n > 0 && n < (int)sizeof(report)) dt_probe_report_slot(fdt, 1, report);
#if CONFIG_KSHIM_DIAGNOSTIC_KEYS_DESCRIPTORS_ONLY
    ui_report_key_descriptors(fdt);
#endif
}
#endif
#if CONFIG_KSHIM_TOUCH
static kshim_touch_t Touch;
static struct CrQupContext TouchPlatform;
static kshim_ftm4_t Ftm4;
static kshim_ftm5_t Ftm5;
static int TouchAttempted, TouchReady;
static struct kshim_touch_resource *TouchResource;
static int touch_transfer(void *context, const uint8_t *command, size_t length,
                          uint8_t *response, size_t count)
{
    (void)context;
    struct CrI2cMessage messages[] = {{TouchResource->address, false, (uint8_t *)(uintptr_t)command, length},
                                    {TouchResource->address, true, response, count}};
    return CrGeniI2cTransfer(&TouchPlatform.i2c, messages, count ? 2 : 1, 20000);
}
static void touch_delay(void *context, uint32_t us)
{ (void)context; TouchPlatform.io.delay_us(TouchPlatform.io.context, us); }
static int touch_quiesce(void)
{
    if (!TouchAttempted) return 0;
    int status = TouchResource->generation == 4 ? kshim_ftm4_quiesce(&Ftm4) : kshim_ftm5_quiesce(&Ftm5);
    if (status || CrQupQuiesce(&TouchPlatform)) return -1;
    TouchAttempted = TouchReady = 0;
    return 0;
}
static void start_touch(struct kshim_resources *r)
{
    if (r->touch_status) return;
    TouchResource = &r->touch;
    kshim_touch_config_t cfg = {.source_width = r->touch.width, .source_height = r->touch.height,
        .output_width = Framebuffer.width, .output_height = Framebuffer.height,
        .flip_x = r->touch.flip_x, .flip_y = r->touch.flip_y,
        .emit = kshim_lvgl_touch, .emit_context = &Lvgl};
    int status = kshim_touch_init(&Touch, &cfg);
    if (!status) status = CrQupInit(&TouchPlatform, NULL, &r->touch.qup);
    TouchAttempted = 1;
    if (!status && r->touch.generation == 4) {
        kshim_ftm4_transport_t t = {NULL, touch_transfer, touch_delay};
        status = kshim_ftm4_init(&Ftm4, &t, &Touch, false, false);
    } else if (!status) {
        kshim_ftm5_transport_t t = {NULL, touch_transfer, touch_delay, KSHIM_FTM5_BUS_I2C};
        status = kshim_ftm5_init(&Ftm5, &t, &Touch);
    }
    TouchReady = status == 0;
    printf("Touch: FTM%u status %d\n", r->touch.generation, status);
    if (!TouchReady) { (void)touch_quiesce(); kshim_lvgl_set_status(&Lvgl, "音量键选择，电源键启动"); }
}
#endif
static int start_menu(const void *fdt)
{
    struct kshim_resources *r = kshim_resources_get();
    kshim_framebuffer_config_t config = r->framebuffer;
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    UiInit = -9;
    UiFrames = 0;
#endif
#if CONFIG_KSHIM_QEMU_PLATFORM
    /* Explicit virtual test surface; physical platforms require scanout evidence. */
    config = (kshim_framebuffer_config_t){.render_address = CONFIG_KSHIM_FB_RENDER_ADDRESS,
        .width = CONFIG_KSHIM_FB_WIDTH, .height = CONFIG_KSHIM_FB_HEIGHT,
        .stride = CONFIG_KSHIM_FB_STRIDE, .format = CONFIG_KSHIM_FB_FORMAT,
        .bpp = kshim_fb_format_bpp(CONFIG_KSHIM_FB_FORMAT), .foreground = UINT32_MAX};
    r->framebuffer = config;
#endif
    if (!config.render_address || kshim_fb_init(&Framebuffer, &config)) return -1;
    int keys = build_keys(fdt);
#if CONFIG_KSHIM_SMP
    int status = kshim_smp_init(fdt);
    SmpInitialized = status == 0;
    if (SmpInitialized) status = kshim_smp_start_workers(KSHIM_SMP_DEFAULT_TIMEOUT_US);
    printf("PSCI workers: %u (status %d)\n", (unsigned)kshim_smp_available_count(), status);
#endif
    if (!kshim_mmu_is_enabled() &&
        (kshim_mmu_root_table() || kshim_mmu_configure_default(fdt) == 0))
        (void)kshim_mmu_enable_current_cpu();
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY && CONFIG_KSHIM_DIAGNOSTIC_KEYS_DESCRIPTORS_ONLY
    ui_prepare_key_descriptors(fdt);
#endif
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    (void)keys;
    QcomKeys *menu_keys = NULL;
#if CONFIG_KSHIM_DIAGNOSTIC_KEYS_DESCRIPTORS_ONLY && CONFIG_KSHIM_DIAGNOSTIC_MENU_KEYS && CONFIG_KSHIM_DIAGNOSTIC_GPIO_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_SPMI_CORE_SINGLE_READ && CONFIG_KSHIM_DIAGNOSTIC_PON_SINGLE_READ
    if (UiInputStatus == 0) menu_keys = &Keys;
    UiInputEnabled = menu_keys == &Keys ? 1 : 0;
#endif
#else
    QcomKeys *menu_keys = &Keys;
#endif
    int lvgl_status = kshim_lvgl_init(&Lvgl, &Framebuffer, menu_keys);
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    UiInit = lvgl_status;
#endif
    if (lvgl_status) return -2;
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    kshim_lvgl_set_status(&Lvgl, "音量键选择，电源键启动");
#else
    if (keys) kshim_lvgl_set_status(&Lvgl, "音量键暂不可用");
#endif
#if CONFIG_KSHIM_TOUCH
    start_touch(r);
#endif
    LastTime = get_current_time();
    return 0;
}
static void menu_frame(void)
{
#if CONFIG_KSHIM_TOUCH
    if (TouchReady) {
        int status = CrQupIrq(&TouchPlatform);
        if (status > 0) status = TouchResource->generation == 4 ? kshim_ftm4_poll(&Ftm4) : kshim_ftm5_poll(&Ftm5);
        if (status < 0) {
            TouchReady = 0; kshim_touch_cancel(&Touch);
            printf("Touch: input status %d\n", status);
            kshim_lvgl_set_status(&Lvgl, "音量键选择，电源键启动");
        }
    }
#endif
    uint64_t now = get_current_time(), elapsed = now - LastTime;
    LastTime = now;
    kshim_lvgl_frame(&Lvgl, elapsed > UINT32_MAX ? UINT32_MAX : (uint32_t)elapsed);
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    ++UiFrames;
#endif
}
#endif
int kshim_platform_prepare_handoff(void)
{
#if CONFIG_KSHIM_FRAMEBUFFER && CONFIG_KSHIM_LVGL
#if CONFIG_KSHIM_TOUCH
    TouchReady = 0;
    if (touch_quiesce()) return -1;
#endif
    kshim_lvgl_deinit(&Lvgl);
#if CONFIG_KSHIM_SMP
    if (kshim_lvgl_os_prepare_shutdown()) return -2;
#endif
#endif
    if (SmpInitialized) {
        if (kshim_smp_prepare_handoff(KSHIM_SMP_DEFAULT_TIMEOUT_US)) return -2;
        SmpInitialized = 0;
        return 0;
    }
    return kshim_mmu_prepare_handoff();
}
static int serial_select(const void *fdt, const char *const *names, size_t count,
                          size_t initial, uint32_t timeout, size_t *selected)
{
    int keys_ready = build_keys(fdt) == 0;
    size_t current = initial, numeric = 0;
    printf("\nKShim boot menu\n");
    for (size_t i = 0; i < count; ++i) printf("  %u. %s%s\n", (unsigned)i + 1, names[i], i == initial ? " [default]" : "");
    printf("Select kernel and press Enter: ");
    uint64_t start = get_current_time();
    for (;;) {
        char c;
        if (kshim_console_try_read(&c)) {
            if (c >= '0' && c <= '9') {
                size_t next = numeric * 10 + c - '0';
                if (next <= count) { numeric = next; kshim_console_write(&c, 1); }
            } else if (c == '\r' || c == '\n') {
                if (numeric && numeric <= count) { current = numeric - 1; break; }
                numeric = 0;
            } else if (c == 27) { current = initial; break; }
        }
        if (keys_ready) {
            QcomKeyEvent event;
            if (QcomKeysPoll(&Keys, &event) == QCOM_KEYS_OK && event.Pressed) {
                if (event.Action == QCOM_KEY_ACTION_UP) current = current ? current - 1 : count - 1;
                if (event.Action == QCOM_KEY_ACTION_DOWN) current = (current + 1) % count;
                if (event.Action == QCOM_KEY_ACTION_SELECT) break;
                printf("\n> %s\n", names[current]);
            }
        }
        if (timeout && get_current_time() - start >= timeout) break;
        (void)delay(5000);
    }
    printf("\n"); *selected = current;
    return 0;
}
int kshim_platform_select(uint64_t fdt, const char *const *names, size_t count,
                           size_t initial, uint32_t timeout_ms, size_t *selected)
{
    if (!names || !selected || !count || initial >= count) return -1;
#if CONFIG_KSHIM_FRAMEBUFFER && CONFIG_KSHIM_LVGL
    int status = start_menu((const void *)(uintptr_t)fdt);
    if (!status && kshim_lvgl_set_entries(&Lvgl, names, count, initial) == 0) {
        kshim_lvgl_set_countdown(&Lvgl, timeout_ms);
        uint64_t start = get_current_time();
        for (;;) {
            menu_frame();
            int index = kshim_lvgl_take_index(&Lvgl);
            if (index >= 0 && (size_t)index < count) { *selected = index; break; }
            if (timeout_ms && get_current_time() - start >= timeout_ms) { *selected = initial; break; }
            (void)delay(5000);
        }
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
        int close = kshim_platform_prepare_handoff();
        ui_probe_report((void *)(uintptr_t)fdt, close, *selected);
        return close ? -2 : 0;
#else
        return kshim_platform_prepare_handoff() ? -2 : 0;
#endif
    }
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    {
        int close = kshim_platform_prepare_handoff();
        ui_probe_report((void *)(uintptr_t)fdt, close, initial);
        if (close) return -2;
    }
#else
    if (kshim_platform_prepare_handoff()) return -2;
#endif
#endif
    return serial_select((const void *)(uintptr_t)fdt, names, count, initial, timeout_ms, selected);
}
int kshim_platform_main(uint64_t fdt, uint64_t arg1, uint64_t arg2, uint64_t arg3)
{
    kshim_boot_set_args(fdt, arg1, arg2, arg3);
#if CONFIG_KSHIM_SMP_SELFTEST
    int status = kshim_smp_selftest((const void *)(uintptr_t)fdt);
    printf("SMP_SELFTEST %s (%d)\n", status == 0 ? "PASS" : "FAIL", status);
    kshim_psci_system_off();
#endif
    const char *names[] = {CONFIG_KSHIM_MENU_ENTRY1, CONFIG_KSHIM_MENU_ENTRY2};
    for (;;) {
        size_t selected;
        if (kshim_platform_select(fdt, names, 2, 0, 0, &selected) == -2) {
            printf("Unsafe UI shutdown; refusing payload handoff\n");
            for (;;) (void)delay(100000);
        }
        if (kshim_menu_activate(selected ? KSHIM_MENU_RECOVERY : KSHIM_MENU_BOOT))
            printf("Unable to start selected image\n");
    }
}
