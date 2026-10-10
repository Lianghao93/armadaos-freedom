// Regression test: packed MangoApp protocol wire decode (test-only; no product change).
// Encodes raw wire bytes at numeric offsets, memcpy into the ACTUAL header struct,
// then asserts decoded fields. Baseline RED is expected on app/visible fields.
#include <array>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>

#include "mangoapp_proto.h"

static_assert(sizeof(long) == 8, "LP64: long must be 8 bytes");
static_assert(sizeof(mangoapp_msg_v1) == 95, "packed msg_v1 must be 95 bytes");
static_assert(sizeof(mangoapp_msg_v1) - sizeof(long) == 87, "SysV payload excludes msg_type long8");

static_assert(offsetof(mangoapp_msg_v1, pid) == 12, "pid offset");
static_assert(offsetof(mangoapp_msg_v1, fsrUpscale) == 24, "fsrUpscale offset");
static_assert(offsetof(mangoapp_msg_v1, fsrSharpness) == 25, "fsrSharpness offset");
static_assert(offsetof(mangoapp_msg_v1, latency_ns) == 34, "latency_ns offset");
static_assert(offsetof(mangoapp_msg_v1, engineName) == 53, "engineName offset");
static_assert(offsetof(mangoapp_msg_v1, upscaler) == 93, "upscaler offset");
static_assert(offsetof(mangoapp_msg_v1, wantedUpscaler) == 94, "wantedUpscaler offset");
// app_frametime_ns / visible_frametime_ns offsets are deliberately NOT asserted so the
// baseline executes and reports a RED value mismatch instead of stopping at compile time.

static int g_fail = 0;

static void expect_u64(const char* label, uint64_t actual, uint64_t wanted) {
    if (actual != wanted) {
        std::printf("FAIL %s: actual=%llu wanted=%llu\n", label,
                    (unsigned long long)actual, (unsigned long long)wanted);
        ++g_fail;
    } else {
        std::printf("PASS %s: %llu\n", label, (unsigned long long)actual);
    }
}
static void expect_u32(const char* label, uint32_t actual, uint32_t wanted) {
    expect_u64(label, actual, wanted);
}

template <typename T>
static void put(std::array<unsigned char, 95>& b, std::size_t off, T v) {
    std::memcpy(b.data() + off, &v, sizeof(T));
}
static mangoapp_msg_v1 decode(const std::array<unsigned char, 95>& b) {
    mangoapp_msg_v1 m;
    std::memcpy(&m, b.data(), sizeof(m));
    return m;
}

static void fill_common(std::array<unsigned char, 95>& b) {
    long msg_type = 1;           std::memcpy(b.data() + 0, &msg_type, sizeof(long));
    uint32_t version = 1;        put(b, 8, version);
    uint32_t pid = 48431;        put(b, 12, pid);
    uint8_t  fsrUpscale = 1;     put(b, 24, fsrUpscale);
    uint8_t  fsrSharpness = 77;  put(b, 25, fsrSharpness);
    uint64_t latency = 99887766; put(b, 34, latency);
    std::memcpy(b.data() + 53, "MangoHudSGSR", sizeof("MangoHudSGSR"));
    uint8_t  upscaler = MANGOAPP_UPSCALER_SGSR; put(b, 93, upscaler);
    uint8_t  wanted   = MANGOAPP_UPSCALER_FSR;  put(b, 94, wanted);
}

static void check_common(const mangoapp_msg_v1& m) {
    expect_u64("hdr.msg_type", (uint64_t)m.hdr.msg_type, 1);
    expect_u32("hdr.version", m.hdr.version, 1);
    expect_u32("pid", m.pid, 48431);
    expect_u32("fsrUpscale", m.fsrUpscale, 1);
    expect_u32("fsrSharpness", m.fsrSharpness, 77);
    expect_u64("latency_ns", m.latency_ns, 99887766);
    if (std::strcmp(m.engineName, "MangoHudSGSR") != 0) {
        std::printf("FAIL engineName: '%s'\n", m.engineName); ++g_fail;
    } else {
        std::printf("PASS engineName: %s\n", m.engineName);
    }
    expect_u32("upscaler", m.upscaler, MANGOAPP_UPSCALER_SGSR);
    expect_u32("wantedUpscaler", m.wantedUpscaler, MANGOAPP_UPSCALER_FSR);
}

int main() {
    std::printf("== SampleA ==\n");
    std::array<unsigned char, 95> a{};
    fill_common(a);
    put(a, 16, (uint64_t)25419186);     // offset16 = APP ready ns
    put(a, 26, (uint64_t)UINT64_MAX);   // offset26 = VISIBLE sentinel
    mangoapp_msg_v1 ma = decode(a);
    check_common(ma);
    expect_u64("A.app_frametime_ns", ma.app_frametime_ns, 25419186);
    expect_u64("A.visible_frametime_ns", ma.visible_frametime_ns, UINT64_MAX);

    std::printf("== SampleB ==\n");
    std::array<unsigned char, 95> b{};
    fill_common(b);
    put(b, 16, (uint64_t)UINT64_MAX);   // offset16 = APP sentinel
    put(b, 26, (uint64_t)11111000);     // offset26 = VISIBLE actual
    mangoapp_msg_v1 mb = decode(b);
    check_common(mb);
    expect_u64("B.app_frametime_ns", mb.app_frametime_ns, UINT64_MAX);
    expect_u64("B.visible_frametime_ns", mb.visible_frametime_ns, 11111000);

    std::printf("total failures: %d\n", g_fail);
    return g_fail == 0 ? 0 : 1;
}
