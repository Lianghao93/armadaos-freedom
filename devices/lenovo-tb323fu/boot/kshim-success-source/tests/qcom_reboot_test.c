/* SPDX-License-Identifier: MIT */
#include <qcom_reboot.h>
#include <qcom_dt.h>
#include <psci.h>
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned char blob[16384] __attribute__((aligned(8)));
static struct {
    uint32_t dload, version, dload_values[8];
    uint8_t reason, reason_values[8];
    unsigned smc_calls, dload_reads, dload_writes, reason_reads, reason_writes;
    unsigned cold, warm, oem_warm, prepared;
    unsigned fail_dload_write, fail_reason_write, corrupt_dload_read;
    int no_scm, no_reset2, has_oem, fail_prepare, interrupt, resumed, fail_after_write;
} fake;

static int node(const char *path)
{
    int n = fdt_path_offset(blob, path);
    assert(n >= 0);
    return n;
}
static void add(const char *parent, const char *name)
{
    assert(fdt_add_subnode(blob, node(parent), name) >= 0);
}
static void string(const char *path, const char *name, const char *value)
{
    assert(!fdt_setprop_string(blob, node(path), name, value));
}
static void cells(const char *path, const char *name, const uint32_t *values, unsigned count)
{
    fdt32_t values_be[8];
    assert(count <= 8);
    for (unsigned i = 0; i < count; ++i) values_be[i] = cpu_to_fdt32(values[i]);
    assert(!fdt_setprop(blob, node(path), name, values_be, count * 4));
}
#define CELLS(path, name, ...) do { const uint32_t v[] = {__VA_ARGS__}; \
    cells(path, name, v, sizeof(v) / sizeof(v[0])); } while (0)
#define PMIC "/arbiter/spmi/pmic"
#define SDAM PMIC "/sdam"
#define CELL SDAM "/restart"

static void fixture(void)
{
    memset(&fake, 0, sizeof(fake));
    fake.dload = 0x30; fake.reason = 0xa5; fake.version = 0x10001;
    assert(!fdt_create_empty_tree(blob, sizeof(blob)));
    string("/", "compatible", "qcom,canoep");
    CELLS("/", "#address-cells", 2); CELLS("/", "#size-cells", 2);
    add("/", "syscon"); string("/syscon", "compatible", "syscon");
    CELLS("/syscon", "reg", 0, 0x1fc0000, 0, 0x30000);
    CELLS("/syscon", "phandle", 2);
    add("/", "scm"); string("/scm", "compatible", "qcom,scm");
    CELLS("/scm", "qcom,dload-mode", 2, 0x19000);
    add("/", "arbiter");
    string("/arbiter", "compatible", "qcom,canoe-spmi-pmic-arb");
    add("/arbiter", "spmi"); CELLS("/arbiter/spmi", "qcom,bus-id", 0);
    add("/arbiter/spmi", "pmic"); string(PMIC, "compatible", "qcom,spmi-pmic");
    CELLS(PMIC, "reg", 0, 0); CELLS(PMIC, "#address-cells", 1);
    CELLS(PMIC, "#size-cells", 0);
    add(PMIC, "sdam"); string(SDAM, "compatible", "qcom,spmi-sdam");
    CELLS(SDAM, "reg", 0x7100); CELLS(SDAM, "#address-cells", 1);
    CELLS(SDAM, "#size-cells", 1);
    add(SDAM, "restart"); CELLS(CELL, "reg", 0x48, 1);
    CELLS(CELL, "bits", 1, 7); CELLS(CELL, "phandle", 3);
    add("/", "reason"); string("/reason", "compatible", "qcom,reboot-reason");
    CELLS("/reason", "nvmem-cells", 3);
    string("/reason", "nvmem-cell-names", "restart_reason");
}

int kshim_psci_init(const void *fdt) { assert(fdt == blob); return 0; }
kshim_psci_conduit_t kshim_psci_conduit(void) { return KSHIM_PSCI_CONDUIT_SMC; }
uint32_t kshim_psci_version(void) { return fake.version; }
int64_t kshim_psci_features(uint32_t fid)
{
    assert(fid == 0xc4000012); return fake.no_reset2 ? -1 : 0;
}
int64_t kshim_psci_request_system_reset(void)
{
    ++fake.cold;
    assert(fake.dload == 0);
    assert(fake.reason >= 2 && fake.reason <= 5);
    return 0; /* Even an unexpected success return must roll back. */
}
int64_t kshim_psci_call(uint32_t fid, uint64_t type, uint64_t cookie, uint64_t unused)
{
    assert(fid == 0xc4000012 && type == 0 && cookie == 0 && unused == 0);
    assert(fake.dload == 1);
    ++fake.warm;
    return -3;
}
struct kshim_resources *kshim_resources_get(void) { return NULL; }

static void check_config(const struct kshim_qcom_reboot_config *c)
{
    assert(c->fdt == blob && c->dload_address == 0x1fd9000);
    assert(c->controller == 0 && c->bus == 0 && c->sid == 0);
    assert(c->reason_address == 0x7148 && c->reason_mask == 0xfe);
}
int kshim_qcom_reboot_spmi_prepare(const struct kshim_qcom_reboot_config *c)
{
    check_config(c); ++fake.prepared; return fake.fail_prepare ? -1 : 0;
}
int kshim_qcom_reboot_spmi_read(const struct kshim_qcom_reboot_config *c, uint8_t *value)
{
    check_config(c); ++fake.reason_reads; *value = fake.reason; return 0;
}
int kshim_qcom_reboot_spmi_write(const struct kshim_qcom_reboot_config *c, uint8_t value)
{
    check_config(c);
    assert(fake.reason_writes < 8);
    fake.reason_values[fake.reason_writes++] = value;
    if (fake.reason_writes == fake.fail_reason_write) return -1;
    fake.reason = value; return 0;
}
void kshim_qcom_reboot_smccc(uint64_t r[8])
{
    ++fake.smc_calls;
    uint64_t fid = r[0];
    if (fid == 0xc2000601) {
        assert(r[1] == 1 && (r[2] == 0x02000601 || r[2] == 0x02000501 ||
                             r[2] == 0x02000502 || r[2] == 0x03000922));
        r[0] = 0;
        r[1] = r[2] == 0x03000922 ? fake.has_oem : !fake.no_scm;
        return;
    }
    if (fid == 0xc3000922) {
        assert(fake.has_oem && fake.dload == 1);
        for (unsigned i = 1; i < 8; ++i) assert(r[i] == 0);
        ++fake.oem_warm; r[0] = (uint64_t)-3; return;
    }
    if (fid == 1) {
        assert(fake.interrupt && !fake.resumed && r[6] == 0xabcd);
        assert(r[1] == 1 && r[2] == 0x1fd9000 && r[3] == 0);
        fake.resumed = 1; fid = 0xc2000501;
    } else if (fid == 0xc2000501 && fake.interrupt && !fake.resumed) {
        r[0] = 1; r[1] = 99; r[2] = 99; r[3] = 99; r[6] = 0xabcd; return;
    }
    assert(r[2] == 0x1fd9000);
    if (fid == 0xc2000501) {
        assert(r[1] == 1); ++fake.dload_reads;
        r[0] = 0; r[1] = fake.dload;
        if (fake.dload_reads == fake.corrupt_dload_read) r[1] ^= 0x10;
    } else {
        assert(fid == 0xc2000502 && r[1] == 2 && fake.dload_writes < 8);
        fake.dload_values[fake.dload_writes++] = (uint32_t)r[3];
        if (fake.dload_writes == fake.fail_dload_write) {
            if (fake.fail_after_write) fake.dload = (uint32_t)r[3];
            r[0] = (uint64_t)-5; return;
        }
        fake.dload = (uint32_t)r[3]; r[0] = 0;
    }
}

static void test_targets(void)
{
    fixture();
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.cold == 1 && !fake.warm && fake.reason_values[0] == 3);
    assert(fake.dload_values[0] == 0 && fake.dload_values[1] == 0x30);
    assert(fake.reason == 0xa5 && fake.dload == 0x30);
    fixture();
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_BOOTLOADER) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.reason_values[0] == 5 && fake.reason_values[1] == 0xa5);
    fixture(); fake.reason = 0xa4;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.reason_values[0] == 2 && fake.reason == 0xa4);
    fixture(); fake.interrupt = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.warm == 1 && !fake.cold && !fake.prepared && !fake.reason_writes);
    assert(fake.dload_values[0] == 1 && fake.dload == 0x30 && fake.resumed);
    fixture(); fake.has_oem = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.oem_warm == 1 && !fake.warm && !fake.cold && fake.dload == 0x30);
    fixture(); fake.has_oem = 1; fake.version = 0x10000;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) ==
           KSHIM_QCOM_REBOOT_RESET_RETURNED);
    assert(fake.oem_warm == 1 && !fake.warm && fake.dload == 0x30);
}

static void test_preflight_and_rollback(void)
{
    fixture(); fake.version = 0x10000;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) == KSHIM_QCOM_REBOOT_UNSUPPORTED);
    assert(!fake.dload_writes && !fake.reason_writes && !fake.warm && !fake.oem_warm);
    fixture(); fake.no_reset2 = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) == KSHIM_QCOM_REBOOT_UNSUPPORTED);
    assert(!fake.dload_writes && !fake.reason_writes);
    fixture(); fake.no_scm = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) == KSHIM_QCOM_REBOOT_UNSUPPORTED);
    assert(!fake.dload_writes && !fake.reason_writes);
    fixture(); fake.fail_prepare = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) == KSHIM_QCOM_REBOOT_IO_FAILED);
    assert(!fake.dload_writes);
    fixture(); fake.fail_dload_write = 1; fake.fail_after_write = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) == KSHIM_QCOM_REBOOT_IO_FAILED);
    assert(fake.dload_writes == 2 && fake.dload == 0x30 && !fake.warm);
    fixture(); fake.corrupt_dload_read = 2;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) == KSHIM_QCOM_REBOOT_VERIFY_FAILED);
    assert(fake.dload == 0x30 && !fake.warm);
    fixture(); fake.fail_reason_write = 1;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) == KSHIM_QCOM_REBOOT_IO_FAILED);
    assert(fake.reason_writes == 2 && fake.reason == 0xa5 && fake.dload == 0x30 && !fake.cold);
    fixture(); fake.fail_reason_write = 2;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) == KSHIM_QCOM_REBOOT_ROLLBACK_FAILED);
    assert(fake.reason == 3 && fake.dload == 0x30);
    fixture(); fake.fail_dload_write = 2;
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_EDL) == KSHIM_QCOM_REBOOT_ROLLBACK_FAILED);
    assert(fake.dload == 1);
}

static void test_dt_validation(void)
{
    struct kshim_qcom_reboot_config c;
    fixture(); assert(!kshim_qcom_reboot_parse(blob, &c)); check_config(&c);
    /* Addresses follow the supplied DT rather than board constants. */
    CELLS("/syscon", "reg", 0, 0x2200000, 0, 0x30000);
    CELLS(SDAM, "reg", 0x7200);
    assert(!kshim_qcom_reboot_parse(blob, &c));
    assert(c.dload_address == 0x2219000 && c.reason_address == 0x7248);
    fixture(); string("/", "compatible", "qcom,sm8750");
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_UNSUPPORTED);
    fixture(); CELLS("/scm", "qcom,dload-mode", 2, 0x30000);
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_INVALID_DT);
    fixture(); CELLS("/scm", "qcom,dload-mode", 2, 3);
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_INVALID_DT);
    fixture(); CELLS(CELL, "bits", 0, 8);
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_INVALID_DT);
    fixture(); CELLS(CELL, "reg", 0x48, 2);
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_INVALID_DT);
    fixture(); string("/arbiter", "status", "disabled");
    assert(kshim_qcom_reboot_parse(blob, &c) == KSHIM_QCOM_REBOOT_INVALID_DT);
    fixture(); CELLS("/reason", "nvmem-cells", 999);
    assert(kshim_qcom_reboot_request(blob, KSHIM_QCOM_REBOOT_RECOVERY) == KSHIM_QCOM_REBOOT_INVALID_DT);
    assert(!fake.smc_calls && !fake.dload_writes && !fake.reason_writes);
}

static int test_hardware_dtb(const char *path)
{
    FILE *file = fopen(path, "rb");
    if (!file) { perror(path); return 1; }
    if (fseek(file, 0, SEEK_END)) { fclose(file); return 1; }
    long length = ftell(file);
    if (length < (long)sizeof(struct fdt_header) || length > INT_MAX ||
        fseek(file, 0, SEEK_SET)) {
        fprintf(stderr, "Invalid DTB file length\n");
        fclose(file); return 1;
    }
    void *hardware = malloc((size_t)length);
    if (!hardware) { fclose(file); return 1; }
    size_t bytes = fread(hardware, 1, (size_t)length, file);
    int close_result = fclose(file);
    if (bytes != (size_t)length || close_result || dt_validate(hardware, bytes)) {
        fprintf(stderr, "Cannot read a complete valid DTB\n");
        free(hardware); return 1;
    }

    /* Parse-only regression: never enter request(), PSCI, SCM or SPMI. */
    struct kshim_qcom_reboot_config c;
    int result = kshim_qcom_reboot_parse(hardware, &c);
    if (result) {
        fprintf(stderr, "Hardware DT reboot parse failed: %d\n", result);
        free(hardware); return 1;
    }
    printf("Hardware DT reboot parse: dload=0x%llx controller=%u bus=%u sid=%u "
           "reason=0x%04x mask=0x%02x shift=%u\n",
           (unsigned long long)c.dload_address, (unsigned)c.controller,
           (unsigned)c.bus, (unsigned)c.sid, (unsigned)c.reason_address,
           (unsigned)c.reason_mask, (unsigned)c.reason_shift);
    int matches = c.dload_address == 0x1fd9000 && c.bus == 0 && c.sid == 0 &&
                  c.reason_address == 0x7148 && c.reason_mask == 0xfe &&
                  c.reason_shift == 1;
    assert(!fake.smc_calls && !fake.prepared && !fake.reason_reads &&
           !fake.reason_writes && !fake.cold && !fake.warm && !fake.oem_warm);
    free(hardware);
    if (!matches) {
        fprintf(stderr, "Hardware DT reboot values differ from TB323FU evidence\n");
        return 1;
    }
    puts("qcom_reboot_test: hardware DT parse-only regression passed");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "--hardware-dtb"))
        return test_hardware_dtb(argv[2]);
    if (argc != 1) {
        fprintf(stderr, "Usage: %s [--hardware-dtb PATH]\n", argv[0]);
        return 2;
    }
    test_targets(); test_preflight_and_rollback(); test_dt_validation();
    puts("qcom_reboot_test: DT, target ABI, capability checks and rollback passed");
    return 0;
}
