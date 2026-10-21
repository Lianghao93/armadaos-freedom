/*
 * Minimal static aarch64 init used only for kShim maintenance-boot
 * diagnostics on TB322FC. Prints proof of life plus the partition table,
 * then idles. No libc runtime deps beyond static glibc.
 */
#include <fcntl.h>
#include <string.h>
#include <sys/mount.h>
#include <unistd.h>

static void out(const char *s)
{
    write(1, s, strlen(s));
}

static void dump(const char *path, int add_newline)
{
    int fd = open(path, O_RDONLY);
    char buf[2048];
    long n;

    if (fd < 0) {
        out("  <cannot open> ");
        out(path);
        out("\n");
        return;
    }
    while ((n = read(fd, buf, sizeof buf)) > 0)
        write(1, buf, (size_t)n);
    close(fd);
    if (add_newline)
        out("\n");
}

int main(void)
{
    int c = open("/dev/console", O_RDWR);

    if (c >= 0) {
        dup2(c, 0);
        dup2(c, 1);
        dup2(c, 2);
    }

    out("\n\n=== KSHIM LINUX ALIVE ===\n");

    mount("proc", "/proc", "proc", 0, "");
    mount("sysfs", "/sys", "sysfs", 0, "");
    mount("devtmpfs", "/dev", "devtmpfs", 0, "");

    out("--- /proc/partitions ---\n");
    dump("/proc/partitions", 1);

    out("--- compatible ---\n");
    dump("/proc/device-tree/compatible", 1);

    out("--- block by-name ---\n");
    dump("/proc/partitions", 0);

    out("\n=== end ===\n");

    for (;;)
        sleep(3600);

    return 0;
}
