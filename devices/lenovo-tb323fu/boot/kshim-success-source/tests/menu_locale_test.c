/* Runtime menu locale regression test for KShimC.
 *
 * Standalone main() using only assert/stdio/string plus the public
 * menu_locale.h surface and the menu_text.h display macros. No framework,
 * no mocks. Built with CONFIG_KSHIM_MENU_LANGUAGE_SWITCH=1 and
 * CONFIG_KSHIM_MENU_CHINESE=1 shared with src/core/menu_locale.c, so the
 * session language starts at ZH_CN and every text macro must follow the
 * runtime selection rather than the compile-time locale.
 */

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "menu_locale.h"
#include "menu_text.h"

#define KSHIM_TEST_GUARD 0xA5u

static int contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

/* Every legal id resolves to a non-empty string; invalid ids are empty. */
static void check_text_ids(void)
{
    int id;

    for (id = 0; id < (int)KSHIM_TEXT_COUNT; ++id) {
        const char *text = kshim_menu_text((kshim_menu_text_id)id);
        assert(text != NULL);
        assert(text[0] != '\0');
    }
    assert(strcmp(kshim_menu_text((kshim_menu_text_id)-1), "") == 0);
    assert(strcmp(kshim_menu_text(KSHIM_TEXT_COUNT), "") == 0);
    assert(strcmp(kshim_menu_text((kshim_menu_text_id)(KSHIM_TEXT_COUNT + 1)), "") == 0);
}

/* Out-of-enum languages are rejected and never change the selection. */
static void check_language_rejection(void)
{
    kshim_menu_language_t before = kshim_menu_get_language();

    assert(kshim_menu_set_language((kshim_menu_language_t)-1) == -1);
    assert(kshim_menu_get_language() == before);

    assert(kshim_menu_set_language((kshim_menu_language_t)(KSHIM_MENU_LANG_EN + 1)) == -1);
    assert(kshim_menu_get_language() == before);
}

/* Compact manually specified role fixtures; deliberately not the whole
 * production text table. */
struct role_case {
    kshim_menu_role_t role;
    const char *zh_name;
    const char *en_name;
    const char *zh_desc_fragment;
    const char *en_desc_fragment;
};

static const struct role_case role_cases[] = {
    { KSHIM_MENU_ROLE_ANDROID,    "Android 原厂",   "Stock Android",     "原有",     "original Android" },
    { KSHIM_MENU_ROLE_INTERNAL,   "机身 ArmadaOS",  "Internal ArmadaOS", "机身存储", "internal storage" },
    { KSHIM_MENU_ROLE_TF,         "TF 卡 ArmadaOS", "TF card ArmadaOS",  "TF 卡",    "TF card" },
    { KSHIM_MENU_ROLE_DIAGNOSTIC, "Linux 诊断模式", "Linux diagnostics", "诊断环境", "diagnostic environment" },
};

#define ROLE_CASE_COUNT (sizeof role_cases / sizeof role_cases[0])

/* Verify the explicit role labels/descriptions and record the returned name
 * pointers for the caller's cross-language comparison. */
static void check_roles_in_language(int chinese, const char **names_out)
{
    size_t i;

    for (i = 0; i < ROLE_CASE_COUNT; ++i) {
        const struct role_case *rc = &role_cases[i];
        const char *name = kshim_menu_role_name(rc->role, NULL);
        const char *desc = kshim_menu_role_description(rc->role, NULL);

        assert(name != NULL);
        assert(desc != NULL);
        if (chinese) {
            assert(strcmp(name, rc->zh_name) == 0);
            assert(contains(desc, rc->zh_desc_fragment));
        } else {
            assert(strcmp(name, rc->en_name) == 0);
            assert(contains(desc, rc->en_desc_fragment));
        }
        names_out[i] = name;
    }
}

/* UNKNOWN/BASE/out-of-range roles must hand back the caller's own pointers
 * verbatim: no inference from displayed label text. */
static void check_role_fallbacks(void)
{
    char fallback_name[] = "Android 原厂";
    char fallback_desc[] = "fallback description text";

    assert(kshim_menu_role_name(KSHIM_MENU_ROLE_UNKNOWN, fallback_name) == fallback_name);
    assert(kshim_menu_role_description(KSHIM_MENU_ROLE_UNKNOWN, fallback_desc) == fallback_desc);

    assert(kshim_menu_role_name(KSHIM_MENU_ROLE_UNKNOWN, NULL) == NULL);
    assert(kshim_menu_role_description(KSHIM_MENU_ROLE_UNKNOWN, NULL) == NULL);

    assert(kshim_menu_role_name(KSHIM_MENU_ROLE_BASE, fallback_name) == fallback_name);

    assert(kshim_menu_role_name((kshim_menu_role_t)-1, fallback_name) == fallback_name);
    assert(kshim_menu_role_description((kshim_menu_role_t)-1, fallback_desc) == fallback_desc);
    assert(kshim_menu_role_name((kshim_menu_role_t)(KSHIM_MENU_ROLE_LANG_EN + 1), fallback_name) == fallback_name);
    assert(kshim_menu_role_description((kshim_menu_role_t)(KSHIM_MENU_ROLE_LANG_EN + 1), fallback_desc) == fallback_desc);
}

/* Countdown output plus the snprintf short-buffer contract. `expected` is the
 * full manually specified visible line, never derived from the SUT. */
static void check_countdown(const char *expected, const char *name)
{
    size_t full = strlen(expected);
    char buf[128];
    unsigned char guarded[64];
    unsigned char zeroed[16];
    const size_t cap = 4;
    char *dst;
    int rc;
    size_t i;

    rc = kshim_menu_format_countdown(buf, sizeof buf, 3, name);
    assert(rc == (int)full);
    assert(strcmp(buf, expected) == 0);

    /* Truncated write: guards untouched, NUL at capacity-1, prefix correct,
     * and the return value is the full would-have-written length. */
    memset(guarded, KSHIM_TEST_GUARD, sizeof guarded);
    dst = (char *)&guarded[2];
    rc = kshim_menu_format_countdown(dst, cap, 3, name);
    assert(rc == (int)full);
    assert(dst[cap - 1] == '\0');
    assert(strncmp(dst, expected, cap - 1) == 0);
    assert(guarded[0] == KSHIM_TEST_GUARD);
    assert(guarded[1] == KSHIM_TEST_GUARD);
    for (i = 2 + cap; i < sizeof guarded; ++i) {
        assert(guarded[i] == KSHIM_TEST_GUARD);
    }

    /* capacity 0 with a non-NULL buffer: nothing written, full length back. */
    memset(zeroed, KSHIM_TEST_GUARD, sizeof zeroed);
    rc = kshim_menu_format_countdown((char *)zeroed, 0, 3, name);
    assert(rc == (int)full);
    for (i = 0; i < sizeof zeroed; ++i) {
        assert(zeroed[i] == KSHIM_TEST_GUARD);
    }
}

int main(void)
{
    const char *zh_names[ROLE_CASE_COUNT];
    const char *en_names[ROLE_CASE_COUNT];
    size_t i;

    /* 1. Initial runtime state must be Chinese (CONFIG_KSHIM_MENU_CHINESE=1). */
    assert(kshim_menu_get_language() == KSHIM_MENU_LANG_ZH_CN);
    assert(strcmp(KSHIM_MENU_TEXT_TITLE("fallback"), "KShimC 启动器") == 0);
    assert(contains(KSHIM_MENU_TEXT_HELP, "音量"));

    check_text_ids();
    check_language_rejection();
    check_countdown("3 秒后启动ArmadaOS", "ArmadaOS");
    check_countdown("3 秒后自动启动", NULL);
    check_countdown("3 秒后自动启动", "");
    check_roles_in_language(1, zh_names);
    check_role_fallbacks();

    /* 2. Switch to English: getter and every runtime macro must follow. */
    assert(kshim_menu_set_language(KSHIM_MENU_LANG_EN) == 0);
    assert(kshim_menu_get_language() == KSHIM_MENU_LANG_EN);
    assert(strcmp(KSHIM_MENU_TEXT_TITLE("fallback"), "KShimC Launcher") == 0);
    assert(contains(KSHIM_MENU_TEXT_HELP, "Volume"));
    assert(contains(KSHIM_MENU_TEXT_HELP, "Power"));
    assert(contains(KSHIM_MENU_TEXT_STATUS, "Select a system"));
    assert(contains(KSHIM_MENU_TEXT_CREDIT, "Danyi"));
    assert(contains(KSHIM_MENU_TEXT_CREDIT, "Free to use"));
    assert(strcmp(KSHIM_MENU_TEXT_LANGUAGE_STATE, "English") == 0);

    check_text_ids();
    check_language_rejection();
    check_countdown("Booting ArmadaOS in 3 s", "ArmadaOS");
    check_countdown("Booting in 3 s", NULL);
    check_countdown("Booting in 3 s", "");
    check_roles_in_language(0, en_names);
    check_role_fallbacks();

    /* 2. The explicit role labels must actually differ across languages. */
    for (i = 0; i < ROLE_CASE_COUNT; ++i) {
        assert(strcmp(zh_names[i], en_names[i]) != 0);
    }

    /* 1. Switch back to Chinese and confirm the macros are restored. */
    assert(kshim_menu_set_language(KSHIM_MENU_LANG_ZH_CN) == 0);
    assert(kshim_menu_get_language() == KSHIM_MENU_LANG_ZH_CN);
    assert(strcmp(KSHIM_MENU_TEXT_TITLE("fallback"), "KShimC 启动器") == 0);
    assert(contains(KSHIM_MENU_TEXT_HELP, "音量"));

    printf("menu_locale runtime regression: ALL TESTS PASSED\n");
    return 0;
}
