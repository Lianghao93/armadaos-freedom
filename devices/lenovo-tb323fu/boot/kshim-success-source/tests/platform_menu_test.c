/* Exercise the real platform selection loop; hardware and UI are scripted. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CONFIG_KSHIM_FRAMEBUFFER 1
#define CONFIG_KSHIM_LVGL 1
#define CONFIG_QCOM_KEYS 1
#define CONFIG_QCOM_KEY_DEBOUNCE_SAMPLES 2
#define CONFIG_KSHIM_SMP 0
#define CONFIG_KSHIM_SMP_SELFTEST 0
#define CONFIG_KSHIM_TOUCH 0
#define CONFIG_KSHIM_DIAGNOSTIC_UI_ONLY 0
#define CONFIG_KSHIM_QEMU_PLATFORM 0
#ifndef CONFIG_KSHIM_MENU_CHINESE
#define CONFIG_KSHIM_MENU_CHINESE 0
#endif
#define CONFIG_KSHIM_MENU_ENTRY1 "Payload 1"
#define CONFIG_KSHIM_MENU_ENTRY2 "Payload 2"
#ifndef CONFIG_KSHIM_MENU_POWER_ACTIONS
#define CONFIG_KSHIM_MENU_POWER_ACTIONS 1
#endif
/* Only the dedicated language target defines this; the legacy profiles keep
 * the old grouping-only fixture untouched. */
#ifndef CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
#define CONFIG_KSHIM_MENU_LANGUAGE_SWITCH 0
#endif

#include "../src/core/platform.c"

enum { MAX_EVENTS = 32, MAX_STEPS = 12000, MAX_PAGES = 16 };
typedef struct { uint64_t at; int index; int interacted; } ui_event_t;
typedef struct { uint64_t at; char value; } serial_event_t;
typedef struct { uint64_t at; int result; QcomKeyEvent event; } key_event_t;
typedef struct { size_t count, initial; bool advanced; } page_event_t;
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
/* Per-page snapshot so the language tests can inspect names, descriptions and
 * action labels after capture. 53 rows fit 49 payloads plus the advanced,
 * language and two power rows without editing any production header. */
enum { LANG_ROW_CAP = 53, LANG_PAGES = 8 };
typedef struct {
    int kind; /* 0 = home, 1 = advanced, 2 = language */
    size_t count, initial;
    char names[LANG_ROW_CAP][32];
    const char *descriptions[LANG_ROW_CAP];
    const char *actions[LANG_ROW_CAP];
} lang_page_t;
#endif
static struct {
    uint64_t now;
    unsigned steps;
    bool serial, interacted, ui_alive, advanced, advanced_page;
    int pending, psci_result, ui_init_result, entries_result;
    int64_t reset_result, off_result;
    unsigned init_calls, entries_calls, entries_fail_at, frames, deinit_calls, handoff_calls;
    unsigned psci_calls, reset_calls, off_calls, countdown_zero, countdown_started, status_calls;
    unsigned fb_calls, keys_build_calls;
    unsigned keys_adapter_calls, keys_begin_calls, keys_end_calls;
    unsigned keys_init_calls, keys_debounce_calls;
    bool keys_scope_active;
    int keys_adapter_result, keys_parse_result, keys_init_result;
    size_t keys_parse_count;
    QcomKeyFdtAdapter *keys_adapter;
    unsigned reboot_calls;
    int reboot_result, reboot_target, handoff_result;
    uint64_t advanced_mask;
    uint32_t countdown_first;
    size_t payload_count, menu_count, initial;
    const void *psci_fdt, *reboot_fdt;
    /* 53 covers the language-enabled worst case: 49 payloads plus the advanced,
     * language and two power rows. */
    char status[160], labels[53][32], payload_labels[50][32];
    const char *names[50], *descriptions[50], *entry_descriptions[53];
    const char *entry_action_labels[53];
    kshim_menu_role_t roles[53]; size_t role_count; unsigned role_calls;
    ui_event_t ui[MAX_EVENTS]; size_t ui_count, ui_next;
    serial_event_t chars[MAX_EVENTS]; size_t char_count, char_next;
    key_event_t keys[MAX_EVENTS]; size_t key_count, key_next;
    page_event_t pages[MAX_PAGES]; size_t page_count;
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
    lang_page_t snap[LANG_PAGES]; size_t snap_count;
#endif
    struct kshim_resources resources;
} h;
static unsigned fdt_cookie;
static const QcomKeyDescriptor key_descriptors[] = {
    {.Base = QCOM_KEY_SPMI_RESOURCE(0, 0, 0, 0x1300),
     .RegisterOffset = 0x10, .Mask = 128, .Code = QCOM_KEYCODE_POWER,
     .Action = QCOM_KEY_ACTION_SELECT, .Source = QCOM_KEY_SOURCE_PON,
     .ActiveLow = 0, .DebounceSamples = 3},
    {.Base = 0xf165000, .RegisterOffset = 4, .Mask = 1,
     .Code = QCOM_KEYCODE_VOLUMEUP, .Action = QCOM_KEY_ACTION_UP,
     .Source = QCOM_KEY_SOURCE_GPIO, .ActiveLow = 1, .DebounceSamples = 4},
    {.Base = QCOM_KEY_SPMI_RESOURCE(0, 0, 0, 0x1300),
     .RegisterOffset = 0x10, .Mask = 64, .Code = QCOM_KEYCODE_VOLUMEDOWN,
     .Action = QCOM_KEY_ACTION_DOWN, .Source = QCOM_KEY_SOURCE_PON,
     .ActiveLow = 0, .DebounceSamples = 5},
};

static void budget(void) { assert(++h.steps < MAX_STEPS); }
static uint64_t boot_fdt(void) { return (uint64_t)(uintptr_t)&fdt_cookie; }
static size_t expected_count(void)
{
    return h.payload_count + (CONFIG_KSHIM_MENU_POWER_ACTIONS ? 2U : 0U);
}
static void setup(bool serial, size_t count)
{
    memset(&h, 0, sizeof(h));
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
    /* The locale module keeps a session language; reset it to the configured
     * default so each case starts from a known locale. */
    (void)kshim_menu_set_language(CONFIG_KSHIM_MENU_CHINESE ?
        KSHIM_MENU_LANG_ZH_CN : KSHIM_MENU_LANG_EN);
#endif
    h.serial = serial;
    h.pending = -1;
    h.payload_count = count;
    h.keys_parse_count = 1;
    h.reset_result = h.off_result = -1;
    h.resources.framebuffer.render_address = 0x10000U;
    h.resources.framebuffer.width = 800;
    h.resources.framebuffer.height = 600;
    h.resources.framebuffer.stride = 3200;
    h.resources.framebuffer.bpp = 32;
    h.resources.framebuffer.format = KSHIM_FB_FORMAT_ARGB8888;
    for (size_t i = 0; i < count; ++i) {
        snprintf(h.payload_labels[i], sizeof(h.payload_labels[i]), "Payload %u", (unsigned)i + 1U);
        h.names[i] = h.payload_labels[i];
    }
}
static void ui_at(uint64_t at, int index, int interacted)
{
    assert(h.ui_count < MAX_EVENTS);
    h.ui[h.ui_count++] = (ui_event_t){at, index, interacted};
}
static void char_at(uint64_t at, char value)
{
    assert(h.char_count < MAX_EVENTS);
    h.chars[h.char_count++] = (serial_event_t){at, value};
}
static void text_at(uint64_t at, const char *text)
{
    while (*text) char_at(at, *text++);
}
__attribute__((unused))
static void key_at(uint64_t at, int result, QcomKeyEventType type,
                   QcomKeyAction action, uint8_t pressed)
{
    assert(h.key_count < MAX_EVENTS);
    h.keys[h.key_count++] = (key_event_t){at, result,
        {.Type = type, .Action = action, .Pressed = pressed}};
}
static void expect_psci_init(void)
{
    assert(h.psci_calls == (CONFIG_KSHIM_MENU_POWER_ACTIONS ? 1U : 0U));
    if (CONFIG_KSHIM_MENU_POWER_ACTIONS)
        assert(h.psci_fdt == (const void *)(uintptr_t)boot_fdt());
}
#if !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
static size_t choose(size_t initial, uint32_t timeout)
{
    size_t selected = SIZE_MAX;
    int rc = kshim_platform_select(boot_fdt(), h.names, h.payload_count,
                                    initial, timeout, &selected);
    assert(rc == 0);
    assert(selected < h.payload_count);
    expect_psci_init();
    assert(h.deinit_calls == 1U && h.handoff_calls == 1U);
    if (!h.serial && h.ui_init_result == 0 && h.entries_result == 0) {
        assert(h.init_calls == 1U && h.entries_calls == 1U);
        assert(h.menu_count == expected_count());
    }
    return selected;
}
#endif

uint64_t get_current_time(void) { return h.now; }
uint64_t delay(uint64_t us)
{
    budget();
    h.now += us / 1000U ? us / 1000U : 1U;
    return 0;
}
struct kshim_resources *kshim_resources_get(void) { return &h.resources; }
kshim_fb_status_t kshim_fb_init(kshim_framebuffer_t *fb,
                              const kshim_framebuffer_config_t *config)
{
    ++h.fb_calls;
    if (h.serial) return KSHIM_FB_ERR_INVALID_ARGUMENT;
    memset(fb, 0, sizeof(*fb));
    fb->render_address = config->render_address;
    fb->width = config->width; fb->height = config->height;
    fb->stride = config->stride; fb->format = config->format; fb->bpp = config->bpp;
    return KSHIM_FB_OK;
}
static int key_parse_output(QcomKeyDescriptor *descriptors, size_t capacity,
                            size_t *count)
{
    assert(descriptors && count && capacity == QCOM_KEYS_MAX);
    assert(h.keys_parse_count <= sizeof(key_descriptors) / sizeof(key_descriptors[0]));
    ++h.keys_build_calls;
    for (size_t i = 0; i < h.keys_parse_count; ++i)
        descriptors[i] = key_descriptors[i];
    *count = h.keys_parse_count;
    return h.keys_parse_result;
}
/* Keep the old path linkable so missing scope handling fails behaviorally. */
int QcomKeysBuildFromDeviceTree(const void *fdt, QcomKeyDescriptor *descriptors,
                               size_t capacity, size_t *count)
{
    assert(fdt == (const void *)(uintptr_t)boot_fdt());
    return key_parse_output(descriptors, capacity, count);
}
int QcomKeyFdtAdapterInit(QcomKeyFdtAdapter *adapter, const void *fdt)
{
    assert(adapter && fdt == (const void *)(uintptr_t)boot_fdt());
    assert(!h.keys_scope_active);
    ++h.keys_adapter_calls;
    if (h.keys_adapter_result != QCOM_KEYS_OK)
        return h.keys_adapter_result;
    *adapter = (QcomKeyFdtAdapter){.Blob = fdt, .Ops = {.Context = adapter}};
    h.keys_adapter = adapter;
    return QCOM_KEYS_OK;
}
void dt_parent_index_begin(const void *fdt)
{
    assert(!h.keys_scope_active && h.keys_adapter_result == QCOM_KEYS_OK);
    assert(h.keys_adapter_calls == h.keys_begin_calls + 1);
    assert(h.keys_adapter && h.keys_adapter->Blob == fdt);
    assert(fdt == (const void *)(uintptr_t)boot_fdt());
    ++h.keys_begin_calls;
    h.keys_scope_active = true;
}
int QcomKeysBuildFromFdt(const QcomKeyFdtOps *ops, QcomKeyDescriptor *descriptors,
                        size_t capacity, size_t *count)
{
    assert(h.keys_scope_active && h.keys_adapter);
    assert(ops == &h.keys_adapter->Ops && ops->Context == h.keys_adapter);
    return key_parse_output(descriptors, capacity, count);
}
void dt_parent_index_end(void)
{
    assert(h.keys_scope_active);
    assert(h.keys_build_calls == h.keys_begin_calls);
    ++h.keys_end_calls;
    h.keys_scope_active = false;
}
int QcomKeysInitEx(QcomKeys *keys, const QcomKeyDescriptor *descriptors,
                  size_t count, QcomKeyRead32 read32,
                  QcomKeyRead32Status read_status, void *context)
{
    assert(!h.keys_scope_active && h.keys_begin_calls == h.keys_end_calls);
    assert(keys == &Keys && descriptors && count == h.keys_parse_count && count > 0);
    assert(read32 == kshim_qcom_key_read32);
    assert(read_status == kshim_qcom_key_read32_status && context == NULL);
    for (size_t i = 0; i < count; ++i) {
        const QcomKeyDescriptor *actual = &descriptors[i], *expected = &key_descriptors[i];
        assert(actual->Base == expected->Base);
        assert(actual->RegisterOffset == expected->RegisterOffset);
        assert(actual->Mask == expected->Mask && actual->Code == expected->Code);
        assert(actual->Action == expected->Action && actual->Source == expected->Source);
        assert(actual->ActiveLow == expected->ActiveLow);
        assert(actual->DebounceSamples == expected->DebounceSamples);
        assert(actual->Reserved[0] == expected->Reserved[0]);
        assert(actual->Reserved[1] == expected->Reserved[1]);
    }
    ++h.keys_init_calls;
    memset(keys, 0, sizeof(*keys));
    return h.keys_init_result;
}
void QcomKeysSetDebounce(QcomKeys *keys, uint8_t samples)
{
    assert(keys == &Keys && samples == 2 && !h.keys_scope_active);
    assert(h.keys_init_calls == h.keys_debounce_calls + 1);
    ++h.keys_debounce_calls;
}
uint32_t kshim_qcom_key_read32(void *context, uint64_t address)
{ (void)context; (void)address; assert(!"Unexpected hardware read"); return 0; }
int kshim_qcom_key_read32_status(void *context, uint64_t address, uint32_t *value)
{ (void)context; (void)address; (void)value; assert(!"Unexpected hardware read"); return -1; }
int QcomKeysPoll(QcomKeys *keys, QcomKeyEvent *event)
{
    (void)keys; budget();
    /* Poison even NO_EVENT: the caller must check the real return code. */
    *event = (QcomKeyEvent){.Type = QCOM_KEY_EVENT_PRESS,
        .Action = QCOM_KEY_ACTION_SELECT, .Pressed = 1};
    if (h.key_next < h.key_count && h.keys[h.key_next].at <= h.now) {
        key_event_t e = h.keys[h.key_next++];
        *event = e.event;
        return e.result;
    }
    return QCOM_KEYS_NO_EVENT;
}
int kshim_console_try_read(char *character)
{
    budget();
    if (h.char_next < h.char_count && h.chars[h.char_next].at <= h.now) {
        *character = h.chars[h.char_next++].value;
        return 1;
    }
    return 0;
}
void kshim_console_write(const char *buffer, uint32_t length)
{ (void)buffer; (void)length; }
int kshim_mmu_is_enabled(void) { return 1; }
uint64_t kshim_mmu_root_table(void) { return 1; }
int kshim_mmu_configure_default(const void *fdt) { (void)fdt; return 0; }
int kshim_mmu_enable_current_cpu(void) { return 0; }
int kshim_mmu_prepare_handoff(void) { ++h.handoff_calls; return h.handoff_result; }
void kshim_boot_set_args(uint64_t fdt, uint64_t a, uint64_t b, uint64_t c)
{ (void)fdt; (void)a; (void)b; (void)c; }
void kshim_lvgl_set_scratch(void *base, size_t size) { (void)base; (void)size; }
int kshim_lvgl_init(kshim_lvgl_t *context, kshim_framebuffer_t *fb, QcomKeys *keys)
{
    (void)fb; (void)keys;
    ++h.init_calls;
    assert(!h.ui_alive);
    memset(context, 0, sizeof(*context));
    h.interacted = false;
    if (h.ui_init_result) return h.ui_init_result;
    h.ui_alive = true;
    return 0;
}
int kshim_lvgl_has_cjk_font(const kshim_lvgl_t *context) { (void)context; return 1; }
static void expect_description(const char *actual, const char *expected)
{
    if (expected) assert(actual && strcmp(actual, expected) == 0);
    else assert(actual == NULL);
}
static void expect_action_label(size_t row, const char *expected)
{
    assert(row < h.menu_count && h.entry_action_labels[row]);
    assert(strcmp(h.entry_action_labels[row], expected) == 0);
}
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
/* The language page always offers 简体中文 / English / Back, regardless of the
 * active locale, so the fixture recognises it by its two fixed leading rows. */
#define LANG_STR_CHINESE "简体中文"
#define LANG_STR_ENGLISH "English"
/* Runtime English labels differ from the compile-time (Chinese) macros. */
#define LANG_EN_ADVANCED "Advanced boot modes"
#define LANG_EN_ENTER    "Enter"
#define LANG_EN_RESTART  "Restart"
#define LANG_EN_OFF      "Power off"

static size_t lang_home_payloads(void)
{
    size_t rows = 0;
    for (size_t i = 0; i < h.payload_count; ++i)
        if (!(h.advanced_mask & (UINT64_C(1) << i))) ++rows;
    return rows;
}
static size_t lang_expected_home_rows(void)
{
    size_t rows = lang_home_payloads();
    if (h.advanced) ++rows; /* "Advanced boot modes" row */
    ++rows;                 /* language row */
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    rows += 2;              /* restart + power off */
#endif
    return rows;
}
static int lang_page_kind(const char *const *names, size_t count)
{
    if (count == 3 && strcmp(names[0], LANG_STR_CHINESE) == 0 &&
        strcmp(names[1], LANG_STR_ENGLISH) == 0)
        return 2;
    if (strcmp(names[count - 1], KSHIM_STR_BACK) == 0)
        return 1;
    return 0;
}
static void lang_capture(const char *const *names, const char *const *descriptions,
                         const char *const *action_labels, size_t count, size_t initial)
{
    assert(h.snap_count < LANG_PAGES);
    assert(count <= LANG_ROW_CAP);
    lang_page_t *page = &h.snap[h.snap_count++];
    memset(page, 0, sizeof(*page));
    page->kind = lang_page_kind(names, count);
    page->count = count;
    page->initial = initial;
    for (size_t i = 0; i < count; ++i) {
        assert(names[i] && names[i][0]);
        snprintf(page->names[i], sizeof(page->names[i]), "%s", names[i]);
        page->descriptions[i] = descriptions ? descriptions[i] : NULL;
        page->actions[i] = action_labels ? action_labels[i] : NULL;
    }
    if (page->kind == 0) {
        /* RED anchor: the language build must add exactly one language row, so
         * home grows from six rows to seven before any navigation happens. */
        assert(count == lang_expected_home_rows());
    } else if (page->kind == 2) {
        assert(count == 3);
        assert(strcmp(page->names[2], KSHIM_STR_BACK) == 0);
    }
}
#endif
int kshim_lvgl_set_entries_described(kshim_lvgl_t *context,
                                    const char *const *names,
                                    const char *const *descriptions,
                                    const char *const *action_labels,
                                    size_t count, size_t initial)
{
    (void)context;
    ++h.entries_calls;
    assert(count <= KSHIM_MENU_MAX_ENTRIES && initial < count);
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
    lang_capture(names, descriptions, action_labels, count, initial);
#endif
    if (!h.advanced) {
        assert(initial < h.payload_count);
        assert(count == expected_count());
    }
    h.menu_count = count; h.initial = initial; h.interacted = false;
    for (size_t i = 0; i < count; ++i) {
        assert(names[i] && names[i][0]);
        snprintf(h.labels[i], sizeof(h.labels[i]), "%s", names[i]);
        h.entry_descriptions[i] = descriptions ? descriptions[i] : NULL;
        h.entry_action_labels[i] = action_labels ? action_labels[i] : NULL;
        if (!h.advanced && i < h.payload_count) {
            assert(strcmp(names[i], h.names[i]) == 0);
            expect_description(h.entry_descriptions[i], h.descriptions[i]);
            expect_action_label(i, KSHIM_MENU_ACTION_BOOT);
        }
    }
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    if (h.advanced) {
#if !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
        h.advanced_page = strcmp(names[count - 1], KSHIM_STR_BACK) == 0;
        size_t row = 0;
        for (size_t i = 0; i < h.payload_count; ++i) {
            bool advanced = (h.advanced_mask & (UINT64_C(1) << i)) != 0;
            if (advanced == h.advanced_page) {
                assert(row < count && strcmp(names[row], h.names[i]) == 0);
                expect_description(h.entry_descriptions[row], h.descriptions[i]);
                expect_action_label(row, KSHIM_MENU_ACTION_BOOT);
                ++row;
            }
        }
        if (h.advanced_page) {
            assert(count == row + 4);
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_RECOVERY);
            expect_action_label(row, KSHIM_MENU_ACTION_ENTER);
            assert(strcmp(names[row++], KSHIM_STR_RECOVERY) == 0);
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_FASTBOOT);
            expect_action_label(row, KSHIM_MENU_ACTION_ENTER);
            assert(strcmp(names[row++], KSHIM_STR_FASTBOOT) == 0);
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_EDL);
            expect_action_label(row, KSHIM_MENU_ACTION_ENTER);
            assert(strcmp(names[row++], KSHIM_STR_EDL) == 0);
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_BACK);
            expect_action_label(row, KSHIM_MENU_ACTION_BACK);
            assert(strcmp(names[row++], KSHIM_STR_BACK) == 0);
        } else {
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_ADVANCED);
            expect_action_label(row, KSHIM_MENU_ACTION_ENTER);
            assert(strcmp(names[row++], KSHIM_STR_ADVANCED) == 0);
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_RESTART);
            expect_action_label(row, KSHIM_MENU_ACTION_RESTART);
            assert(strcmp(names[row++], KSHIM_STR_RESTART) == 0);
            expect_description(h.entry_descriptions[row], KSHIM_MENU_DESC_OFF);
            expect_action_label(row, KSHIM_MENU_ACTION_OFF);
            assert(strcmp(names[row++], KSHIM_STR_POWER_OFF) == 0);
#endif
            assert(count == row);
        }
#endif /* !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH */
        assert(h.page_count < MAX_PAGES);
        h.pages[h.page_count++] = (page_event_t){count, initial, h.advanced_page};
    }
#endif
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    if (!h.advanced) {
        assert(strcmp(h.labels[h.payload_count], KSHIM_STR_RESTART) == 0);
        assert(strcmp(h.labels[h.payload_count + 1], KSHIM_STR_POWER_OFF) == 0);
        expect_description(h.entry_descriptions[h.payload_count], KSHIM_MENU_DESC_RESTART);
        expect_description(h.entry_descriptions[h.payload_count + 1], KSHIM_MENU_DESC_OFF);
        expect_action_label(h.payload_count, KSHIM_MENU_ACTION_RESTART);
        expect_action_label(h.payload_count + 1, KSHIM_MENU_ACTION_OFF);
    }
#endif
    return h.entries_fail_at == h.entries_calls ? -1 : h.entries_result;
}
int kshim_lvgl_set_entries(kshim_lvgl_t *context, const char *const *names,
                          size_t count, size_t initial)
{
    return kshim_lvgl_set_entries_described(context, names, NULL, NULL, count, initial);
}
void kshim_lvgl_set_entry_roles(kshim_lvgl_t *context,
                                const kshim_menu_role_t *roles, size_t count)
{
    (void)context;
    ++h.role_calls;
    assert(count <= KSHIM_MENU_MAX_ENTRIES);
    h.role_count = count;
    for (size_t i = 0; i < count; ++i)
        h.roles[i] = roles ? roles[i] : KSHIM_MENU_ROLE_UNKNOWN;
}
void kshim_lvgl_set_countdown(kshim_lvgl_t *context, uint32_t timeout)
{
    (void)context;
    if (timeout) { h.countdown_first = timeout; ++h.countdown_started; }
    else ++h.countdown_zero;
}
void kshim_lvgl_set_status(kshim_lvgl_t *context, const char *text)
{
    (void)context; assert(text);
    ++h.status_calls;
    snprintf(h.status, sizeof(h.status), "%s", text);
}
void kshim_lvgl_frame(kshim_lvgl_t *context, uint32_t elapsed)
{
    (void)context; (void)elapsed; budget(); ++h.frames;
    assert(h.ui_alive);
    if (h.ui_next < h.ui_count && h.ui[h.ui_next].at <= h.now) {
        ui_event_t e = h.ui[h.ui_next++];
        if (e.index >= 0) h.pending = e.index;
        if (e.interacted) h.interacted = true;
    }
}
int kshim_lvgl_take_index(kshim_lvgl_t *context)
{ (void)context; int index = h.pending; h.pending = -1; return index; }
int kshim_lvgl_has_interacted(const kshim_lvgl_t *context)
{ (void)context; return h.interacted; }
void kshim_lvgl_deinit(kshim_lvgl_t *context)
{ (void)context; ++h.deinit_calls; h.ui_alive = false; }
int kshim_psci_init(const void *fdt)
{ ++h.psci_calls; h.psci_fdt = fdt; return h.psci_result; }
static void check_power_request(void)
{
    assert(CONFIG_KSHIM_MENU_POWER_ACTIONS);
    assert(h.psci_calls == 1 && h.psci_result == 0);
    assert(h.psci_fdt == (const void *)(uintptr_t)boot_fdt());
    assert(h.handoff_calls == (h.serial ? 1U : 0U));
    assert(h.deinit_calls == (h.serial ? 1U : 0U));
    assert(h.ui_alive == !h.serial);
}
int64_t kshim_psci_request_system_reset(void)
{ check_power_request(); ++h.reset_calls; return h.reset_result; }
int64_t kshim_psci_request_system_off(void)
{ check_power_request(); ++h.off_calls; return h.off_result; }

#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
int kshim_qcom_reboot_request(const void *fdt, enum kshim_qcom_reboot_target target)
{
    assert(h.advanced);
    assert(fdt == (const void *)(uintptr_t)boot_fdt());
    assert(h.handoff_calls == (h.serial ? 1U : 0U));
    assert(h.deinit_calls == (h.serial ? 1U : 0U));
    assert(h.ui_alive == !h.serial);
    if (!h.serial) assert(h.advanced_page);
    ++h.reboot_calls;
    h.reboot_target = target;
    h.reboot_fdt = fdt;
    return h.reboot_result;
}

#if !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
static size_t choose_advanced(size_t initial, uint32_t timeout, uint64_t mask)
{
    size_t selected = SIZE_MAX;
    h.advanced = true;
    h.advanced_mask = mask;
    assert(kshim_platform_select_advanced(boot_fdt(), h.names, h.payload_count,
                                         initial, timeout, mask, &selected) == 0);
    assert(selected < h.payload_count);
    expect_psci_init();
    assert(h.deinit_calls == 1U && h.handoff_calls == 1U);
    if (!h.serial && h.ui_init_result == 0 && h.entries_result == 0) {
        assert(h.init_calls == 1U && h.entries_calls == h.page_count);
        assert(h.page_count >= 1U);
    }
    return selected;
}
#endif
#endif

/* The whole legacy suite depends on the unconditional choose helpers above, so
 * the language target swaps it out for the locale tests in the #else branch. */
#if !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
static size_t choose_described(const char *const *descriptions, size_t initial,
                              uint32_t timeout, uint64_t mask)
{
    size_t selected = SIZE_MAX;
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    h.advanced = true;
    h.advanced_mask = mask;
#endif
    assert(kshim_platform_select_described(boot_fdt(), h.names, descriptions,
            h.payload_count, initial, timeout, mask, &selected) == 0);
    assert(selected < h.payload_count);
    expect_psci_init();
    assert(h.deinit_calls == 1U && h.handoff_calls == 1U);
    if (!h.serial) {
        assert(h.init_calls == 1U);
        assert(h.entries_calls == (h.advanced ? h.page_count : 1U));
        if (!h.advanced) assert(h.menu_count == expected_count());
    }
    return selected;
}

static void test_build_keys_scope(void)
{
    const void *fdt = (const void *)(uintptr_t)boot_fdt();

    setup(false, 1);
    assert(build_keys(NULL) == -1);
    assert(h.keys_adapter_calls == 0 && h.keys_build_calls == 0);
    assert(h.keys_begin_calls == 0 && h.keys_end_calls == 0 && !h.keys_scope_active);
    assert(h.keys_init_calls == 0 && h.keys_debounce_calls == 0);

    /* The old wrapper still links, but cannot satisfy this scope contract. */
    setup(false, 1);
    h.keys_parse_count = 3;
    assert(build_keys(fdt) == QCOM_KEYS_OK);
    assert(h.keys_adapter_calls == 1 && h.keys_build_calls == 1);
    assert(h.keys_begin_calls == 1 && h.keys_end_calls == 1 && !h.keys_scope_active);
    assert(h.keys_init_calls == 1 && h.keys_debounce_calls == 1);

    /* Validation must finish before begin; failure never opens a scope. */
    setup(false, 1);
    h.keys_adapter_result = QCOM_KEYS_EIO;
    assert(build_keys(fdt) == -1);
    assert(h.keys_adapter_calls == 1 && h.keys_build_calls == 0);
    assert(h.keys_begin_calls == 0 && h.keys_end_calls == 0 && !h.keys_scope_active);
    assert(h.keys_init_calls == 0 && h.keys_debounce_calls == 0);

    /* Partial output does not permit InitEx when descriptor construction fails. */
    setup(false, 1);
    h.keys_parse_result = QCOM_KEYS_ENOSPC;
    h.keys_parse_count = 2;
    assert(build_keys(fdt) == -1);
    assert(h.keys_adapter_calls == 1 && h.keys_build_calls == 1);
    assert(h.keys_begin_calls == 1 && h.keys_end_calls == 1 && !h.keys_scope_active);
    assert(h.keys_init_calls == 0 && h.keys_debounce_calls == 0);

    /* Real zero-key parsing returns EIO; also cover the explicit !count guard. */
    static const int zero_results[] = {QCOM_KEYS_EIO, QCOM_KEYS_OK};
    for (size_t i = 0; i < sizeof(zero_results) / sizeof(zero_results[0]); ++i) {
        setup(false, 1);
        h.keys_parse_result = zero_results[i];
        h.keys_parse_count = 0;
        assert(build_keys(fdt) == -1);
        assert(h.keys_adapter_calls == 1 && h.keys_build_calls == 1);
        assert(h.keys_begin_calls == 1 && h.keys_end_calls == 1 && !h.keys_scope_active);
        assert(h.keys_init_calls == 0 && h.keys_debounce_calls == 0);
    }

    /* InitEx runs after end and keeps its status plus the original debounce call. */
    setup(false, 1);
    h.keys_init_result = QCOM_KEYS_ENOSPC;
    assert(build_keys(fdt) == QCOM_KEYS_ENOSPC);
    assert(h.keys_adapter_calls == 1 && h.keys_build_calls == 1);
    assert(h.keys_begin_calls == 1 && h.keys_end_calls == 1 && !h.keys_scope_active);
    assert(h.keys_init_calls == 1 && h.keys_debounce_calls == 1);
}

static void test_described_payload_mapping(void)
{
    const uint64_t mask = (UINT64_C(1) << 1) | (UINT64_C(1) << 3);
    for (unsigned serial = 0; serial < 2; ++serial) {
        for (unsigned described = 0; described < 2; ++described) {
            setup(serial != 0, 4);
            /* Metadata follows original indexes, not label text or page rows. */
            h.names[0] = h.payload_labels[2];
            h.names[1] = h.payload_labels[0];
            h.names[2] = h.payload_labels[3];
            h.names[3] = h.payload_labels[1];
            if (described) {
                h.descriptions[0] = "Third payload metadata";
                h.descriptions[1] = NULL;
                h.descriptions[2] = "";
                h.descriptions[3] = "Second payload metadata";
            }
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
            if (serial) {
                text_at(5, "3\r"); text_at(30, "6\r");
                text_at(9000, "3\r"); text_at(9030, "2\r");
            } else {
                ui_at(5, 2, 0); ui_at(10, 5, 0);
                ui_at(9000, 2, 0); ui_at(9005, 1, 0);
            }
#else
            /* Disabled grouping ignores the mask but retains all metadata. */
            if (serial) text_at(5, "4\r");
            else ui_at(5, 3, 0);
#endif
            assert(choose_described(described ? h.descriptions : NULL,
                                    0, 8000, mask) == 3);
            assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
            assert(h.now >= 9005);
            if (!serial) {
                assert(h.page_count == 4 && h.pages[2].initial == 2);
                assert(h.countdown_started == 1 && h.countdown_zero >= 1);
                expect_description(h.entry_descriptions[0], NULL);
                expect_description(h.entry_descriptions[1], h.descriptions[3]);
            }
#else
            if (!serial) expect_description(h.entry_descriptions[3], h.descriptions[3]);
#endif
        }
    }
}

static void test_reject_count_50(void)
{
    for (unsigned serial = 0; serial < 2; ++serial) {
        setup(serial != 0, 50);
        struct { size_t before, value, after; } out = {11, 12345, 22};
        assert(kshim_platform_select(boot_fdt(), h.names, 50, 0, 20, &out.value) < 0);
        assert(out.before == 11 && out.value == 12345 && out.after == 22);
        assert(h.psci_calls == 0 && h.fb_calls == 0 && h.init_calls == 0);
        assert(h.steps == 0 && h.handoff_calls == 0 && h.deinit_calls == 0);
    }
}
static void test_ui_boundary_and_payload(void)
{
    setup(false, 49);
    ui_at(5, 48, 1);
    assert(choose(0, 20) == 48);
    assert(h.menu_count == (CONFIG_KSHIM_MENU_POWER_ACTIONS ? 51U : 49U));
    assert(h.reset_calls == 0 && h.off_calls == 0);
}
static void test_ui_timeout_and_interaction(void)
{
    setup(false, 2);
    assert(choose(1, 20) == 1); /* initial focus is not interaction */
    assert(h.now == 20 && h.countdown_zero == 0);
    setup(false, 2);
    ui_at(5, -1, 1);
    ui_at(60, 1, 1);
    assert(choose(0, 20) == 1);
    assert(h.now >= 60 && h.countdown_zero == 1 && h.countdown_first == 20);
    assert(h.status_calls && h.status[0]);
    setup(false, 2);
    ui_at(5, 100, 0);
    ui_at(10, 1, 0);
    assert(choose(0, 20) == 1);
    assert(h.ui_next == 2 && h.now == 10);
}
static void test_ui_init_failure_falls_back(void)
{
    setup(false, 2);
    h.ui_init_result = -1;
    text_at(5, "2\r");
    assert(choose(0, 20) == 1);
    assert(h.init_calls == 1 && h.entries_calls == 0);
    setup(false, 2);
    h.entries_result = -1;
    text_at(5, "2\r");
    assert(choose(0, 20) == 1);
    assert(h.init_calls == 1 && h.entries_calls == 1);
}
static void test_serial_timeout_and_digits(void)
{
    setup(true, 2);
    assert(choose(1, 20) == 1 && h.now == 20);
    setup(true, 2);
    char_at(5, '2'); char_at(60, '\r');
    assert(choose(0, 20) == 1 && h.now >= 60);
    setup(true, 10);
    char_at(5, '1'); char_at(10, '0'); char_at(60, '\n');
    assert(choose(0, 20) == 9 && h.now >= 60 && h.char_next == 3);
    setup(true, 2);
    text_at(5, "0x9"); /* none is a valid prefix for this menu (2 or 4 entries) */
    assert(choose(1, 20) == 1 && h.now == 20);
    setup(true, 2);
    char_at(5, 27);
    assert(choose(1, 20) == 1 && h.now == 5);
}
static void test_serial_key_contract(void)
{
    setup(true, 2);
    key_at(5, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_DOWN, 1);
    key_at(60, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
    assert(choose(0, 20) == 1 && h.now >= 60);
    setup(true, 2);
    key_at(5, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_UP, 1);
    key_at(60, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
    assert(choose(1, 20) == 0 && h.now >= 60);
    setup(true, 2);
    key_at(0, QCOM_KEYS_NO_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_DOWN, 1);
    key_at(5, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_RELEASE, QCOM_KEY_ACTION_DOWN, 1);
    key_at(10, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, (QcomKeyAction)99, 1);
    key_at(15, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_DOWN, 0);
    assert(choose(1, 20) == 1 && h.now == 20 && h.key_next == 4);
}
static void test_serial_boundary(void)
{
    setup(true, 49);
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    text_at(0, "50\r");
    text_at(30, "51\r");
    text_at(70, "49\r");
    assert(choose(0, 20) == 48 && h.now >= 70);
    assert(h.reset_calls == 1 && h.off_calls == 1);
#else
    text_at(0, "49"); char_at(60, '\r');
    assert(choose(0, 20) == 48 && h.now >= 60);
    assert(h.reset_calls == 0 && h.off_calls == 0);
#endif
}
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
static void test_power_actions(void)
{
    static const int64_t results[] = {-1, 0, 1};
    for (unsigned serial = 0; serial < 2; ++serial) {
        for (unsigned action = 0; action < 2; ++action) {
            for (unsigned result = 0; result < 3; ++result) {
                setup(serial != 0, 2);
                h.reset_result = h.off_result = results[result];
                if (serial) {
                    char_at(5, action ? '4' : '3'); char_at(10, '\r');
                    text_at(60, "2\r");
                } else {
                    ui_at(5, action ? 3 : 2, 0);
                    ui_at(35, -1, 0); /* idle after the old deadline */
                    ui_at(60, 1, 0);
                }
                assert(choose(0, 20) == 1 && h.now >= 60);
                assert(h.reset_calls == (action ? 0U : 1U));
                assert(h.off_calls == (action ? 1U : 0U));
                if (!serial) {
                    assert(h.countdown_zero == 1 && h.ui_next == 3);
                    assert(h.status_calls && h.status[0]);
                }
            }
            setup(serial != 0, 2);
            h.psci_result = -1;
            if (serial) {
                char_at(5, action ? '4' : '3'); char_at(10, '\r');
                text_at(60, "2\r");
            } else {
                ui_at(5, action ? 3 : 2, 0); ui_at(60, 1, 0);
            }
            assert(choose(0, 20) == 1 && h.now >= 60);
            assert(h.reset_calls == 0 && h.off_calls == 0);
            if (!serial) assert(h.countdown_zero == 1 && h.status_calls && h.status[0]);
        }
    }
}
static void test_serial_key_power_actions(void)
{
    setup(true, 2);
    key_at(5, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_DOWN, 1);
    key_at(10, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
    key_at(35, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_DOWN, 1);
    key_at(40, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
    key_at(65, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_UP, 1);
    key_at(70, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_UP, 1);
    key_at(75, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
    assert(choose(1, 20) == 1 && h.now >= 75);
    assert(h.reset_calls == 1 && h.off_calls == 1 && h.key_next == 7);
}
static void test_serial_numeric_power_preserves_key_focus(void)
{
    for (unsigned action = 0; action < 2; ++action) {
        setup(true, 2);
        char_at(5, action ? '4' : '3'); char_at(10, '\r');
        key_at(60, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS, QCOM_KEY_ACTION_SELECT, 1);
        assert(choose(1, 20) == 1 && h.now >= 60);
        assert(h.reset_calls == (action ? 0U : 1U));
        assert(h.off_calls == (action ? 1U : 0U));
        assert(h.key_next == 1);
    }
}
#endif
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
static void test_advanced_payload_mapping(void)
{
    for (unsigned serial = 0; serial < 2; ++serial) {
        setup(serial != 0, 4);
        if (serial) {
            text_at(5, "4\r");
            text_at(30, "1\r");
        } else {
            ui_at(5, 3, 0);
            ui_at(10, 0, 0);
        }
        assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 3);
        assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);
        if (!serial) {
            assert(h.page_count == 2);
            assert(!h.pages[0].advanced && h.pages[0].count ==
                   (CONFIG_KSHIM_MENU_POWER_ACTIONS ? 6U : 4U));
            assert(h.pages[0].initial == 1);
            assert(h.pages[1].advanced && h.pages[1].count == 5);
            assert(h.pages[1].initial == 0);
        }

        /* Sparse payload groups must not return an index within the page. */
        setup(serial != 0, 4);
        if (serial) {
            text_at(5, "3\r");
            text_at(30, "2\r");
        } else {
            ui_at(5, 2, 0);
            ui_at(10, 1, 0);
        }
        assert(choose_advanced(0, 8000, (UINT64_C(1) << 1) |
                               (UINT64_C(1) << 3)) == 3);
    }
}

static void test_advanced_default_timeout(void)
{
    for (unsigned serial = 0; serial < 2; ++serial) {
        for (unsigned initial = 1; initial <= 3; initial += 2) {
            setup(serial != 0, 4);
            assert(choose_advanced(initial, 8000, UINT64_C(1) << 3) == initial);
            assert(h.now == 8000);
            assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);
            if (!serial) {
                assert(h.page_count == 1 && h.countdown_zero == 0);
                assert(h.countdown_first == 8000 && h.countdown_started == 1);
                assert(h.pages[0].advanced == (initial == 3));
                assert(h.pages[0].initial == (initial == 3 ? 0U : 1U));
            }
        }
    }
}

static void test_advanced_back_cancels_timeout(void)
{
    setup(false, 4);
    ui_at(5, 3, 0);     /* Enter advanced without a separate interaction flag. */
    ui_at(10, 4, 0);    /* Back. */
    ui_at(9000, 3, 0);  /* Still waiting after the original eight-second deadline. */
    ui_at(9005, 0, 0);
    assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 3);
    assert(h.now >= 9005 && h.ui_next == 4 && h.page_count == 4);
    assert(!h.pages[2].advanced && h.pages[2].initial == 3);
    assert(h.countdown_zero >= 1 && h.countdown_started == 1);
    assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);

    for (unsigned escape = 0; escape < 2; ++escape) {
        setup(true, 4);
        text_at(5, "4\r");
        if (escape) char_at(30, 27);
        else text_at(30, "5\r");
        /* SELECT must reopen advanced because Back restores that main focus. */
        key_at(9000, QCOM_KEYS_EVENT, QCOM_KEY_EVENT_PRESS,
               QCOM_KEY_ACTION_SELECT, 1);
        text_at(9020, "1\r");
        assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 3);
        assert(h.now >= 9020 && h.key_next == 1 && h.char_next == h.char_count);
        assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);
    }

    /* Primary-page ESC keeps the legacy shortcut to the original default. */
    setup(true, 4);
    char_at(5, 27);
    assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 1 && h.now == 5);
}

static void test_advanced_reboot_failures(void)
{
    static const enum kshim_qcom_reboot_target targets[] = {
        KSHIM_QCOM_REBOOT_RECOVERY, KSHIM_QCOM_REBOOT_BOOTLOADER,
        KSHIM_QCOM_REBOOT_EDL,
    };
    static const int results[] = {
        KSHIM_QCOM_REBOOT_UNSUPPORTED, KSHIM_QCOM_REBOOT_INVALID_DT,
        KSHIM_QCOM_REBOOT_IO_FAILED, KSHIM_QCOM_REBOOT_VERIFY_FAILED,
        KSHIM_QCOM_REBOOT_RESET_RETURNED, KSHIM_QCOM_REBOOT_ROLLBACK_FAILED,
        0, 1, /* A successful hardware reset never returns. */
    };
    for (unsigned serial = 0; serial < 2; ++serial) {
        for (size_t target = 0; target < sizeof(targets) / sizeof(targets[0]); ++target) {
            for (size_t result = 0; result < sizeof(results) / sizeof(results[0]); ++result) {
                setup(serial != 0, 4);
                h.reboot_result = results[result];
                if (serial) {
                    text_at(5, "4\r");
                    char_at(30, (char)('2' + target)); char_at(35, '\r');
                    text_at(90, "1\r");
                } else {
                    ui_at(5, 3, 0);
                    ui_at(10, (int)target + 1, 0);
                    ui_at(90, 0, 0);
                }
                /* A failed mode switch must remain on advanced, past timeout. */
                assert(choose_advanced(1, 20, UINT64_C(1) << 3) == 3);
                assert(h.now >= 90 && h.reboot_calls == 1);
                assert(h.reboot_target == (int)targets[target]);
                assert(h.reboot_fdt == (const void *)(uintptr_t)boot_fdt());
                assert(h.reset_calls == 0 && h.off_calls == 0);
                if (!serial) {
                    assert(h.page_count == 2 && h.advanced_page);
                    assert(h.countdown_zero >= 1 && h.countdown_started == 1);
#if CONFIG_KSHIM_MENU_CHINESE
                    const char *expected = results[result] == KSHIM_QCOM_REBOOT_ROLLBACK_FAILED
                        ? "切换启动模式失败，恢复原设置失败" : "切换启动模式失败";
#else
                    const char *expected = results[result] == KSHIM_QCOM_REBOOT_ROLLBACK_FAILED
                        ? "Boot mode switch failed; restoring the original settings failed"
                        : "Boot mode switch failed";
#endif
                    assert(strcmp(h.status, expected) == 0);
                }
            }
        }
    }
}

#if CONFIG_KSHIM_MENU_POWER_ACTIONS
static void test_advanced_power_failures(void)
{
    static const int64_t results[] = {-1, 0, 1};
    for (unsigned serial = 0; serial < 2; ++serial) {
        for (unsigned action = 0; action < 2; ++action) {
            for (size_t result = 0; result < sizeof(results) / sizeof(results[0]); ++result) {
                setup(serial != 0, 4);
                h.reset_result = h.off_result = results[result];
                if (serial) {
                    char_at(5, action ? '6' : '5'); char_at(10, '\r');
                    text_at(60, "2\r");
                } else {
                    ui_at(5, action ? 5 : 4, 0);
                    ui_at(60, 1, 0);
                }
                assert(choose_advanced(1, 20, UINT64_C(1) << 3) == 1);
                assert(h.now >= 60 && h.reboot_calls == 0);
                assert(h.reset_calls == (action ? 0U : 1U));
                assert(h.off_calls == (action ? 1U : 0U));
                if (!serial) assert(h.page_count == 1 && !h.advanced_page);
            }
        }
    }
}
#endif

static void test_advanced_capacity(void)
{
    for (unsigned serial = 0; serial < 2; ++serial) {
        /* 48 ordinary payloads + advanced + two power actions fills 51 rows. */
        setup(serial != 0, 48);
        if (serial) text_at(5, "48\r");
        else ui_at(5, 47, 0);
        assert(choose_advanced(0, 20, 0) == 47);
        if (!serial) assert(h.pages[0].count ==
                            (CONFIG_KSHIM_MENU_POWER_ACTIONS ? 51U : 49U));

        /* 47 advanced payloads + three modes + Back also fills 51 rows. */
        setup(serial != 0, 49);
        if (serial) {
            text_at(5, "3\r"); text_at(30, "51\r");
            text_at(60, "3\r"); text_at(90, "47\r");
        } else {
            ui_at(5, 2, 0); ui_at(10, 50, 0);
            ui_at(15, 2, 0); ui_at(20, 46, 0);
        }
        assert(choose_advanced(48, 8000, (UINT64_C(1) << 47) - 1) == 46);
        if (!serial) {
            assert(h.page_count == 4 && h.pages[1].count == 51);
            assert(h.pages[2].initial == 2 && h.pages[3].count == 51);
        }
        assert(h.reboot_calls == 0 && h.reset_calls == 0 && h.off_calls == 0);

        static const struct { size_t count; uint64_t mask; } invalid[] = {
            {50, 0}, {49, (UINT64_C(1) << 48) - 1},
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
            {49, 0},
#endif
        };
        for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
            setup(serial != 0, invalid[i].count);
            struct { size_t before, value, after; } out = {11, 12345, 22};
            assert(kshim_platform_select_advanced(boot_fdt(), h.names, h.payload_count,
                    0, 8000, invalid[i].mask, &out.value) == -1);
            assert(out.before == 11 && out.value == 12345 && out.after == 22);
            assert(h.psci_calls == 0 && h.fb_calls == 0 && h.init_calls == 0);
            assert(h.steps == 0 && h.handoff_calls == 0 && h.deinit_calls == 0);
            assert(h.reboot_calls == 0);
        }
    }
}

static void test_advanced_fallback_and_handoff(void)
{
    for (unsigned entries_fail = 0; entries_fail < 2; ++entries_fail) {
        setup(false, 4);
        if (entries_fail) h.entries_result = -1;
        else h.ui_init_result = -1;
        text_at(5, "4\r"); text_at(30, "1\r");
        assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 3);
        assert(h.init_calls == 1 && h.entries_calls == entries_fail);
    }
    /* A page rebuild failure must preserve that page and the cancelled timer. */
    setup(false, 4);
    h.entries_fail_at = 2;
    ui_at(5, 3, 0);
    text_at(9000, "1\r");
    assert(choose_advanced(1, 8000, UINT64_C(1) << 3) == 3);
    assert(h.now >= 9000 && h.entries_calls == 2 && h.page_count == 2);
    assert(h.pages[1].advanced && h.countdown_started == 1);
    assert(h.countdown_zero >= 1 && h.reboot_calls == 0);

    for (unsigned serial = 0; serial < 2; ++serial) {
        setup(serial != 0, 4);
        h.advanced = true;
        h.advanced_mask = UINT64_C(1) << 3;
        h.handoff_result = -1;
        ui_at(5, 1, 0);
        text_at(5, "2\r");
        size_t selected = 12345;
        assert(kshim_platform_select_advanced(boot_fdt(), h.names, 4, 1,
                8000, h.advanced_mask, &selected) == -2);
        assert(selected == (serial ? 12345U : 1U));
        assert(h.deinit_calls == 1 && h.handoff_calls == 1 && !h.ui_alive);
        assert(h.char_next == 0 && h.reboot_calls == 0);
    }
}
#endif

#else /* !CONFIG_KSHIM_MENU_LANGUAGE_SWITCH */

/* Home -> language -> English -> home again.  The reopened home page must be
 * rebuilt in English while the payload names survive unchanged. */
static void test_language_page_switch(void)
{
    setup(false, 4);
    h.advanced = true;
    h.advanced_mask = UINT64_C(1) << 3;
    ui_at(5, 4, 0);     /* Row four of the home menu is the language entry. */
    ui_at(10, 1, 0);    /* English is the second row of the language page. */
    ui_at(9000, 1, 0);  /* Reopened home page: choose the original payload. */
    size_t selected = SIZE_MAX;
    assert(kshim_platform_select_described(boot_fdt(), h.names, h.descriptions,
            4, 0, 8000, h.advanced_mask, &selected) == 0);
    assert(selected == 1);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    assert(h.now >= 9000);
    expect_psci_init();

    /* Each page is captured as it is (re)built: home, language, home. */
    assert(h.snap_count == 3);
    assert(h.snap[0].kind == 0 && h.snap[0].count == 7);
    assert(h.snap[1].kind == 2 && h.snap[1].count == 3);
    assert(strcmp(h.snap[1].names[0], LANG_STR_CHINESE) == 0);
    assert(strcmp(h.snap[1].names[1], LANG_STR_ENGLISH) == 0);
    assert(h.snap[2].kind == 0 && h.snap[2].count == 7);
    assert(h.snap[2].initial == 4); /* Focus returns to the language row. */

    /* The rebuilt home page now renders the selected locale. */
    assert(strcmp(h.snap[2].names[3], LANG_EN_ADVANCED) == 0);
    assert(h.snap[2].actions[3] && strcmp(h.snap[2].actions[3], LANG_EN_ENTER) == 0);
    assert(strcmp(h.snap[2].names[5], LANG_EN_RESTART) == 0);
    assert(strcmp(h.snap[2].names[6], LANG_EN_OFF) == 0);

    /* Payload names carry no locale, so they survive the switch unchanged. */
    assert(strcmp(h.snap[2].names[0], h.payload_labels[0]) == 0);
    assert(strcmp(h.snap[2].names[1], h.payload_labels[1]) == 0);
    assert(strcmp(h.snap[2].names[2], h.payload_labels[2]) == 0);
}

/* Back (UI row two / serial ESC) leaves the locale untouched. */
static void test_language_page_navigation(void)
{
    setup(false, 4);
    h.advanced = true;
    h.advanced_mask = UINT64_C(1) << 3;
    ui_at(5, 4, 0);
    ui_at(10, 2, 0);    /* Back row. */
    ui_at(9000, 1, 0);
    size_t selected = SIZE_MAX;
    assert(kshim_platform_select_described(boot_fdt(), h.names, h.descriptions,
            4, 0, 8000, h.advanced_mask, &selected) == 0);
    assert(selected == 1);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    assert(h.now >= 9000);
    assert(h.snap_count == 3);
    assert(h.snap[0].kind == 0 && h.snap[0].count == 7);
    assert(h.snap[1].kind == 2 && h.snap[1].count == 3);
    assert(h.snap[2].kind == 0 && h.snap[2].count == 7);

    setup(true, 4);
    h.advanced = true;
    h.advanced_mask = UINT64_C(1) << 3;
    text_at(5, "5\r");  /* Open the language entry from the serial menu. */
    char_at(30, 27);    /* ESC on the language page is the same Back. */
    text_at(60, "2\r");
    size_t serial = SIZE_MAX;
    assert(kshim_platform_select_described(boot_fdt(), h.names, h.descriptions,
            4, 0, 8000, h.advanced_mask, &serial) == 0);
    assert(serial == 1);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    assert(h.now >= 60);
}

/* Known roles resolve through the runtime table, while an UNKNOWN row keeps the
 * caller's exact text even when it matches a known localized label. */
static void test_language_localized_roles(void)
{
    static const kshim_menu_role_t roles[4] = {
        KSHIM_MENU_ROLE_UNKNOWN, KSHIM_MENU_ROLE_ANDROID,
        KSHIM_MENU_ROLE_INTERNAL, KSHIM_MENU_ROLE_TF,
    };
    setup(false, 4);
    h.advanced = true;
    h.advanced_mask = 0;
    for (size_t i = 0; i < 4; ++i) h.descriptions[i] = "caller description";
    /* The UNKNOWN row carries the localized Android label verbatim; the mapping
     * must come from the role, never from comparing displayed text. */
    h.names[0] = kshim_menu_role_name(KSHIM_MENU_ROLE_ANDROID, NULL);
    ui_at(5, 2, 0);
    size_t selected = SIZE_MAX;
    assert(kshim_platform_select_localized(boot_fdt(), h.names, h.descriptions, roles,
            4, 0, 20, 0, &selected) == 0);
    assert(selected == 2);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    expect_psci_init();
    /* 4 payloads + advanced + language + restart + power off. */
    assert(h.role_calls == 1 && h.role_count == 8);
    assert(h.roles[0] == KSHIM_MENU_ROLE_UNKNOWN);
    assert(h.roles[1] == KSHIM_MENU_ROLE_ANDROID);
    assert(h.roles[2] == KSHIM_MENU_ROLE_INTERNAL);
    assert(h.roles[3] == KSHIM_MENU_ROLE_TF);
    assert(h.roles[4] == KSHIM_MENU_ROLE_ADVANCED);
    assert(h.roles[5] == KSHIM_MENU_ROLE_LANGUAGE);
    assert(h.roles[6] == KSHIM_MENU_ROLE_RESTART);
    assert(h.roles[7] == KSHIM_MENU_ROLE_OFF);
    assert(strcmp(h.labels[0], h.names[0]) == 0);
    assert(strcmp(h.labels[1], kshim_menu_role_name(KSHIM_MENU_ROLE_ANDROID, NULL)) == 0);
    assert(strcmp(h.labels[2], kshim_menu_role_name(KSHIM_MENU_ROLE_INTERNAL, NULL)) == 0);
    assert(strcmp(h.labels[3], kshim_menu_role_name(KSHIM_MENU_ROLE_TF, NULL)) == 0);
    /* Known-role descriptions are re-derived, not the stale caller text. */
    assert(strcmp(h.entry_descriptions[0], "caller description") == 0);
    assert(strcmp(h.entry_descriptions[1],
        kshim_menu_role_description(KSHIM_MENU_ROLE_ANDROID, "caller description")) == 0);
}

/* The 49-payload input cap is preserved: with the language page and advanced
 * row the home page fills exactly 53 rows and 50 payloads are still rejected. */
static void test_language_capacity(void)
{
    setup(false, 49);
    h.advanced = true;
    h.advanced_mask = 0;
    ui_at(5, 48, 0);
    size_t selected = SIZE_MAX;
    assert(kshim_platform_select_described(boot_fdt(), h.names, h.descriptions,
            49, 0, 20, 0, &selected) == 0);
    assert(selected == 48);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    expect_psci_init();
    assert(h.snap_count == 1);
    assert(h.snap[0].kind == 0 && h.snap[0].count == 53);

    setup(false, 50);
    struct { size_t before, value, after; } out = {11, 12345, 22};
    assert(kshim_platform_select_localized(boot_fdt(), h.names, h.descriptions, NULL,
            50, 0, 20, 0, &out.value) < 0);
    assert(out.before == 11 && out.value == 12345 && out.after == 22);
    assert(h.psci_calls == 0 && h.fb_calls == 0 && h.init_calls == 0);
    assert(h.entries_calls == 0 && h.handoff_calls == 0 && h.deinit_calls == 0);
}

/* A failed page rebuild after a live language change falls back to the serial
 * menu without firing any dangerous action, and the selection still resolves
 * through the original payload index. */
static void test_language_ui_fallback(void)
{
    setup(false, 4);
    h.advanced = true;
    h.advanced_mask = UINT64_C(1) << 3;
    h.entries_fail_at = 3; /* home and language page succeed; rebuilt home fails */
    ui_at(5, 4, 0);
    ui_at(10, 1, 0);
    text_at(9000, "2\r");
    size_t selected = SIZE_MAX;
    assert(kshim_platform_select_described(boot_fdt(), h.names, h.descriptions,
            4, 0, 20, h.advanced_mask, &selected) == 0);
    assert(selected == 1);
    assert(h.reset_calls == 0 && h.off_calls == 0 && h.reboot_calls == 0);
    assert(h.snap_count == 3);
    assert(h.snap[2].kind == 0);
    assert(h.now >= 9000);
    expect_psci_init();
}

#endif /* CONFIG_KSHIM_MENU_LANGUAGE_SWITCH */

int main(void)
{
#if CONFIG_KSHIM_MENU_LANGUAGE_SWITCH
    test_language_page_switch();
    test_language_page_navigation();
    test_language_localized_roles();
    test_language_capacity();
    test_language_ui_fallback();
#else
    test_build_keys_scope();
    test_described_payload_mapping();
    test_reject_count_50();
    test_ui_boundary_and_payload();
    test_ui_timeout_and_interaction();
    test_ui_init_failure_falls_back();
    test_serial_timeout_and_digits();
    test_serial_key_contract();
    test_serial_boundary();
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    test_power_actions();
    test_serial_key_power_actions();
    test_serial_numeric_power_preserves_key_focus();
#endif
#if CONFIG_KSHIM_MENU_ADVANCED_ACTIONS
    test_advanced_payload_mapping();
    test_advanced_default_timeout();
    test_advanced_back_cancels_timeout();
    test_advanced_reboot_failures();
#if CONFIG_KSHIM_MENU_POWER_ACTIONS
    test_advanced_power_failures();
#endif
    test_advanced_capacity();
    test_advanced_fallback_and_handoff();
#endif
#endif /* CONFIG_KSHIM_MENU_LANGUAGE_SWITCH */
    puts("Real platform menu regression checks passed");
    return 0;
}
