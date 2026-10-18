#!/usr/bin/env python3
"""Real native OSTree lifecycle test for the kShimC default-entry path.

Manager-run only, inside an ARM64 container that ships the production
ostree 2026.4. Creates an isolated sysroot under --work, commits three tiny
trees, performs real `ostree admin deploy` transitions, and verifies the
shared armada_default_bls_entry() plus the real kshim-select-boot.py after
native ordering and `ostree admin set-default` reordering.

No network, device, mount, bootc, or raw boot write. A container sysroot is
not a booted deployment: staging/finalize, systemd units, and an actual boot
are out of scope and are never fabricated.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import traceback

ROOT_UUID = "11111111-2222-3333-4444-555555555555"
BOOT_UUID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
ELDEN_DTB = "dtb/qcom/sm8750-lenovo-elden.dtb"
OSTREE_ENV = {"OSTREE_SYSROOT_DEBUG": "mutable-deployments"}
LIMITATIONS = [
    "No real boot: this sysroot is never booted.",
    "No ostree staging/finalize-staged or systemd unit execution.",
    "No bootc rollback, no booted=true state.",
    "No ESP /KERNEL raw write and no device access.",
]

WORK = None
SYSROOT = None
REPO = None
BOOT = None
TREES = None
TRANSITIONS = None
REPO_ROOT = None
ARGS_FILE = None
SELECTOR = None
LOG = None
SUMMARY = {
    "test": "kshim-ostree-lifecycle",
    "result": None,
    "error": None,
    "started": None,
    "finished": None,
    "ostree_version": None,
    "work": None,
    "sysroot": None,
    "repo": None,
    "deployments": [],
    "transitions": [],
    "limitations": LIMITATIONS,
}


class Failure(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Failure(message)


def fail(message):
    raise Failure(message)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class Log:
    def __init__(self, path):
        self.path = path
        self.handle = open(path, "w", encoding="utf-8")

    def write(self, text):
        text = str(text).rstrip("\n")
        print(text, flush=True)
        self.handle.write(text + "\n")
        self.handle.flush()

    def traceback(self):
        traceback.print_exc(file=self.handle)
        self.handle.flush()

    def close(self):
        self.handle.close()


def run(argv):
    argv = [str(arg) for arg in argv]
    LOG.write("$ " + " ".join(shlex.quote(arg) for arg in argv))
    env = dict(os.environ)
    env.update(OSTREE_ENV)
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    if proc.stdout.strip():
        LOG.write(proc.stdout.rstrip())
    if proc.stderr.strip():
        LOG.write(proc.stderr.rstrip())
    LOG.write("exit %d" % proc.returncode)
    if proc.returncode != 0:
        fail("command failed (%d): %s" % (proc.returncode, " ".join(argv)))
    return proc.stdout


def status_json():
    raw = run(["ostree", "admin", "status", "--json", "--sysroot=" + str(SYSROOT)])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        fail("ostree admin status --json was not JSON: %s" % error)
    need(isinstance(data, dict) and isinstance(data.get("deployments"), list),
         "ostree admin status --json lacks deployments[]")
    return data


def deployments(data):
    result = []
    for position, item in enumerate(data["deployments"]):
        need(isinstance(item, dict), "deployment %d is not an object" % position)
        for key in ("checksum", "stateroot", "serial"):
            need(key in item, "deployment %d lacks %r" % (position, key))
        result.append({
            "index": int(item.get("index", position)),
            "checksum": item["checksum"],
            "stateroot": item["stateroot"],
            "serial": int(item["serial"]),
        })
    return result


def find_deployment(commit):
    matches = [item for item in deployments(status_json())
               if item["checksum"] == commit and item["stateroot"] == "default"]
    need(len(matches) == 1,
         "expected one deployment for %s, found %d" % (commit, len(matches)))
    return matches[0]


def log_status(tag):
    data = status_json()
    for item in deployments(data):
        LOG.write("%s: index=%d checksum=%s stateroot=%s serial=%d"
                  % (tag, item["index"], item["checksum"],
                     item["stateroot"], item["serial"]))
    (TRANSITIONS / (tag + ".status.json")).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def bls_fields(path):
    fields = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle.read().splitlines():
            body = raw.strip()
            if not body or body.startswith("#"):
                continue
            key, _, value = body.partition(" ")
            fields.setdefault(key, []).append(value.strip())
    return fields


def default_entry():
    raw = run(["bash", "-c",
               'source "$1" || exit 1; armada_default_bls_entry "$2"',
               "armada-lifecycle", str(ARGS_FILE), str(BOOT)])
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    need(bool(lines), "armada_default_bls_entry selected no entry under %s"
         % (BOOT / "loader/entries"))
    entry = Path(lines[-1])
    need(entry.is_file(), "selected BLS entry is not a file: %s" % entry)
    return entry


def build_tree(tag, kver, kernel, initrd, dtb, version):
    tree = TREES / tag
    release = ("ID=armada-lifecycle-test\n"
               "NAME=Armada lifecycle %s\n"
               "PRETTY_NAME=Armada lifecycle %s\n"
               "VERSION_ID=%s\n" % (tag, tag, version)).encode()
    files = {
        # The ticket names usr/etc/os-release; usr/lib/os-release is what
        # ostree parses for the BLS title, so both carry the same text.
        "usr/etc/os-release": release,
        "usr/lib/os-release": release,
        "usr/lib/modules/%s/vmlinuz" % kver: kernel,
        "usr/lib/modules/%s/initramfs.img" % kver: initrd,
        "usr/lib/modules/%s/%s" % (kver, ELDEN_DTB): dtb,
        "usr/share/test-marker": (tag + "\n").encode(),
    }
    for relative, content in files.items():
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return tree


def commit_tree(tag, tree):
    run(["ostree", "commit", "--repo=" + str(REPO),
         "--branch=lifecycle/" + tag, "--tree=dir=" + str(tree)])
    commit = run(["ostree", "rev-parse", "--repo=" + str(REPO),
                  "lifecycle/" + tag]).strip()
    need(len(commit) == 64, "unexpected commit id for %s: %r" % (tag, commit))
    LOG.write("%s: committed %s" % (tag, commit))
    return commit


def deploy(commit):
    run(["ostree", "admin", "deploy", "--sysroot=" + str(SYSROOT),
         "--os=default", "--retain",
         "--karg=root=UUID=" + ROOT_UUID,
         "--karg=boot=UUID=" + BOOT_UUID,
         "--karg=rw", commit])


def select_metadata(entry, tag):
    out = TRANSITIONS / tag
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "metadata.json"
    run([sys.executable, str(SELECTOR),
         "--boot-dir", str(BOOT), "--sysroot", str(SYSROOT),
         "--entry", str(entry), "--output", str(meta_path)])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    shutil.copyfile(entry, out / "bls.conf")
    entries = out / "entries"
    entries.mkdir(exist_ok=True)
    for path in sorted((BOOT / "loader/entries").glob("*.conf")):
        shutil.copyfile(path, entries / path.name)
    (out / "status.json").write_text(
        json.dumps(status_json(), indent=2) + "\n", encoding="utf-8")
    links = collect_links()
    (out / "links.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
    LOG.write("%s: selector bls=%s commit=%s image=%s initrd=%s fdtdir=%s"
              % (tag, meta["bls"], meta["deployment_checksum"], meta["image"],
                 meta["initrd"], meta["fdtdir"]))
    for line in links:
        LOG.write("%s: link %s" % (tag, line))
    return meta


def collect_links():
    lines = []
    ostree_root = SYSROOT / "ostree"
    for path in sorted(ostree_root.iterdir()):
        if not path.name.startswith("boot."):
            continue
        if path.is_symlink():
            lines.append("%s -> %s" % (path.relative_to(SYSROOT),
                                       os.readlink(str(path))))
        else:
            lines.append("%s (dir)" % path.relative_to(SYSROOT))
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    lines.append("%s -> %s" % (child.relative_to(SYSROOT),
                                               os.readlink(str(child))))
    return lines


def check_transition(tag, commit, tree, kver):
    deployment = find_deployment(commit)
    entry = default_entry()
    meta = select_metadata(entry, tag)

    need(meta["deployment_checksum"] == commit,
         "%s: default BLS selected %s, expected %s"
         % (tag, meta["deployment_checksum"], commit))
    expected_bls = os.path.relpath(str(entry), str(BOOT)).replace(os.sep, "/")
    need(meta["bls"] == expected_bls,
         "%s: selector reported %r, expected %r" % (tag, meta["bls"], expected_bls))

    fields = bls_fields(entry)
    for key in ("linux", "initrd", "fdtdir", "options"):
        need(len(fields.get(key, [])) == 1,
             "%s: BLS %s has %d %r lines" % (tag, entry, len(fields.get(key, [])), key))
    native_options = fields["options"][0]
    need(meta["options"] == native_options,
         "%s: selector options %r != native BLS options %r"
         % (tag, meta["options"], native_options))
    tokens = meta["options"].split()
    need("root=UUID=" + ROOT_UUID in tokens,
         "%s: native options lost root=UUID" % tag)
    need("boot=UUID=" + BOOT_UUID in tokens,
         "%s: native options lost boot=UUID" % tag)
    need("rw" in tokens, "%s: native options lost rw" % tag)
    need(sum(1 for token in tokens if token.startswith("ostree=")) == 1,
         "%s: expected exactly one ostree= token" % tag)

    image = BOOT / meta["image"]
    initrd = BOOT / meta["initrd"]
    dtb = BOOT / meta["fdtdir"] / os.path.relpath(ELDEN_DTB, "dtb")
    need(image.is_file(), "%s: image missing: %s" % (tag, image))
    need(initrd.is_file(), "%s: initrd missing: %s" % (tag, initrd))
    need(dtb.is_file(), "%s: Elden DT missing: %s" % (tag, dtb))

    expected_kernel = tree / "usr/lib/modules" / kver / "vmlinuz"
    expected_initrd = tree / "usr/lib/modules" / kver / "initramfs.img"
    expected_dtb = tree / "usr/lib/modules" / kver / ELDEN_DTB
    image_hash = digest(image)
    initrd_hash = digest(initrd)
    dtb_hash = digest(dtb)
    need(image_hash == digest(expected_kernel),
         "%s: deployed image does not match the %s tree" % (tag, kver))
    need(initrd_hash == digest(expected_initrd),
         "%s: deployed initrd does not match the %s tree" % (tag, kver))
    need(dtb_hash == digest(expected_dtb),
         "%s: deployed DT does not match the %s tree" % (tag, kver))

    ostree_value = next(token[len("ostree="):] for token in tokens
                        if token.startswith("ostree="))
    link_real = Path(os.path.realpath(SYSROOT / ostree_value.lstrip("/")))
    deploy_dir = SYSROOT / "ostree/deploy/default/deploy" / (
        "%s.%d" % (commit, deployment["serial"]))
    need(deploy_dir.is_dir(), "%s: deployment dir missing: %s" % (tag, deploy_dir))
    need(link_real == deploy_dir.resolve(),
         "%s: ostree= resolves to %s, expected %s"
         % (tag, link_real, deploy_dir.resolve()))

    record = {
        "stage": tag,
        "expected_commit": commit,
        "observed_commit": meta["deployment_checksum"],
        "bls": meta["bls"],
        "deployment": deployment,
        "image": meta["image"],
        "initrd": meta["initrd"],
        "fdtdir": meta["fdtdir"],
        "options": meta["options"],
        "kernel_sha256": image_hash,
        "initrd_sha256": initrd_hash,
        "dtb_sha256": dtb_hash,
    }
    SUMMARY["transitions"].append(record)
    LOG.write("%s: PASS commit=%s serial=%d kernel=%s"
              % (tag, commit, deployment["serial"], image_hash[:12]))
    return record


def set_default(tag, commit, serial):
    out = TRANSITIONS / tag
    out.mkdir(parents=True, exist_ok=True)
    data = status_json()
    (out / "status-before.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # Recompute the index from status immediately before the call.
    matches = [item for item in deployments(data)
               if item["checksum"] == commit
               and item["stateroot"] == "default"
               and item["serial"] == serial]
    need(len(matches) == 1,
         "%s: expected one deployment for %s.%d, found %d"
         % (tag, commit, serial, len(matches)))
    index = matches[0]["index"]
    (out / "selection.json").write_text(json.dumps({
        "commit": commit, "serial": serial, "stateroot": "default",
        "index": index,
    }, indent=2) + "\n", encoding="utf-8")
    LOG.write("%s: set-default index %d for %s.%d" % (tag, index, commit, serial))
    run(["ostree", "admin", "set-default", "--sysroot=" + str(SYSROOT),
         str(index)])
    after = status_json()
    (out / "status-after.json").write_text(
        json.dumps(after, indent=2) + "\n", encoding="utf-8")
    return deployments(after)


def run_flow():
    version = run(["ostree", "--version"])
    need(bool(version.strip()), "ostree --version produced no output")
    SUMMARY["ostree_version"] = version.strip()
    LOG.write("ostree version: %s" % SUMMARY["ostree_version"])

    SYSROOT.mkdir()
    run(["ostree", "admin", "init-fs", "--modern", str(SYSROOT)])
    run(["ostree", "admin", "os-init", "--sysroot=" + str(SYSROOT), "default"])
    run(["ostree", "config", "--repo=" + str(REPO),
         "set", "sysroot.bootloader", "none"])
    run(["ostree", "config", "--repo=" + str(REPO),
         "set", "sysroot.bootprefix", "true"])
    LOG.write("config: sysroot.bootloader=%s sysroot.bootprefix=%s" % (
        run(["ostree", "config", "--repo=" + str(REPO),
             "get", "sysroot.bootloader"]).strip(),
        run(["ostree", "config", "--repo=" + str(REPO),
             "get", "sysroot.bootprefix"]).strip()))

    kernel_a = b"KERNEL-A lifecycle fixture\n"
    kernel_b = b"KERNEL-B lifecycle fixture\n"
    tree_a = build_tree("A", "6.0.0-lifecycle-a", kernel_a,
                        b"INITRD-A\n", b"DTB-A\n", "1")
    tree_b = build_tree("B", "6.1.0-lifecycle-b", kernel_b,
                        b"INITRD-B\n", b"DTB-B\n", "2")
    tree_c = build_tree("C", "6.1.0-lifecycle-b", kernel_b,
                        b"INITRD-B\n", b"DTB-B\n", "3")

    TRANSITIONS.mkdir(parents=True, exist_ok=True)
    b_record = None
    for tag, tree, kver in (("A", tree_a, "6.0.0-lifecycle-a"),
                            ("B", tree_b, "6.1.0-lifecycle-b"),
                            ("C", tree_c, "6.1.0-lifecycle-b")):
        commit = commit_tree(tag, tree)
        deploy(commit)
        log_status("deploy-" + tag)
        record = check_transition("0%d-%s" % (1 + "ABC".index(tag), tag),
                                  commit, tree, kver)
        SUMMARY["deployments"].append({
            "id": tag,
            "commit": commit,
            "kver": kver,
            "serial": record["deployment"]["serial"],
            "initial_index": record["deployment"]["index"],
        })
        if tag == "B":
            b_record = record
        if tag == "C":
            need(record["observed_commit"] != b_record["observed_commit"],
                 "C and B have the same deployment checksum")
            need(record["kernel_sha256"] == b_record["kernel_sha256"],
                 "C kernel differs from B")
            need(record["initrd_sha256"] == b_record["initrd_sha256"],
                 "C initrd differs from B")
            need(record["dtb_sha256"] == b_record["dtb_sha256"],
                 "C DT differs from B")

    deployments_a = SUMMARY["deployments"][0]
    deployments_b = SUMMARY["deployments"][1]
    retained = deployments(status_json())
    need(len(retained) == 3,
         "expected three retained deployments, found %d" % len(retained))
    set_default("04-set-default-A", deployments_a["commit"],
                deployments_a["serial"])
    check_transition("04-default-A", deployments_a["commit"], tree_a,
                     "6.0.0-lifecycle-a")
    set_default("05-set-default-B", deployments_b["commit"],
                deployments_b["serial"])
    check_transition("05-default-B", deployments_b["commit"], tree_b,
                     "6.1.0-lifecycle-b")

    entries = sorted((BOOT / "loader/entries").glob("*.conf"))
    LOG.write("final BLS entries: %s" % ", ".join(path.name for path in entries))
    need(len(entries) >= 3,
         "expected at least three native BLS entries, found %d" % len(entries))


def prepare(args):
    global WORK, SYSROOT, REPO, BOOT, TREES, TRANSITIONS
    global REPO_ROOT, ARGS_FILE, SELECTOR, LOG

    WORK = Path(args.work).expanduser().resolve()
    need(WORK != Path(os.path.abspath(os.sep)), "--work must not be /")
    if WORK.exists():
        need(WORK.is_dir(), "--work is not a directory: %s" % WORK)
        need(not any(WORK.iterdir()), "--work must be absent or empty: %s" % WORK)
    else:
        WORK.mkdir(parents=True)

    LOG = Log(WORK / "lifecycle.log")
    LOG.write("started: %s" % now())
    LOG.write("work: %s" % WORK)

    REPO_ROOT = Path(args.repo_root).expanduser().resolve()
    need(REPO_ROOT.is_dir(), "--repo-root is not a directory: %s" % REPO_ROOT)
    ARGS_FILE = REPO_ROOT / "system_files/usr/lib/armada/bootimg-args"
    SELECTOR = REPO_ROOT / "system_files/usr/libexec/armada/kshim-select-boot.py"
    need(ARGS_FILE.is_file(), "shared bootimg-args missing: %s" % ARGS_FILE)
    need(SELECTOR.is_file(), "kshim-select-boot.py missing: %s" % SELECTOR)
    need(shutil.which("ostree") is not None, "native ostree not found in PATH")
    need(shutil.which("bash") is not None, "bash not found in PATH")

    SYSROOT = WORK / "sysroot"
    REPO = SYSROOT / "ostree/repo"
    BOOT = SYSROOT / "boot"
    TREES = WORK / "trees"
    TRANSITIONS = WORK / "transitions"

    SUMMARY["work"] = str(WORK)
    SUMMARY["sysroot"] = str(SYSROOT)
    SUMMARY["repo"] = str(REPO)
    SUMMARY["repo_root"] = str(REPO_ROOT)
    SUMMARY["kargs"] = ["root=UUID=" + ROOT_UUID,
                        "boot=UUID=" + BOOT_UUID, "rw"]
    SUMMARY["started"] = now()
    LOG.write("repo-root: %s" % REPO_ROOT)
    LOG.write("native ostree: %s" % shutil.which("ostree"))


def write_summary():
    SUMMARY["finished"] = now()
    SUMMARY["limitations"] = LIMITATIONS
    path = WORK / "lifecycle-summary.json"
    path.write_text(json.dumps(SUMMARY, indent=2) + "\n", encoding="utf-8")
    LOG.write("summary: %s" % path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Real OSTree deployment lifecycle check for kShimC selection")
    parser.add_argument("--work", required=True,
                        help="absent or empty directory for all evidence")
    parser.add_argument("--repo-root", required=True,
                        help="armadaos-freedom checkout providing the real scripts")
    args = parser.parse_args(argv)

    try:
        prepare(args)
    except (Failure, OSError) as error:
        sys.stderr.write("kshim-ostree-lifecycle: %s\n" % error)
        if LOG is not None:
            SUMMARY["result"] = "fail"
            SUMMARY["error"] = str(error)
            try:
                write_summary()
            except OSError:
                pass
            LOG.close()
        return 2

    exit_code = 0
    try:
        run_flow()
        SUMMARY["result"] = "pass"
    except Exception as error:  # noqa: BLE001 - record any native failure honestly
        exit_code = 1
        SUMMARY["result"] = "fail"
        SUMMARY["error"] = "%s: %s" % (type(error).__name__, error)
        LOG.write("FAIL: %s" % SUMMARY["error"])
        LOG.traceback()
    finally:
        try:
            write_summary()
        except OSError as error:
            LOG.write("could not write summary: %s" % error)
            SUMMARY["result"] = "fail"
            exit_code = 1
        LOG.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
