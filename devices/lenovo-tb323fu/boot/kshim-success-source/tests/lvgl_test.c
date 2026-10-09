/* Exercise the real LVGL renderer and both physical-key and touch paths. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <lvgl.h>
#include <lvgl_port.h>
#include <kshim_ui_fonts.h>

#ifdef CONFIG_KSHIM_UEFI_TEST_MENU
#define DEFAULT_ENTRY_COUNT 2U
#define DEFAULT_SECOND_ACTION KSHIM_MENU_RECOVERY
#else
#define DEFAULT_ENTRY_COUNT 3U
#define DEFAULT_SECOND_ACTION KSHIM_MENU_RECOVERY
#endif

typedef struct test_ui {
    uint8_t *storage;
    size_t storage_bytes;
    size_t frame_bytes;
    unsigned guard;
    unsigned width;
    unsigned height;
    unsigned content_bytes;
    kshim_framebuffer_t framebuffer;
    kshim_lvgl_t ui;
    QcomKeys keys;
    QcomKeyDescriptor descriptors[3];
} test_ui_t;

static uint32_t Inputs[3];

static uint32_t ReadInput(void *Context, uint64_t Address)
{
    (void)Context;
    assert(Address >= 1U && Address <= 3U);
    return Inputs[Address - 1U];
}

static void Advance(kshim_lvgl_t *Ui, unsigned Milliseconds)
{
    while (Milliseconds != 0U) {
        unsigned Step = Milliseconds > 16U ? 16U : Milliseconds;
        kshim_lvgl_frame(Ui, Step);
        Milliseconds -= Step;
    }
}

static void PressKey(kshim_lvgl_t *Ui, unsigned Index)
{
    Inputs[Index] = 1U;
    Advance(Ui, 112U);
    Inputs[Index] = 0U;
    Advance(Ui, 112U);
}

static void InitStableUi(test_ui_t *Test, unsigned Width, unsigned Height,
                         kshim_fb_format_t Format)
{
    memset(Test, 0, sizeof(*Test));
    static const uint32_t Codes[] = {
        QCOM_KEYCODE_VOLUMEUP, QCOM_KEYCODE_VOLUMEDOWN,
        QCOM_KEYCODE_POWER,
    };
    unsigned Bpp = kshim_fb_format_bpp(Format);
    Test->content_bytes = (Width * Bpp + 7U) / 8U;
    unsigned Stride = Test->content_bytes + 13U;
    Test->frame_bytes = (size_t)Stride * Height;
    Test->guard = 64U;
    Test->storage_bytes = Test->frame_bytes + Test->guard * 2U;
    Test->storage = malloc(Test->storage_bytes);
    assert(Test->storage != NULL);
    memset(Test->storage, 0xa5, Test->storage_bytes);
    Test->width = Width;
    Test->height = Height;
    memset(Inputs, 0, sizeof(Inputs));

    for (unsigned Index = 0; Index < 3U; Index++)
        assert(QcomKeyMakeGpioAt(&Test->descriptors[Index], Index + 1U,
                                 1U, Codes[Index], 0U) == 0);
    assert(QcomKeysInit(&Test->keys, Test->descriptors, 3U,
                        ReadInput, NULL) == 0);
    kshim_framebuffer_config_t Config = {
        .render_address = (uintptr_t)(Test->storage + Test->guard),
        .width = Width,
        .height = Height,
        .stride = Stride,
        .bpp = Bpp,
        .format = Format,
        .foreground = Bpp == 1U ? 1U : UINT32_MAX,
        .background = 0U,
        .buffer_size = Test->frame_bytes,
    };
    assert(kshim_fb_init(&Test->framebuffer, &Config) == KSHIM_FB_OK);
    assert(kshim_fb_clear(&Test->framebuffer) == KSHIM_FB_OK);
    assert(kshim_lvgl_init(&Test->ui, &Test->framebuffer, &Test->keys) == 0);
    assert(kshim_lvgl_ready(&Test->ui));
}

static uint64_t FrameHash(const test_ui_t *Test)
{
    const uint8_t *Frame = Test->storage + Test->guard;
    uint64_t Hash = UINT64_C(14695981039346656037);
    unsigned Changed = 0U;
    for (unsigned Y = 0; Y < Test->height; Y++) {
        for (unsigned X = 0; X < Test->content_bytes; X++) {
            uint8_t Value = Frame[(size_t)Y * Test->framebuffer.stride + X];
            Hash = (Hash ^ Value) * UINT64_C(1099511628211);
            Changed += Value != 0U;
        }
    }
    assert(Changed > Test->height);
    return Hash;
}

static void CheckGuards(const test_ui_t *Test)
{
    for (unsigned Index = 0; Index < Test->guard; Index++) {
        assert(Test->storage[Index] == 0xa5);
        assert(Test->storage[Test->guard + Test->frame_bytes + Index] == 0xa5);
    }
    const uint8_t *Frame = Test->storage + Test->guard;
    for (unsigned Y = 0; Y < Test->height; Y++)
        for (unsigned X = Test->content_bytes;
             X < Test->framebuffer.stride; X++)
            assert(Frame[(size_t)Y * Test->framebuffer.stride + X] == 0U);
}

static void DestroyUi(test_ui_t *Test)
{
    kshim_lvgl_deinit(&Test->ui);
    assert(!kshim_lvgl_ready(&Test->ui));
    CheckGuards(Test);
    free(Test->storage);
}

static void TestFormats(void)
{
    uint64_t Hashes[6] = {0};
    for (int Format = KSHIM_FB_FORMAT_MONO1_LSB;
         Format <= KSHIM_FB_FORMAT_ARGB8888; Format++) {
        test_ui_t Test;
        InitStableUi(&Test, 320U, 240U, (kshim_fb_format_t)Format);
        Advance(&Test.ui, KSHIM_UI_ENTRY_DURATION_MS + 64U);
        Hashes[Format - 1] = FrameHash(&Test);
        assert(Test.ui.TextureWidth > 0U && Test.ui.TextureHeight > 0U);
        assert(lv_group_get_obj_count(Test.ui.Group) == DEFAULT_ENTRY_COUNT);
        DestroyUi(&Test);
    }
    assert(Hashes[0] != Hashes[2]);
    assert(Hashes[3] != Hashes[4]);
}

static void TestDefaultKeys(void)
{
    test_ui_t Test;
    InitStableUi(&Test, 640U, 360U, KSHIM_FB_FORMAT_ARGB8888);
    lv_group_t *Group = Test.ui.Group;
    lv_obj_t *First = lv_group_get_obj_by_index(Group, 0U);
    lv_obj_t *Second = lv_group_get_obj_by_index(Group, 1U);
    assert(lv_group_get_focused(Group) == First);
    assert(kshim_lvgl_take_selection(&Test.ui) == KSHIM_MENU_NONE);
    assert(kshim_lvgl_take_index(&Test.ui) == -1);

    PressKey(&Test.ui, 1U);
    assert(lv_group_get_focused(Group) == Second);
    lv_obj_t *SecondLabel = lv_obj_get_child(Second, 0U);
    const lv_anim_t *Focus = lv_anim_get(SecondLabel, NULL);
    assert(Focus != NULL && Focus->duration == KSHIM_UI_FOCUS_DURATION_MS);

    PressKey(&Test.ui, 2U);
    assert(kshim_lvgl_take_selection(&Test.ui) == DEFAULT_SECOND_ACTION);
    assert(kshim_lvgl_take_selection(&Test.ui) == KSHIM_MENU_NONE);
    assert(kshim_lvgl_take_index(&Test.ui) == 1);
    assert(kshim_lvgl_take_index(&Test.ui) == -1);
    const lv_anim_t *Confirm = lv_anim_get(Test.ui.BootButton, NULL);
    assert(Confirm != NULL &&
           Confirm->duration == KSHIM_UI_CONFIRM_DURATION_MS);

    PressKey(&Test.ui, 0U);
    assert(lv_group_get_focused(Group) == First);
    PressKey(&Test.ui, 2U);
    assert(kshim_lvgl_take_selection(&Test.ui) == KSHIM_MENU_BOOT);
    DestroyUi(&Test);
}

static void Touch(kshim_lvgl_t *Ui, lv_obj_t *Object)
{
    lv_area_t Area;
    lv_obj_get_coords(Object, &Area);
    kshim_touch_event_t Event = {
        .type = KSHIM_TOUCH_EVENT_PRESS,
        .contact_id = 0U,
        .x = (uint32_t)((Area.x1 + Area.x2) / 2),
        .y = (uint32_t)((Area.y1 + Area.y2) / 2),
    };
    kshim_lvgl_touch(Ui, &Event);
    Advance(Ui, 48U);
    Event.type = KSHIM_TOUCH_EVENT_RELEASE;
    kshim_lvgl_touch(Ui, &Event);
    Advance(Ui, 48U);
}

static void TestDynamicAndTouch(void)
{
    test_ui_t Test;
    char Storage[KSHIM_MENU_MAX_ENTRIES][24];
    const char *Names[KSHIM_MENU_MAX_ENTRIES];
    for (size_t Index = 0; Index < KSHIM_MENU_MAX_ENTRIES; Index++) {
        int Length = snprintf(Storage[Index], sizeof(Storage[Index]),
                              "Image %02zu", Index);
        assert(Length > 0 && (size_t)Length < sizeof(Storage[Index]));
        Names[Index] = Storage[Index];
    }

    InitStableUi(&Test, 640U, 360U, KSHIM_FB_FORMAT_XRGB8888);
    assert(kshim_lvgl_set_entries(&Test.ui, Names,
                                  KSHIM_MENU_MAX_ENTRIES, 37U) == 0);
    assert(lv_group_get_obj_count(Test.ui.Group) == KSHIM_MENU_MAX_ENTRIES);
    assert(lv_group_get_focused(Test.ui.Group) ==
           lv_group_get_obj_by_index(Test.ui.Group, 37U));
    assert(kshim_lvgl_set_entries(&Test.ui, Names, 0U, 0U) != 0);
    assert(kshim_lvgl_set_entries(&Test.ui, Names,
                                  KSHIM_MENU_MAX_ENTRIES, 49U) != 0);

    PressKey(&Test.ui, 1U);
    assert(lv_group_get_focused(Test.ui.Group) ==
           lv_group_get_obj_by_index(Test.ui.Group, 38U));
    PressKey(&Test.ui, 2U);
    assert(kshim_lvgl_take_index(&Test.ui) == 38);
    assert(kshim_lvgl_take_selection(&Test.ui) == KSHIM_MENU_NONE);

    lv_obj_t *Focused = lv_group_get_focused(Test.ui.Group);
    Touch(&Test.ui, Focused);
    assert(kshim_lvgl_take_index(&Test.ui) == -1);
    Touch(&Test.ui, Test.ui.BootButton);
    assert(kshim_lvgl_take_index(&Test.ui) == 38);

    lv_area_t ListArea;
    lv_area_t FocusedArea;
    lv_obj_get_coords(Test.ui.List, &ListArea);
    lv_obj_get_coords(Focused, &FocusedArea);
    assert(FocusedArea.y2 >= ListArea.y1 && FocusedArea.y1 <= ListArea.y2);

    lv_obj_send_event(Focused, LV_EVENT_PRESSED, NULL);
    const lv_anim_t *Press = lv_anim_get(Focused, NULL);
    assert(Press != NULL && Press->duration == KSHIM_UI_PRESS_DURATION_MS);
    DestroyUi(&Test);
}

static void TestBatchedTouchAndCancel(void)
{
    test_ui_t Test;
    kshim_touch_t TouchState;
    const char *Names[] = {"Linux", "UEFI"};
    InitStableUi(&Test, 640U, 360U, KSHIM_FB_FORMAT_XRGB8888);
    assert(kshim_lvgl_set_entries(&Test.ui, Names, 2, 0) == 0);
    Advance(&Test.ui, 500U);
    kshim_touch_config_t Config = {
        .source_width = 640, .source_height = 360,
        .output_width = 640, .output_height = 360,
        .emit = kshim_lvgl_touch, .emit_context = &Test.ui,
    };
    assert(kshim_touch_init(&TouchState, &Config) == 0);
    lv_area_t Area;
    lv_obj_t *Second = lv_group_get_obj_by_index(Test.ui.Group, 1);
    lv_obj_get_coords(Second, &Area);
    uint32_t X = (uint32_t)((Area.x1 + Area.x2) / 2);
    uint32_t Y = (uint32_t)((Area.y1 + Area.y2) / 2);
    /* Exactly the callback sequence from one ST FIFO read, with no frame
     * between ENTER and LEAVE. The old sampled pointer loses this tap. */
    assert(kshim_touch_update(&TouchState, 0, KSHIM_TOUCH_CONTACT_ENTER, X, Y) == 0);
    assert(kshim_touch_update(&TouchState, 0, KSHIM_TOUCH_CONTACT_LEAVE, X, Y) == 0);
    assert(lv_group_get_focused(Test.ui.Group) == Second);
    assert(kshim_lvgl_take_index(&Test.ui) == -1);

    lv_obj_get_coords(Test.ui.BootButton, &Area);
    X = (uint32_t)((Area.x1 + Area.x2) / 2);
    Y = (uint32_t)((Area.y1 + Area.y2) / 2);
    assert(kshim_touch_update(&TouchState, 0, KSHIM_TOUCH_CONTACT_ENTER, X, Y) == 0);
    kshim_touch_cancel(&TouchState); /* NACK/reset/shutdown must not boot. */
    Advance(&Test.ui, 48U);
    assert(kshim_lvgl_take_index(&Test.ui) == -1);
    assert(Test.ui.PointerPressed == 0);
    assert(!lv_obj_has_state(Test.ui.BootButton, LV_STATE_PRESSED));

    assert(kshim_touch_update(&TouchState, 0, KSHIM_TOUCH_CONTACT_ENTER, X, Y) == 0);
    assert(kshim_touch_update(&TouchState, 0, KSHIM_TOUCH_CONTACT_LEAVE, X, Y) == 0);
    assert(kshim_lvgl_take_index(&Test.ui) == 1);
    DestroyUi(&Test);
}

static const uint32_t ChinesePhraseCodepoints[] = {
    0x9009U, 0x62E9U, 0x542FU, 0x52A8U, 0x7CFBU, 0x7EDFU, 0x97F3U,
    0x91CFU, 0x952EU, 0x7535U, 0x6E90U, 0xFF0CU, 0x5173U, 0x673AU,
    0x6062U, 0x590DU, 0x6A21U, 0x5F0FU, 0x6682U, 0x4E0DU, 0x53EFU,
    0x7528U, 0x79D2U, 0x540EU, 0x81EAU, 0x539FU, 0x5382U, 0x5907U,
    0x8BCAU, 0x65ADU, 0x00B7U,
};

static const uint32_t ChineseTitleCodepoints[] = {
    0x9009U, 0x62E9U, 0x542FU, 0x52A8U, 0x7CFBU, 0x7EDFU,
};

static void ExpectGlyphSameFont(const lv_font_t *Font, uint32_t Codepoint)
{
    lv_font_glyph_dsc_t Dsc;
    memset(&Dsc, 0, sizeof(Dsc));
    assert(lv_font_get_glyph_dsc(Font, &Dsc, Codepoint, 0U));
    assert(!Dsc.is_placeholder);
    assert(Dsc.resolved_font == Font);
    assert(Dsc.box_w > 0U && Dsc.box_h > 0U);
}

static void ExpectGlyphFallback(const lv_font_t *Font, uint32_t Codepoint)
{
    lv_font_glyph_dsc_t Dsc;
    memset(&Dsc, 0, sizeof(Dsc));
    assert(lv_font_get_glyph_dsc(Font, &Dsc, Codepoint, 0U));
    assert(!Dsc.is_placeholder);
    assert(Dsc.resolved_font != Font);
    assert(Dsc.box_w > 0U && Dsc.box_h > 0U);
}

static void ExpectGlyphAbsent(const lv_font_t *Font, uint32_t Codepoint)
{
    lv_font_glyph_dsc_t Dsc;
    memset(&Dsc, 0, sizeof(Dsc));
    int Present = lv_font_get_glyph_dsc(Font, &Dsc, Codepoint, 0U);
    assert(!Present || Dsc.is_placeholder);
}

static void ExpectPhraseGlyphs(const lv_font_t *Font, const uint32_t *Codepoints,
                               size_t Count)
{
    for (size_t Index = 0; Index < Count; Index++)
        ExpectGlyphSameFont(Font, Codepoints[Index]);
}

static void TestChineseFontGlyphs(void)
{
    const lv_font_t *PhraseFonts[] = {
        &kshim_font_cjk_14, &kshim_font_cjk_20, &kshim_font_cjk_28,
    };
    for (size_t Index = 0;
         Index < sizeof(PhraseFonts) / sizeof(PhraseFonts[0]); Index++) {
        const lv_font_t *Font = PhraseFonts[Index];
        ExpectPhraseGlyphs(Font, ChinesePhraseCodepoints,
                           sizeof(ChinesePhraseCodepoints) /
                               sizeof(ChinesePhraseCodepoints[0]));
        ExpectGlyphFallback(Font, 0x41U);
        ExpectGlyphFallback(Font, 0x30U);
        ExpectGlyphAbsent(Font, 0x9F98U);
    }

    ExpectPhraseGlyphs(&kshim_font_cjk_48, ChineseTitleCodepoints,
                       sizeof(ChineseTitleCodepoints) /
                           sizeof(ChineseTitleCodepoints[0]));
    ExpectGlyphFallback(&kshim_font_cjk_48, 0x41U);
    ExpectGlyphFallback(&kshim_font_cjk_48, 0x30U);
    ExpectGlyphAbsent(&kshim_font_cjk_48, 0x9F98U);
}

typedef struct chinese_scan {
    lv_obj_t   *Title;
    lv_obj_t   *Boot;
    lv_obj_t   *Help;
    lv_obj_t   *Countdown;
    unsigned    TitleCount;
    unsigned    BootCount;
    unsigned    HelpCount;
    const char *CountdownNumber;
} chinese_scan_t;

static lv_obj_t *TopObject(lv_obj_t *Object)
{
    lv_obj_t *Parent = lv_obj_get_parent(Object);
    while (Parent != NULL) {
        Object = Parent;
        Parent = lv_obj_get_parent(Object);
    }
    return Object;
}

static void ScanChineseLabel(lv_obj_t *Label, void *Context)
{
    chinese_scan_t *Scan = Context;
    const char *Text = lv_label_get_text(Label);
    if (Text == NULL)
        return;
    if (strcmp(Text, "选择启动系统") == 0) {
        Scan->Title = Label;
        Scan->TitleCount++;
    } else if (strcmp(Text, "启动") == 0) {
        Scan->Boot = Label;
        Scan->BootCount++;
    } else if (strcmp(Text, "音量键选择，电源键启动") == 0) {
        Scan->Help = Label;
        Scan->HelpCount++;
    } else if (Scan->CountdownNumber != NULL &&
               strstr(Text, Scan->CountdownNumber) != NULL) {
        Scan->Countdown = Label;
    }
}

static unsigned WalkLabels(lv_obj_t *Object, void (*Visit)(lv_obj_t *, void *),
                           void *Context)
{
    unsigned Found = 0U;
    uint32_t Count = lv_obj_get_child_count(Object);
    for (uint32_t Index = 0; Index < Count; Index++) {
        lv_obj_t *Child = lv_obj_get_child(Object, (int32_t)Index);
        if (lv_obj_check_type(Child, &lv_label_class)) {
            Visit(Child, Context);
            Found++;
        }
        Found += WalkLabels(Child, Visit, Context);
    }
    return Found;
}

static void TestChineseMenu(void)
{
    test_ui_t Test;
    InitStableUi(&Test, 800U, 1200U, KSHIM_FB_FORMAT_ARGB8888);
    kshim_lvgl_set_status(&Test.ui, "音量键选择，电源键启动");
    Advance(&Test.ui, 400U);

    TestChineseFontGlyphs();

    kshim_lvgl_frame(&Test.ui, 16U);

    chinese_scan_t Scan;
    memset(&Scan, 0, sizeof(Scan));

    lv_obj_t *Root = TopObject(Test.ui.List);
    assert(Root != NULL);
    assert(WalkLabels(Root, ScanChineseLabel, &Scan) > 0U);

    assert(Scan.TitleCount == 1U);
    assert(Scan.BootCount == 1U);
    assert(Scan.HelpCount == 1U);
    assert(Scan.Title != NULL && Scan.Boot != NULL && Scan.Help != NULL);
    assert(lv_obj_get_style_text_font(Scan.Title, 0) == &kshim_font_cjk_48);
    assert(lv_obj_get_style_text_font(Scan.Boot, 0) == &kshim_font_cjk_28);
    assert(lv_obj_get_style_text_font(Scan.Help, 0) == &kshim_font_cjk_28);

    const char *Names[] = {"Android", "Android 备用"};
    assert(kshim_lvgl_set_entries(&Test.ui, Names, 2U, 0U) == 0);

    kshim_lvgl_set_countdown(&Test.ui, 30000U);
    kshim_lvgl_frame(&Test.ui, 16U);

    char Expected[128];
    int Length = snprintf(Expected, sizeof(Expected),
                          "音量键选择，电源键启动 · %u 秒后自动启动",
                          Test.ui.CountdownShown);
    assert(Length > 0 && (size_t)Length < sizeof(Expected));

    memset(&Scan, 0, sizeof(Scan));
    Scan.CountdownNumber = Expected;

    Root = TopObject(Test.ui.List);
    assert(Root != NULL);
    assert(WalkLabels(Root, ScanChineseLabel, &Scan) > 0U);

    assert(Scan.Countdown != NULL);
    const char *CountdownText = lv_label_get_text(Scan.Countdown);
    assert(CountdownText != NULL);
    assert(strcmp(CountdownText, Expected) == 0);
    assert(lv_obj_get_style_text_font(Scan.Countdown, 0) == &kshim_font_cjk_28);

    DestroyUi(&Test);
}

int main(void)
{
    TestChineseMenu();
    TestFormats();
    TestDefaultKeys();
    TestDynamicAndTouch();
    TestBatchedTouchAndCancel();
    puts("LVGL color formats, 49-entry menu, key mapping, touch confirmation and motion tests passed");
    return 0;
}
