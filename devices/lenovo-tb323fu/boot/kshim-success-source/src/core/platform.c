/* SPDX-License-Identifier: MIT */
#include <kshim.h>
#include <platform.h>
#include <qcom_dt.h>
#include <smp.h>
#include <smp_selftest.h>
#include <psci.h>
#include <kshim_mmu.h>
#include <st_ftm5.h>
#include <kshim_lvgl_os.h>
#include <kshim_console.h>
#include <timer.h>
#include <config.h>
#include <menu_text.h>
#include <framebuffer.h>
#include <lvgl_port.h>
#include <qcom_key_platform.h>
#include <qcom_keys.h>
#include <qcom_keys_fdt.h>
#include <uefi_boot.h>
#include <dt.h>
#include <boot_metrics.h>
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
#include <qcom_reboot.h>
#endif

/* With the runtime language switch on, every user-visible string resolves
 * through the locale table so the live selection flips platform text too; the
 * compile-time literals below stay authoritative when the switch is off. */
#if defined(CONFIG_KSHIM_MENU_LANGUAGE_SWITCH) && CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
#define KSHIM_STR_KEYS_HINT      kshim_menu_text(KSHIM_TEXT_KEYS_HINT)
#define KSHIM_STR_KEYS_UNAVAIL   kshim_menu_text(KSHIM_TEXT_KEYS_UNAVAILABLE)
#define KSHIM_STR_AUTO_CANCELLED kshim_menu_text(KSHIM_TEXT_AUTO_CANCELLED)
#define KSHIM_STR_POWER_FAILED   kshim_menu_text(KSHIM_TEXT_POWER_FAILED)
#define KSHIM_STR_RESTART        kshim_menu_text(KSHIM_TEXT_RESTART)
#define KSHIM_STR_POWER_OFF      kshim_menu_text(KSHIM_TEXT_POWER_OFF)
#define KSHIM_STR_ADVANCED       kshim_menu_text(KSHIM_TEXT_ADVANCED)
#define KSHIM_STR_RECOVERY       kshim_menu_text(KSHIM_TEXT_RECOVERY)
#define KSHIM_STR_FASTBOOT       kshim_menu_text(KSHIM_TEXT_FASTBOOT)
#define KSHIM_STR_EDL            kshim_menu_text(KSHIM_TEXT_EDL)
#define KSHIM_STR_BACK           kshim_menu_text(KSHIM_TEXT_BACK)
#define KSHIM_STR_MODE_FAILED    kshim_menu_text(KSHIM_TEXT_MODE_FAILED)
#define KSHIM_STR_RESTORE_FAILED kshim_menu_text(KSHIM_TEXT_RESTORE_FAILED)
#define KSHIM_STR_MENU_TITLE     kshim_menu_text(KSHIM_TEXT_MENU_TITLE)
#define KSHIM_STR_MENU_PROMPT    kshim_menu_text(KSHIM_TEXT_MENU_PROMPT)
#define KSHIM_STR_MENU_DEFAULT   kshim_menu_text(KSHIM_TEXT_MENU_DEFAULT)
#define KSHIM_STR_HANDOFF_UNSAFE kshim_menu_text(KSHIM_TEXT_HANDOFF_UNSAFE)
#define KSHIM_STR_UNABLE_START   kshim_menu_text(KSHIM_TEXT_UNABLE_START)
#elif CONFIG_KSHIM_MENU_CHINESE
#define KSHIM_STR_KEYS_HINT      "音量键选择，电源键启动"
#define KSHIM_STR_KEYS_UNAVAIL   "音量键暂不可用"
#define KSHIM_STR_AUTO_CANCELLED "已取消自动启动，请手动选择"
#define KSHIM_STR_POWER_FAILED   "电源操作失败"
#define KSHIM_STR_RESTART        "重启"
#define KSHIM_STR_POWER_OFF      "关机"
#define KSHIM_STR_ADVANCED       "高级启动模式"
#define KSHIM_STR_RECOVERY       "Recovery 恢复模式"
#define KSHIM_STR_FASTBOOT       "Fastboot 模式"
#define KSHIM_STR_EDL            "9008 下载模式"
#define KSHIM_STR_BACK           "返回主菜单"
#define KSHIM_STR_MODE_FAILED    "切换启动模式失败"
#define KSHIM_STR_RESTORE_FAILED "切换启动模式失败，恢复原设置失败"
#define KSHIM_STR_MENU_TITLE     "KShimC 启动器"
#define KSHIM_STR_MENU_PROMPT    "输入序号并按回车确认："
#define KSHIM_STR_MENU_DEFAULT   " [默认]"
#define KSHIM_STR_HANDOFF_UNSAFE "Unsafe UI shutdown; refusing payload handoff"
#define KSHIM_STR_UNABLE_START   "Unable to start selected image"
#else
#define KSHIM_STR_KEYS_HINT      "Use vol keys, power to boot"
#define KSHIM_STR_KEYS_UNAVAIL   "Volume keys unavailable"
#define KSHIM_STR_AUTO_CANCELLED "Auto boot cancelled; choose an entry"
#define KSHIM_STR_POWER_FAILED   "Power action failed"
#define KSHIM_STR_RESTART        "Restart"
#define KSHIM_STR_POWER_OFF      "Power off"
#define KSHIM_STR_ADVANCED       "Advanced boot modes"
#define KSHIM_STR_RECOVERY       "Enter Recovery"
#define KSHIM_STR_FASTBOOT       "Enter Fastboot"
#define KSHIM_STR_EDL            "Enter 9008"
#define KSHIM_STR_BACK           "Back to main menu"
#define KSHIM_STR_MODE_FAILED    "Boot mode switch failed"
#define KSHIM_STR_RESTORE_FAILED "Boot mode switch failed; restoring the original settings failed"
#define KSHIM_STR_MENU_TITLE     "KShim boot menu"
#define KSHIM_STR_MENU_PROMPT    "Select kernel and press Enter: "
#define KSHIM_STR_MENU_DEFAULT   " [default]"
#define KSHIM_STR_HANDOFF_UNSAFE "Unsafe UI shutdown; refusing payload handoff"
#define KSHIM_STR_UNABLE_START   "Unable to start selected image"
#endif

/* Enables the runtime language page and role driven payload labels. Guarded so
 * the plain (switch off) builds never reference the locale module. */
#if defined(CONFIG_KSHIM_MENU_LANGUAGE_SWITCH) && CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
#define KSHIM_MENU_LANGUAGE_ENABLED 1
#else
#define KSHIM_MENU_LANGUAGE_ENABLED 0
#endif

__attribute__((weak)) int kshim_menu_activate(kshim_menu_action_t action)
{ (void)action; return -1; }
#if CONFIG_KSHIM_SMP
static int SmpInitialized;
#endif
static QcomKeys Keys;
static int build_keys(const void *fdt)
{
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    (void)fdt; return -1;
#elif CONFIG_QCOM_KEYS
    QcomKeyDescriptor descriptors[QCOM_KEYS_MAX];
    QcomKeyFdtAdapter adapter;
    size_t count = 0;
    if (!fdt || QcomKeyFdtAdapterInit(&adapter, fdt))
        return -1;
    /* The resources phase has already closed its DT index. Reopen it only
     * for parsing this validated blob, then release it before key setup. */
    dt_parent_index_begin(fdt);
    int status = QcomKeysBuildFromFdt(&adapter.Ops, descriptors, QCOM_KEYS_MAX, &count);
    dt_parent_index_end();
    if (status || !count)
        return -1;
    status = QcomKeysInitEx(&Keys, descriptors, count, kshim_qcom_key_read32,
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
    int status = kshim_ftm5_quiesce(&Ftm5);
    if (status || CrQupQuiesce(&TouchPlatform)) return -1;
    TouchAttempted = TouchReady = 0;
    return 0;
}
static void start_touch(struct kshim_resources *r)
{
    if (r->touch_status) return;
    /* Only the FTM5 generation has a runtime driver; unsupported generations
     * fall back to the volume-key menu without touching MMIO. */
    if (r->touch.generation != 5) return;
    TouchResource = &r->touch;
    kshim_touch_config_t cfg = {.source_width = r->touch.width, .source_height = r->touch.height,
        .output_width = Framebuffer.width, .output_height = Framebuffer.height,
        .flip_x = r->touch.flip_x, .flip_y = r->touch.flip_y,
        .emit = kshim_lvgl_touch, .emit_context = &Lvgl};
    int status = kshim_touch_init(&Touch, &cfg);
    if (!status) status = CrQupInit(&TouchPlatform, NULL, &r->touch.qup);
    TouchAttempted = 1;
    if (!status) {
        kshim_ftm5_transport_t t = {NULL, touch_transfer, touch_delay, KSHIM_FTM5_BUS_I2C};
        status = kshim_ftm5_init(&Ftm5, &t, &Touch);
    }
    TouchReady = status == 0;
    printf("Touch: FTM%u status %d\n", r->touch.generation, status);
    if (!TouchReady) { (void)touch_quiesce(); kshim_lvgl_set_status(&Lvgl, KSHIM_STR_KEYS_HINT); }
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
    kshim_boot_metrics_record_once(KSHIM_BOOT_STAGE_MMU);
    kshim_lvgl_set_scratch((void *)(uintptr_t)r->ui_scratch.base,
                           (size_t)r->ui_scratch.size);
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
    printf("UI: %s font, scratch %#llx+%#llx\n",
           kshim_lvgl_has_cjk_font(&Lvgl) ? "Noto Sans CJK" : "fallback Latin",
           (unsigned long long)r->ui_scratch.base, (unsigned long long)r->ui_scratch.size);
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
    kshim_lvgl_set_status(&Lvgl, KSHIM_STR_KEYS_HINT);
#else
    if (keys) kshim_lvgl_set_status(&Lvgl, KSHIM_STR_KEYS_UNAVAIL);
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
        if (status > 0) status = kshim_ftm5_poll(&Ftm5);
        if (status < 0) {
            TouchReady = 0; kshim_touch_cancel(&Touch);
            printf("Touch: input status %d\n", status);
            kshim_lvgl_set_status(&Lvgl, KSHIM_STR_KEYS_HINT);
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
#if CONFIG_KSHIM_SMP
    if (SmpInitialized) {
        if (kshim_smp_prepare_handoff(KSHIM_SMP_DEFAULT_TIMEOUT_US)) return -2;
        SmpInitialized = 0;
        return 0;
    }
#endif
    return kshim_mmu_prepare_handoff();
}
/* Entries carry their action separately from the original manifest index. */
enum menu_action {
    MENU_PAYLOAD, MENU_ADVANCED, MENU_BACK, MENU_RESET, MENU_OFF,
    MENU_RECOVERY, MENU_FASTBOOT, MENU_EDL,
    MENU_LANGUAGE, MENU_LANG_ZH, MENU_LANG_EN,
};
struct menu_page {
    const char *names[KSHIM_MENU_MAX_ENTRIES];
    const char *descriptions[KSHIM_MENU_MAX_ENTRIES];
    const char *action_labels[KSHIM_MENU_MAX_ENTRIES];
    enum menu_action actions[KSHIM_MENU_MAX_ENTRIES];
    kshim_menu_role_t roles[KSHIM_MENU_MAX_ENTRIES];
    uint8_t payloads[KSHIM_MENU_MAX_ENTRIES];
    size_t count, initial;
};
struct menu_selection {
    struct menu_page pages[3];
    const void *fdt;
    /* Caller-owned inputs are cached so the pages can be rebuilt in place after
     * a language change; no translated text is retained across rebuilds. */
    const char *const *caller_names;
    const char *const *caller_descriptions;
    const kshim_menu_role_t *caller_roles;
    size_t caller_count;
    size_t page, advanced_row, language_row, original_initial;
    uint64_t advanced_mask;
    uint32_t timeout;
    int advanced, language, psci_ready;
};
static int menu_append(struct menu_page *page, const char *name, const char *description,
                       enum menu_action action, size_t payload, kshim_menu_role_t role)
{
    if (page->count >= KSHIM_MENU_MAX_ENTRIES || !name) return -1;
    size_t row = page->count++;
    page->names[row] = name;
    page->descriptions[row] = description;
    page->actions[row] = action;
    page->roles[row] = role;
    switch (action) {
    case MENU_PAYLOAD:
        page->action_labels[row] = KSHIM_MENU_ACTION_BOOT;
        break;
    case MENU_ADVANCED:
    case MENU_RECOVERY:
    case MENU_FASTBOOT:
    case MENU_EDL:
    case MENU_LANGUAGE:
        page->action_labels[row] = KSHIM_MENU_ACTION_ENTER;
        break;
    case MENU_LANG_ZH:
    case MENU_LANG_EN:
        page->action_labels[row] = kshim_menu_text(KSHIM_TEXT_LANGUAGE_SELECT);
        break;
    case MENU_RESET:
        page->action_labels[row] = KSHIM_MENU_ACTION_RESTART;
        break;
    case MENU_OFF:
        page->action_labels[row] = KSHIM_MENU_ACTION_OFF;
        break;
    case MENU_BACK:
        page->action_labels[row] = KSHIM_MENU_ACTION_BACK;
        break;
    }
    page->payloads[row] = (uint8_t)payload;
    return 0;
}
/* Rebuild every page from the cached caller inputs for the active locale. */
static int menu_fill(struct menu_selection *menu)
{
    for (size_t p = 0; p < 3; ++p) {
        menu->pages[p].count = 0;
        menu->pages[p].initial = 0;
    }
    for (size_t i = 0; i < menu->caller_count; ++i) {
        size_t page_index = menu->advanced && (menu->advanced_mask & (UINT64_C(1) << i)) ? 1U : 0U;
        struct menu_page *page = &menu->pages[page_index];
        if (i == menu->original_initial) {
            menu->page = page_index;
            page->initial = page->count;
        }
        kshim_menu_role_t role = menu->caller_roles ? menu->caller_roles[i] : KSHIM_MENU_ROLE_UNKNOWN;
        const char *name = menu->caller_names[i];
        const char *description = menu->caller_descriptions ? menu->caller_descriptions[i] : NULL;
#if KSHIM_MENU_LANGUAGE_ENABLED
        name = kshim_menu_role_name(role, name);
        description = kshim_menu_role_description(role, description);
#endif
        if (menu_append(page, name, description, MENU_PAYLOAD, i, role)) return -1;
    }
    if (menu->advanced) {
        menu->advanced_row = menu->pages[0].count;
        if (menu_append(&menu->pages[0], KSHIM_STR_ADVANCED, KSHIM_MENU_DESC_ADVANCED,
                        MENU_ADVANCED, 0, KSHIM_MENU_ROLE_ADVANCED) ||
            menu_append(&menu->pages[1], KSHIM_STR_RECOVERY, KSHIM_MENU_DESC_RECOVERY,
                        MENU_RECOVERY, 0, KSHIM_MENU_ROLE_RECOVERY) ||
            menu_append(&menu->pages[1], KSHIM_STR_FASTBOOT, KSHIM_MENU_DESC_FASTBOOT,
                        MENU_FASTBOOT, 0, KSHIM_MENU_ROLE_FASTBOOT) ||
            menu_append(&menu->pages[1], KSHIM_STR_EDL, KSHIM_MENU_DESC_EDL,
                        MENU_EDL, 0, KSHIM_MENU_ROLE_EDL) ||
            menu_append(&menu->pages[1], KSHIM_STR_BACK, KSHIM_MENU_DESC_BACK,
                        MENU_BACK, 0, KSHIM_MENU_ROLE_BACK)) return -1;
    }
#if KSHIM_MENU_LANGUAGE_ENABLED
    if (menu->language) {
        menu->language_row = menu->pages[0].count;
        if (menu_append(&menu->pages[0], KSHIM_MENU_TEXT_LANGUAGE_LABEL,
                        kshim_menu_text(KSHIM_TEXT_LANGUAGE_DESC), MENU_LANGUAGE, 0,
                        KSHIM_MENU_ROLE_LANGUAGE) ||
            menu_append(&menu->pages[2], KSHIM_MENU_LABEL_LANG_ZH,
                        kshim_menu_text(KSHIM_TEXT_DESC_LANG_ZH), MENU_LANG_ZH, 0,
                        KSHIM_MENU_ROLE_LANG_ZH) ||
            menu_append(&menu->pages[2], KSHIM_MENU_LABEL_LANG_EN,
                        kshim_menu_text(KSHIM_TEXT_DESC_LANG_EN), MENU_LANG_EN, 0,
                        KSHIM_MENU_ROLE_LANG_EN) ||
            menu_append(&menu->pages[2], KSHIM_STR_BACK, KSHIM_MENU_DESC_BACK,
                        MENU_BACK, 0, KSHIM_MENU_ROLE_BACK)) return -1;
    }
#endif
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    if (menu_append(&menu->pages[0], KSHIM_STR_RESTART, KSHIM_MENU_DESC_RESTART,
                    MENU_RESET, 0, KSHIM_MENU_ROLE_RESTART) ||
        menu_append(&menu->pages[0], KSHIM_STR_POWER_OFF, KSHIM_MENU_DESC_OFF,
                    MENU_OFF, 0, KSHIM_MENU_ROLE_OFF)) return -1;
#endif
    return 0;
}
static int menu_build(struct menu_selection *menu, const char *const *names,
                      const char *const *descriptions, const kshim_menu_role_t *roles,
                      size_t count, size_t initial, uint64_t mask, int advanced, int language)
{
    if (!names || !count || count > 49U || initial >= count || (mask >> count)) return -1;
    menu->caller_names = names;
    menu->caller_descriptions = descriptions;
    menu->caller_roles = roles;
    menu->caller_count = count;
    menu->original_initial = initial;
    menu->advanced = advanced;
    menu->language = language;
    menu->advanced_mask = mask;
    menu->advanced_row = 0;
    menu->language_row = 0;
    return menu_fill(menu);
}
/* 1 = payload selected; 2 = changed page; 0 = returned firmware action failed. */
static int menu_activate(struct menu_selection *menu, size_t index,
                         size_t *selected, const char **failure)
{
    struct menu_page *page = &menu->pages[menu->page];
    enum menu_action action = page->actions[index];
    *failure = NULL;
    if (action == MENU_PAYLOAD) {
        *selected = page->payloads[index];
        return 1;
    }
    /* This flag belongs to the whole menu session, not to an individual page. */
    menu->timeout = 0;
    if (action == MENU_ADVANCED) {
        menu->page = 1U;
        menu->pages[1].initial = 0U;
        return 2;
    }
    if (action == MENU_BACK) {
        size_t from = menu->page;
        menu->page = 0U;
        menu->pages[0].initial = from == 2U ? menu->language_row : menu->advanced_row;
        return 2;
    }
#if KSHIM_MENU_LANGUAGE_ENABLED
    if (action == MENU_LANGUAGE) {
        menu->page = 2U;
        menu->pages[2].initial = 0U;
        return 2;
    }
    if (action == MENU_LANG_ZH || action == MENU_LANG_EN) {
        (void)kshim_menu_set_language(action == MENU_LANG_ZH ?
            KSHIM_MENU_LANG_ZH_CN : KSHIM_MENU_LANG_EN);
        if (menu_fill(menu)) return 2;
        /* Rebuilt in the new locale; focus stays on the home language row. */
        menu->page = 0U;
        menu->pages[0].initial = menu->language_row;
        return 2;
    }
#endif
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    if (action == MENU_RECOVERY || action == MENU_FASTBOOT || action == MENU_EDL) {
        enum kshim_qcom_reboot_target target = action == MENU_RECOVERY ?
            KSHIM_QCOM_REBOOT_RECOVERY : action == MENU_FASTBOOT ?
            KSHIM_QCOM_REBOOT_BOOTLOADER : KSHIM_QCOM_REBOOT_EDL;
        int result = kshim_qcom_reboot_request(menu->fdt, target);
        *failure = result == KSHIM_QCOM_REBOOT_ROLLBACK_FAILED ?
            KSHIM_STR_RESTORE_FAILED : KSHIM_STR_MODE_FAILED;
        printf("%s: %d\n", *failure, result);
        return 0;
    }
#endif
    *failure = KSHIM_STR_POWER_FAILED;
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    if (menu->psci_ready) {
        int64_t result = action == MENU_RESET ? kshim_psci_request_system_reset() :
                                                kshim_psci_request_system_off();
        printf("%s: %lld\n", *failure, (long long)result);
    } else
#endif
        printf("%s\n", *failure);
    return 0;
}
static void serial_show_page(const struct menu_page *page)
{
    printf("\n%s\n", KSHIM_STR_MENU_TITLE);
    for (size_t i = 0; i < page->count; ++i)
        printf("  %u. %s%s\n", (unsigned)i + 1U, page->names[i],
               i == page->initial ? KSHIM_STR_MENU_DEFAULT : "");
    printf("%s", KSHIM_STR_MENU_PROMPT);
}
static void serial_cancel_timeout(struct menu_selection *menu)
{
    if (menu->timeout) printf("\n%s\n", KSHIM_STR_AUTO_CANCELLED);
    menu->timeout = 0;
}
static int serial_select(struct menu_selection *menu, size_t *selected)
{
    int keys_ready = build_keys(menu->fdt) == 0;
    struct menu_page *page = &menu->pages[menu->page];
    size_t current = page->initial, numeric = 0;
    serial_show_page(page);
    uint64_t start = get_current_time();
    for (;;) {
        int confirm = 0;
        size_t confirmed_index = current;
        char c;
        if (kshim_console_try_read(&c)) {
            if (c >= '0' && c <= '9') {
                size_t digit = (size_t)(c - '0');
                if (numeric > 0 || digit > 0) {
                    size_t next = numeric * 10U + digit;
                    if (next <= page->count) {
                        numeric = next;
                        kshim_console_write(&c, 1);
                        serial_cancel_timeout(menu);
                    }
                }
            } else if (c == '\r' || c == '\n') {
                if (numeric >= 1U && numeric <= page->count) {
                    confirmed_index = numeric - 1U;
                    confirm = 1;
                }
                numeric = 0;
            } else if (c == 27) {
                if (menu->page != 0U) {
                    /* Every subpage ends with the explicit Back action. */
                    confirmed_index = page->count - 1U;
                    confirm = 1;
                    serial_cancel_timeout(menu);
                } else {
                    *selected = menu->original_initial;
                    return 0;
                }
            }
        }
        if (!confirm && keys_ready) {
            QcomKeyEvent event;
            if (QcomKeysPoll(&Keys, &event) == QCOM_KEYS_EVENT &&
                event.Type == QCOM_KEY_EVENT_PRESS && event.Pressed) {
                int consumed = 0;
                if (event.Action == QCOM_KEY_ACTION_UP) {
                    current = current ? current - 1U : page->count - 1U;
                    consumed = 1;
                }
                if (event.Action == QCOM_KEY_ACTION_DOWN) {
                    current = (current + 1U) % page->count;
                    consumed = 1;
                }
                if (event.Action == QCOM_KEY_ACTION_SELECT) {
                    consumed = 1;
                    confirmed_index = current;
                    confirm = 1;
                }
                if (consumed) {
                    serial_cancel_timeout(menu);
                    printf("\n> %s\n", page->names[current]);
                }
            }
        }
        if (confirm) {
            const char *failure;
            int result = menu_activate(menu, confirmed_index, selected, &failure);
            if (result == 1) return 0;
            if (result == 2) {
                page = &menu->pages[menu->page];
                current = page->initial;
                serial_show_page(page);
            } else {
                printf("%s", KSHIM_STR_MENU_PROMPT);
            }
            numeric = 0;
            continue;
        }
        if (menu->timeout && get_current_time() - start >= (uint64_t)menu->timeout) {
            *selected = menu->original_initial;
            return 0;
        }
        (void)delay(5000);
    }
}
static int platform_select(uint64_t fdt, const char *const *names,
                           const char *const *descriptions,
                           const kshim_menu_role_t *roles, size_t count,
                           size_t initial, uint32_t timeout_ms, uint64_t advanced_mask,
                           int advanced, int language, size_t *selected)
{
    if (!selected) return -1;
    struct menu_selection menu = {.fdt = (const void *)(uintptr_t)fdt, .timeout = timeout_ms};
    if (menu_build(&menu, names, descriptions, roles, count, initial,
                   advanced_mask, advanced, language)) return -1;
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    int pi = kshim_psci_init(menu.fdt);
    menu.psci_ready = pi == 0;
    if (pi) printf("PSCI init failed: %d\n", pi);
#endif
#if CONFIG_KSHIM_FRAMEBUFFER && CONFIG_KSHIM_LVGL
    struct menu_page *page = &menu.pages[menu.page];
    int status = start_menu(menu.fdt);
    if (!status && kshim_lvgl_set_entries_described(&Lvgl, page->names, page->descriptions,
                                                   page->action_labels, page->count,
                                                   page->initial) == 0) {
        kshim_lvgl_set_entry_roles(&Lvgl, page->roles, page->count);
        kshim_boot_metrics_record_once(KSHIM_BOOT_STAGE_ENTRIES);
        kshim_lvgl_set_countdown(&Lvgl, menu.timeout);
        uint64_t start = get_current_time();
        for (;;) {
            menu_frame();
            if (menu.timeout && kshim_lvgl_has_interacted(&Lvgl)) {
                menu.timeout = 0;
                kshim_lvgl_set_countdown(&Lvgl, 0);
                kshim_lvgl_set_status(&Lvgl, KSHIM_STR_AUTO_CANCELLED);
            }
            int index = kshim_lvgl_take_index(&Lvgl);
            if (index >= 0 && (size_t)index < page->count) {
                const char *failure;
                int result = menu_activate(&menu, (size_t)index, selected, &failure);
                if (result == 1) break;
                kshim_lvgl_set_countdown(&Lvgl, 0);
                if (result == 2) {
                    page = &menu.pages[menu.page];
                    if (kshim_lvgl_set_entries_described(&Lvgl, page->names, page->descriptions,
                                                        page->action_labels, page->count,
                                                        page->initial)) {
                        /* Keep the selected page and cancelled timeout on serial fallback. */
                        status = -1;
                        break;
                    }
                    kshim_lvgl_set_entry_roles(&Lvgl, page->roles, page->count);
                } else {
                    kshim_lvgl_set_status(&Lvgl, failure);
                }
                continue;
            }
            if (menu.timeout && get_current_time() - start >= (uint64_t)menu.timeout) {
                *selected = menu.original_initial;
                break;
            }
            (void)delay(5000);
        }
        if (!status) {
#if CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY
            int close = kshim_platform_prepare_handoff();
            ui_probe_report((void *)(uintptr_t)fdt, close, *selected);
            return close ? -2 : 0;
#else
            return kshim_platform_prepare_handoff() ? -2 : 0;
#endif
        }
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
    return serial_select(&menu, selected);
}
int kshim_platform_select(uint64_t fdt, const char *const *names, size_t count,
                           size_t initial, uint32_t timeout_ms, size_t *selected)
{
    /* Compatibility entry point: flat rows, no role labels, no language page. */
    return platform_select(fdt, names, NULL, NULL, count, initial, timeout_ms, 0, 0, 0, selected);
}
int kshim_platform_select_advanced(uint64_t fdt, const char *const *names,
                                  size_t count, size_t initial,
                                  uint32_t timeout_ms, uint64_t advanced_mask,
                                  size_t *selected)
{
    return kshim_platform_select_described(fdt, names, NULL, count, initial,
                                           timeout_ms, advanced_mask, selected);
}
int kshim_platform_select_described(uint64_t fdt, const char *const *names,
                                   const char *const *descriptions, size_t count,
                                   size_t initial, uint32_t timeout_ms,
                                   uint64_t advanced_mask, size_t *selected)
{
    /* No caller roles: every payload keeps its own name/description text. */
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    return platform_select(fdt, names, descriptions, NULL, count, initial, timeout_ms,
                            advanced_mask, 1, KSHIM_MENU_LANGUAGE_ENABLED, selected);
#else
    (void)advanced_mask;
    return platform_select(fdt, names, descriptions, NULL, count, initial, timeout_ms,
                            0, 0, KSHIM_MENU_LANGUAGE_ENABLED, selected);
#endif
}
int kshim_platform_select_localized(uint64_t fdt, const char *const *names,
                                    const char *const *descriptions,
                                    const kshim_menu_role_t *roles, size_t count,
                                    size_t initial, uint32_t timeout_ms,
                                    uint64_t advanced_mask, size_t *selected)
{
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    return platform_select(fdt, names, descriptions, roles, count, initial, timeout_ms,
                            advanced_mask, 1, KSHIM_MENU_LANGUAGE_ENABLED, selected);
#else
    (void)advanced_mask;
    return platform_select(fdt, names, descriptions, roles, count, initial, timeout_ms,
                            0, 0, KSHIM_MENU_LANGUAGE_ENABLED, selected);
#endif
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
            printf("%s\n", KSHIM_STR_HANDOFF_UNSAFE);
            for (;;) (void)delay(100000);
        }
        if (kshim_menu_activate(selected ? KSHIM_MENU_RECOVERY : KSHIM_MENU_BOOT))
            printf("%s\n", KSHIM_STR_UNABLE_START);
    }
}
