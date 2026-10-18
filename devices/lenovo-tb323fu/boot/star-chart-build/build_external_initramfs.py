#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline external initramfs builder for the TB323FU Gen5 tree (Linux).

Rebuilds the accepted external initramfs from a gzip-compressed newc cpio
template and a staged kernel module tree. The template is authoritative for
everything except /lib/modules: init, armada-root-resolve, helper binaries,
firmware and fonts are preserved and checked bit-for-bit plus mode, symlink
target and device numbers. /lib/modules is replaced by one ZSTD squashfs image
named after the kernel release; the unified kernel is built with
CONFIG_SQUASHFS_ZSTD=y and XZ off, so no XZ support is assumed.

Matching full-KVER modules must be installed separately in the selected
internal/TF root. By default all staged modules are included in the
initramfs; --early-only instead keeps the transitive modules.dep closure
of the required early modules after building the full module index.

Requires a Linux host/container with gzip, cpio, depmod and mksquashfs (ZSTD).
The template may contain device nodes, so extraction must run as root.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys

sys.dont_write_bytecode = True

KVER_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
REQUIRED_MODULES = ("qcom_q6v5_pas.ko", "nt36536_ts.ko")
COMPRESSED_MODULE_SUFFIXES = (".ko.xz", ".ko.zst", ".ko.gz")
MODULE_TOP_LEVEL_EXCLUDES = ("vmlinuz", "dtb", "build", "source")
CPIO_HEADER_SIZE = 110
CPIO_MAGICS = (b"070701", b"070702")
MODULES_SKIP = "lib/modules"
OUTPUT_INITRAMFS = "initramfs.cpio.gz"

STAGE = "init"
OUT = None
WORK = None
LOGS = None
KVER = None
OUT_OWNED = False


class BuildError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage
        self.message = message


def set_stage(stage):
    global STAGE
    STAGE = stage


def fail(message):
    raise BuildError(STAGE, message)


def require_tool(name):
    path = shutil.which(name)
    if not path:
        fail("required tool not found in PATH: %s" % name)
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def last_stderr(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(4000)
    except OSError:
        return ""
    lines = [line.strip() for line in data.decode("utf-8", "replace").splitlines()
             if line.strip()]
    if not lines:
        return ""
    text = lines[-1]
    return text[:300]


def run(cmd, name, cwd=None, stdin_path=None, input_bytes=None,
        stdout_path=None):
    out_log = os.path.join(LOGS, name + ".stdout")
    err_log = os.path.join(LOGS, name + ".stderr")
    stdout_target = stdout_path or out_log

    stdin_handle = None
    kwargs = {}
    if stdin_path is not None:
        stdin_handle = open(stdin_path, "rb")
        kwargs["stdin"] = stdin_handle
    elif input_bytes is not None:
        kwargs["input"] = input_bytes

    try:
        with open(stdout_target, "wb") as fout, open(err_log, "wb") as ferr:
            proc = subprocess.run(cmd, cwd=cwd, stdout=fout, stderr=ferr,
                                  **kwargs)
    finally:
        if stdin_handle is not None:
            stdin_handle.close()

    if proc.returncode != 0:
        detail = last_stderr(err_log)
        fail("command failed rc=%d: %s%s"
             % (proc.returncode, " ".join(cmd),
                (": " + detail) if detail else ""))
    return out_log


def entry_type(mode):
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISCHR(mode):
        return "char"
    if stat.S_ISBLK(mode):
        return "block"
    if stat.S_ISFIFO(mode):
        return "fifo"
    return None


def under_modules(name):
    return name == MODULES_SKIP or name.startswith(MODULES_SKIP + "/")


def normalize_member(raw_name):
    name = raw_name
    if name.startswith("/"):
        fail("unsafe template path (absolute): %r" % name)
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if name in ("", "."):
        return None
    if name.startswith("/"):
        fail("unsafe template path (absolute): %r" % name)
    for part in name.split("/"):
        if part in ("", ".", ".."):
            fail("unsafe template path (%r component): %r" % (part, name))
    return name


def parse_cpio(path):
    members = []
    file_size = os.path.getsize(path)
    with open(path, "rb") as handle:
        while True:
            offset = handle.tell()
            header = handle.read(CPIO_HEADER_SIZE)
            if len(header) != CPIO_HEADER_SIZE:
                fail("truncated cpio header at offset %d" % offset)
            magic = header[:6]
            if magic not in CPIO_MAGICS:
                fail("bad cpio magic at offset %d: %r" % (offset, magic))
            try:
                fields = [int(header[6 + 8 * i:14 + 8 * i], 16)
                          for i in range(13)]
            except ValueError:
                fail("non-hex cpio header at offset %d" % offset)
            (ino, mode, uid, gid, nlink, mtime, filesize, devmajor, devminor,
             rdevmajor, rdevminor, namesize, check) = fields
            if namesize < 1:
                fail("cpio member with empty name at offset %d" % offset)
            name_bytes = handle.read(namesize)
            if len(name_bytes) != namesize:
                fail("truncated cpio name at offset %d" % offset)
            if not name_bytes.endswith(b"\x00"):
                fail("cpio name is not NUL-terminated at offset %d" % offset)
            name = os.fsdecode(name_bytes[:-1])
            name_pad = (-(CPIO_HEADER_SIZE + namesize)) % 4
            if len(handle.read(name_pad)) != name_pad:
                fail("truncated cpio name padding at offset %d" % offset)
            data_offset = handle.tell()
            next_offset = data_offset + filesize + (-filesize % 4)
            if next_offset > file_size:
                fail("truncated cpio data for %r" % name)
            handle.seek(next_offset)
            if name == "TRAILER!!!":
                break
            kind = entry_type(mode)
            if kind is None:
                fail("unsupported cpio member type for %r" % name)
            members.append({
                "name": name,
                "raw_name": name,
                "mode": mode,
                "uid": uid,
                "gid": gid,
                "type": kind,
                "filesize": filesize,
                "data_offset": data_offset,
                "rdev": os.makedev(rdevmajor, rdevminor) if kind in
                        ("char", "block") else 0,
            })
    if not members:
        fail("template cpio contains no members")
    return members


def validate_members(members, require_root=False):
    seen = {}
    normalized = []
    for member in members:
        name = normalize_member(member["raw_name"])
        if name is None:
            continue
        if name in seen:
            fail("duplicate template member: %r" % name)
        member["name"] = name
        seen[name] = member
        normalized.append(member)
    if not normalized:
        fail("template cpio contains no usable members")
    for member in normalized:
        parts = member["name"].split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and seen[parent]["type"] != "dir":
                fail("template member %r uses a non-directory parent %r"
                     % (member["name"], parent))
    if require_root and os.geteuid() != 0:
        devices = [m["name"] for m in normalized
                   if m["type"] in ("char", "block")]
        if devices:
            fail("template contains device nodes (%s); extraction requires "
                 "root inside the Linux Docker build container"
                 % ", ".join(devices[:3]))
    return normalized


def apply_modes(stage, members):
    for member in members:
        path = os.path.join(stage, *member["name"].split("/"))
        if member["type"] == "symlink":
            continue
        try:
            os.chmod(path, stat.S_IMODE(member["mode"]))
        except OSError as error:
            fail("cannot restore mode on %r: %s" % (member["name"], error))


def verify_extracted(stage, members):
    for member in members:
        path = os.path.join(stage, *member["name"].split("/"))
        try:
            info = os.lstat(path)
        except OSError:
            fail("template member missing after extraction: %r"
                 % member["name"])
        kind = entry_type(info.st_mode)
        if kind != member["type"]:
            fail("template member %r changed type during extraction"
                 % member["name"])
        if member["type"] != "symlink":
            if stat.S_IMODE(info.st_mode) != stat.S_IMODE(member["mode"]):
                fail("template member %r has wrong mode after extraction"
                     % member["name"])
        if member["type"] in ("char", "block") and info.st_rdev != member["rdev"]:
            fail("template device %r has wrong device number"
                 % member["name"])


def describe(before, after):
    missing = sorted(set(before) - set(after))[:3]
    added = sorted(set(after) - set(before))[:3]
    changed = [name for name in sorted(set(before) & set(after))
               if before[name] != after[name]][:3]
    return "missing=%s added=%s changed=%s" % (missing, added, changed)


def file_attributes(path):
    info = os.lstat(path)
    kind = entry_type(info.st_mode)
    record = {"type": kind, "mode": stat.S_IMODE(info.st_mode)}
    if kind == "file":
        record["sha256"] = sha256_file(path)
        record["size"] = info.st_size
    elif kind == "symlink":
        record["target"] = os.readlink(path)
    elif kind in ("char", "block"):
        record["rdev"] = info.st_rdev
    return record


def walk_entries(root):
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                               followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        kept = []
        for name in sorted(dirnames):
            rel = (rel_dir + "/" + name) if rel_dir else name
            if under_modules(rel):
                continue
            entries[rel] = file_attributes(os.path.join(dirpath, name))
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            rel = (rel_dir + "/" + name) if rel_dir else name
            if under_modules(rel):
                continue
            entries[rel] = file_attributes(os.path.join(dirpath, name))
    return entries


def payload_files(entries):
    return {name: record for name, record in entries.items()
            if record["type"] != "dir"}


def scan_modules(root, label):
    compressed = []
    modules = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            rel = rel.replace(os.sep, "/")
            if name.endswith(COMPRESSED_MODULE_SUFFIXES):
                compressed.append(rel)
            elif name.endswith(".ko"):
                modules.append(rel)
    if compressed:
        fail("%s contains compressed modules, but the accepted init loads "
             "uncompressed .ko only: %s"
             % (label, ", ".join(compressed[:3])))
    basenames = {os.path.basename(path) for path in modules}
    missing = [name for name in REQUIRED_MODULES if name not in basenames]
    if missing:
        fail("%s is missing required module(s): %s"
             % (label, ", ".join(missing)))
    if not modules:
        fail("%s contains no uncompressed .ko modules" % label)
    return modules


def read_dep_index(dep_path, modules):
    """Parse modules.dep into an exact path -> dependency list mapping."""
    known = set(modules)
    index = {}
    try:
        handle = open(dep_path, "r", encoding="utf-8", errors="replace")
    except OSError as error:
        fail("cannot read modules.dep: %s" % error)
    with handle:
        for number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            module, separator, remainder = line.partition(":")
            module = module.strip()
            if not separator or not module:
                fail("malformed modules.dep line %d: %r" % (number, line))
            if module in index:
                fail("modules.dep lists %r more than once" % module)
            if module not in known:
                fail("modules.dep lists %r, which is not staged" % module)
            deps = remainder.split()
            seen = set()
            for dep in deps:
                if dep in seen:
                    fail("modules.dep repeats dependency %r for %r"
                         % (dep, module))
                seen.add(dep)
                if dep not in known:
                    fail("modules.dep dependency %r for %r is not staged"
                         % (dep, module))
            index[module] = deps
    return index


def resolve_early_modules(dep_path, modules):
    """Return the transitive modules.dep closure of REQUIRED_MODULES."""
    index = read_dep_index(dep_path, modules)
    by_basename = {}
    for relative in modules:
        by_basename.setdefault(os.path.basename(relative), []).append(relative)

    pending = []
    for required in REQUIRED_MODULES:
        matches = by_basename.get(required, [])
        if len(matches) != 1:
            fail("required module %r must appear exactly once in the staged "
                 "tree (found %d)" % (required, len(matches)))
        pending.append(matches[0])

    selected = set()
    while pending:
        current = pending.pop()
        if current in selected:
            continue
        if current not in index:
            fail("modules.dep has no entry for staged module %r" % current)
        selected.add(current)
        pending.extend(index[current])
    return selected


def remove_unselected_modules(staged_modules, modules, selected):
    """Delete every staged .ko that is outside the selected closure."""
    for relative in modules:
        if relative in selected:
            continue
        path = os.path.join(staged_modules, *relative.split("/"))
        try:
            os.unlink(path)
        except OSError as error:
            fail("cannot remove unselected module %r: %s" % (relative, error))


def filter_modules_order(staged_modules, selected):
    """Keep only selected paths in modules.order, preserving their order."""
    path = os.path.join(staged_modules, "modules.order")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        fail("cannot read modules.order: %s" % error)
    kept = []
    seen = set()
    for line in lines:
        entry = line.strip()
        if entry and entry in selected and entry not in seen:
            seen.add(entry)
            kept.append(entry)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            if kept:
                handle.write("\n".join(kept) + "\n")
    except OSError as error:
        fail("cannot rewrite modules.order: %s" % error)
    return True


def verify_early_index(dep_path, modules, selected):
    """Prove the rebuilt index is exactly the dependency closure."""
    index = read_dep_index(dep_path, modules)
    for relative in sorted(selected, key=os.fsencode):
        if relative not in index:
            fail("after pruning, modules.dep lost %r" % relative)
        for dep in index[relative]:
            if dep not in selected:
                fail("after pruning, %r still depends on %r"
                     % (relative, dep))


def copy_modules(module_dir, modules_root):
    target = os.path.join(modules_root, "lib", "modules", KVER)
    os.makedirs(os.path.dirname(target))

    def ignore_top_level(directory, names):
        if os.path.abspath(directory) == os.path.abspath(module_dir):
            return [name for name in names
                    if name in MODULE_TOP_LEVEL_EXCLUDES]
        return []

    shutil.copytree(module_dir, target, symlinks=True,
                    ignore=ignore_top_level)
    return target


def normalize_mtimes(root):
    for dirpath, dirnames, filenames in os.walk(root, topdown=False,
                                               followlinks=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                os.utime(path, (0, 0), follow_symlinks=False)
            except OSError:
                pass
        for name in dirnames:
            path = os.path.join(dirpath, name)
            try:
                os.utime(path, (0, 0), follow_symlinks=False)
            except OSError:
                pass
        try:
            os.utime(dirpath, (0, 0))
        except OSError:
            pass


def collect_names(root):
    names = set()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                               followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        for name in dirnames + filenames:
            names.add((rel_dir + "/" + name) if rel_dir else name)
    return sorted(names, key=os.fsencode)


def member_sha256(path, member):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        handle.seek(member["data_offset"])
        remaining = member["filesize"]
        while remaining:
            chunk = handle.read(min(1 << 20, remaining))
            if not chunk:
                fail("short read while hashing cpio member %r"
                     % member["name"])
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def prepare_output(args):
    global OUT, WORK, LOGS, OUT_OWNED, KVER

    set_stage("validate")
    if not args.out:
        fail("--out is required")
    OUT = os.path.abspath(args.out)
    if os.path.islink(args.out) or os.path.islink(OUT):
        fail("output directory must not be a symlink: %s" % args.out)

    KVER = args.kernel_release
    if not KVER_RE.match(KVER or ""):
        fail("invalid --kernel-release: %r" % KVER)

    template = os.path.abspath(args.template_initrd)
    module_dir = os.path.abspath(args.module_dir)
    if os.path.islink(module_dir):
        fail("--module-dir must be a real directory, not a symlink")
    if not os.path.isfile(template):
        fail("template initramfs not found: %s" % template)
    if not os.path.isdir(module_dir):
        fail("module directory not found: %s" % module_dir)
    if os.path.basename(module_dir) != KVER:
        fail("--module-dir must end in the exact kernel release %r" % KVER)
    if os.path.basename(os.path.dirname(module_dir)) != "modules":
        fail("--module-dir must be <staging>/lib/modules/<KVER>")
    if os.path.basename(os.path.dirname(os.path.dirname(module_dir))) != "lib":
        fail("--module-dir must be <staging>/lib/modules/<KVER>")

    real_out = os.path.realpath(OUT)
    for label, path in (("template", template), ("module-dir", module_dir)):
        real_in = os.path.realpath(path)
        if real_in == real_out or real_in.startswith(real_out + os.sep):
            fail("%s lives inside --out; refusing to overwrite inputs" % label)

    if os.path.exists(OUT):
        if not os.path.isdir(OUT):
            fail("--out exists and is not a directory: %s" % OUT)
        if os.listdir(OUT):
            fail("--out is not empty; refusing to delete existing contents")
    else:
        os.makedirs(OUT)
    OUT_OWNED = True

    WORK = os.path.join(OUT, "work")
    LOGS = os.path.join(OUT, "logs")
    os.makedirs(WORK)
    os.makedirs(LOGS)
    return template, module_dir


def build(template, module_dir, early_only):
    stage = os.path.join(WORK, "stage")
    os.makedirs(stage)

    set_stage("template-gzip")
    require_tool("gzip")
    with open(template, "rb") as handle:
        head = handle.read(10)
    if len(head) < 10 or head[:2] != b"\x1f\x8b" or head[2] != 8:
        fail("template is not a gzip-compressed initramfs")
    template_cpio = os.path.join(WORK, "template.cpio")
    run(["gzip", "-dc", template], "gzip_template",
        stdout_path=template_cpio)

    set_stage("template-paths")
    members = validate_members(parse_cpio(template_cpio), require_root=True)

    set_stage("template-extract")
    require_tool("cpio")
    run(["cpio", "-idm", "--quiet", "--no-absolute-filenames"],
        "cpio_extract", cwd=stage, stdin_path=template_cpio)
    apply_modes(stage, members)
    verify_extracted(stage, members)

    init_path = os.path.join(stage, "init")
    if not os.path.exists(init_path) or os.path.isdir(init_path):
        fail("accepted template has no usable /init")

    baseline = walk_entries(stage)
    expected = {member["name"] for member in members
                if member["type"] != "dir" and not under_modules(member["name"])}
    actual = set(payload_files(baseline))
    if expected != actual:
        fail("extracted template does not match its member list: %s"
             % describe(expected, actual))
    before = payload_files(baseline)

    set_stage("modules")
    scan_modules(module_dir, "module-dir")
    modules_root = os.path.join(WORK, "modules-root")
    staged_modules = copy_modules(module_dir, modules_root)
    modules = scan_modules(staged_modules, "staged module tree")

    set_stage("depmod")
    require_tool("depmod")
    run(["depmod", "-b", modules_root, KVER], "depmod", cwd=WORK)
    dep_path = os.path.join(staged_modules, "modules.dep")
    if not os.path.isfile(dep_path):
        fail("depmod did not create modules.dep in the staged layout")

    early_modules = None
    if early_only:
        set_stage("early-closure")
        early_modules = resolve_early_modules(dep_path, modules)
        remove_unselected_modules(staged_modules, modules, early_modules)
        modules = sorted(early_modules, key=os.fsencode)
        filter_modules_order(staged_modules, early_modules)

        set_stage("depmod-early")
        run(["depmod", "-b", modules_root, KVER], "depmod_early", cwd=WORK)
        if not os.path.isfile(dep_path):
            fail("depmod did not recreate modules.dep after pruning")
        verify_early_index(dep_path, modules, early_modules)

    set_stage("squashfs")
    require_tool("mksquashfs")
    image = os.path.join(WORK, KVER + ".sqfs")
    run([shutil.which("mksquashfs"), staged_modules, image,
         "-comp", "zstd", "-noappend", "-no-progress", "-all-root",
         "-mkfs-time", "0", "-all-time", "0"], "mksquashfs")
    with open(image, "rb") as handle:
        if handle.read(4) != b"hsqs":
            fail("mksquashfs did not produce a squashfs image")
    image_sha = sha256_file(image)
    image_size = os.path.getsize(image)

    set_stage("replace-modules")
    modules_dir = os.path.join(stage, "lib", "modules")
    lib_dir = os.path.join(stage, "lib")
    if os.path.lexists(lib_dir) and not os.path.isdir(lib_dir):
        fail("template /lib is not a directory; refusing to replace modules")
    if os.path.islink(lib_dir):
        fail("template /lib is a symlink; refusing to replace modules")
    if os.path.lexists(modules_dir):
        if os.path.islink(modules_dir) or not os.path.isdir(modules_dir):
            os.unlink(modules_dir)
        else:
            shutil.rmtree(modules_dir)
    os.makedirs(modules_dir)
    shutil.copyfile(image, os.path.join(modules_dir, KVER + ".sqfs"))
    if os.listdir(modules_dir) != [KVER + ".sqfs"]:
        fail("staged lib/modules contains unexpected entries")

    normalize_mtimes(stage)
    after = payload_files(walk_entries(stage))
    if before != after:
        fail("template files outside lib/modules changed: %s"
             % describe(before, after))

    set_stage("cpio")
    names = collect_names(stage)
    if not names:
        fail("staged root is empty")
    name_bytes = b"".join(os.fsencode(name) + b"\x00" for name in names)
    output_cpio = os.path.join(WORK, "initramfs.cpio")
    run(["cpio", "-o", "-H", "newc", "--owner", "0:0", "--null", "--quiet"],
        "cpio_create", cwd=stage, input_bytes=name_bytes,
        stdout_path=output_cpio)

    set_stage("assert-cpio")
    out_members = validate_members(parse_cpio(output_cpio))
    out_names = [member["name"] for member in out_members]
    if out_names != sorted(out_names, key=os.fsencode):
        fail("output cpio is not in deterministic name order")
    for member in out_members:
        if member["uid"] != 0 or member["gid"] != 0:
            fail("output cpio member %r is not root-owned"
                 % member["name"])

    squashfs_name = MODULES_SKIP + "/" + KVER + ".sqfs"
    module_files = [member["name"] for member in out_members
                    if member["type"] != "dir"
                    and under_modules(member["name"])]
    if module_files != [squashfs_name]:
        fail("output cpio lib/modules is not exactly %r: %r"
             % (squashfs_name, module_files))
    compressed = [name for name in out_names
                  if name.endswith((".ko",) + COMPRESSED_MODULE_SUFFIXES)]
    if compressed:
        fail("output initramfs still contains kernel modules: %r"
             % compressed[:3])

    archived = {member["name"] for member in out_members
                if member["type"] != "dir"}
    wanted = set(after) | {squashfs_name}
    if archived != wanted:
        fail("output cpio does not match the staged tree: %s"
             % describe(wanted, archived))

    squashfs_member = next(member for member in out_members
                           if member["name"] == squashfs_name)
    if squashfs_member["filesize"] != image_size:
        fail("archived squashfs length differs from the built image")
    if member_sha256(output_cpio, squashfs_member) != image_sha:
        fail("archived squashfs bytes differ from the built image")

    init_member = next((member for member in out_members
                        if member["name"] == "init"), None)
    if init_member is None:
        fail("output initramfs has no /init")
    if before["init"]["type"] == "file":
        if init_member["type"] != "file":
            fail("output /init changed type")
        if member_sha256(output_cpio, init_member) != before["init"]["sha256"]:
            fail("output /init bytes differ from the accepted template")

    set_stage("gzip")
    output_gz = os.path.join(OUT, OUTPUT_INITRAMFS)
    run(["gzip", "-n", "-9", "-c", output_cpio], "gzip_output",
        stdout_path=output_gz)
    with open(output_gz, "rb") as handle:
        gz_head = handle.read(8)
    if len(gz_head) != 8 or gz_head[:2] != b"\x1f\x8b":
        fail("gzip did not produce a gzip stream")
    if gz_head[4:8] != b"\x00\x00\x00\x00":
        fail("gzip output carries a non-zero mtime")

    cpio_sha = sha256_file(output_cpio)
    digest = hashlib.sha256()
    total = 0
    with gzip.open(output_gz, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            total += len(chunk)
    if digest.hexdigest() != cpio_sha or total != os.path.getsize(output_cpio):
        fail("gzip round-trip differs from the built cpio archive")

    set_stage("metadata")
    artifact = {
        "artifact": "external-initramfs",
        "kernel_release": KVER,
        "hardware_validated": False,
        "template": {
            "sha256": sha256_file(template),
            "bytes": os.path.getsize(template),
            "preserved_entries": len(before),
            "preserved_verified": True,
        },
        "modules": {
            "ko_count": len(modules),
            "required": list(REQUIRED_MODULES),
            "excluded_top_level": list(MODULE_TOP_LEVEL_EXCLUDES),
            "depmod": "ok",
            "squashfs_compression": "zstd",
            "early_only": early_only,
            "early_module_count": (len(early_modules)
                                   if early_modules is not None else None),
            "early_modules": (sorted(os.path.basename(path)
                                     for path in early_modules)
                              if early_modules is not None else None),
        },
        "initramfs": {
            "path": OUTPUT_INITRAMFS,
            "format": "newc",
            "compression": "gzip -n",
            "owner": "root:root",
            "bytes": os.path.getsize(output_gz),
            "sha256": sha256_file(output_gz),
        },
        "squashfs": {
            "path": squashfs_name,
            "bytes": image_size,
            "sha256": image_sha,
        },
        "module_policy": (
            "The initramfs carries only the early-boot /lib/modules/%s.sqfs "
            "image. Matching full-KVER modules must be installed separately "
            "in the selected internal/TF root." % KVER),
        "caveats": [
            "No TF root was supplied, so TF-side modules and TF boot are not "
            "validated.",
            "The accepted init and armada-root-resolve policy were not "
            "modified.",
            "hardware_validated is false: this is an offline build only.",
        ],
    }
    with open(os.path.join(OUT, "artifact.json"), "w",
              encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    sys.stdout.write("external initramfs built: %s\n"
                     % os.path.join(OUT, OUTPUT_INITRAMFS))
    sys.stdout.write("artifact metadata: %s\n"
                     % os.path.join(OUT, "artifact.json"))


def main():
    parser = argparse.ArgumentParser(
        description="Build the TB323FU Gen5 external initramfs offline")
    parser.add_argument("--template-initrd", required=True)
    parser.add_argument("--module-dir", required=True)
    parser.add_argument("--kernel-release", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--early-only", action="store_true",
        help="keep only the modules.dep closure of the required early modules")
    args = parser.parse_args()

    try:
        template, module_dir = prepare_output(args)
        build(template, module_dir, args.early_only)
    except BuildError as error:
        stage, message = error.stage, error.message
    except Exception as error:  # still leave a compact failure record
        stage, message = STAGE, "%s: %s" % (type(error).__name__, error)
    else:
        return 0

    if OUT_OWNED:
        failure = {
            "artifact": "external-initramfs",
            "stage": stage,
            "error": message,
            "hardware_validated": False,
        }
        try:
            with open(os.path.join(OUT, "failure.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(failure, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError:
            pass
    sys.stderr.write("FAILED at stage '%s': %s\n" % (stage, message))
    return 1


if __name__ == "__main__":
    sys.exit(main())
