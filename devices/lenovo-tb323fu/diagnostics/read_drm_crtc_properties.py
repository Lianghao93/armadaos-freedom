#!/usr/bin/env python3
"""Read-only DRM CRTC property diagnostics via ctypes(libdrm.so.2).

Opens the DRM device O_RDONLY|O_CLOEXEC, enables per-file atomic query
capability only, and dumps CRTC property id/name/flags/value as JSON.
Never reads blob contents, never changes display state, never writes to
the device. On missing card or a failed libdrm call it emits a nonzero,
explicit error JSON -- no guessed values.

Usage: read_drm_crtc_properties.py [--device /dev/dri/card1]
"""
import argparse
import ctypes
import ctypes.util
import json
import os
import sys

DRM_MODE_OBJECT_CRTC = 0xCCCCCCCC
DRM_CLIENT_CAP_ATOMIC = 2
DRM_PROP_NAME_LEN = 32
MAX_COUNT = 4096
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
WATCHED_PROPS = ("DEGAMMA_LUT", "DEGAMMA_LUT_SIZE", "DPU_3D_LUT",
                 "DPU_3D_LUT_SIZE", "GAMMA_LUT", "CTM")


class drmModeRes(ctypes.Structure):
    _fields_ = [
        ("count_fbs", ctypes.c_int),
        ("fbs", ctypes.POINTER(ctypes.c_uint32)),
        ("count_crtcs", ctypes.c_int),
        ("crtcs", ctypes.POINTER(ctypes.c_uint32)),
        ("count_connectors", ctypes.c_int),
        ("connectors", ctypes.POINTER(ctypes.c_uint32)),
        ("count_encoders", ctypes.c_int),
        ("encoders", ctypes.POINTER(ctypes.c_uint32)),
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
    ]


class drmModeObjectProperties(ctypes.Structure):
    _fields_ = [
        ("count_props", ctypes.c_uint32),
        ("props", ctypes.POINTER(ctypes.c_uint32)),
        ("prop_values", ctypes.POINTER(ctypes.c_uint64)),
    ]


class drmModePropertyRes(ctypes.Structure):
    _fields_ = [
        ("prop_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("name", ctypes.c_char * DRM_PROP_NAME_LEN),
        ("count_values", ctypes.c_int),
        ("values", ctypes.POINTER(ctypes.c_uint64)),
        ("count_enums", ctypes.c_int),
        ("enums", ctypes.c_void_p),
        ("count_blobs", ctypes.c_int),
        ("blob_ids", ctypes.POINTER(ctypes.c_uint32)),
    ]


def load_libdrm():
    for name in ("libdrm.so.2", ctypes.util.find_library("drm")):
        if not name:
            continue
        try:
            return ctypes.CDLL(name, use_errno=True)
        except OSError:
            continue
    raise OSError("libdrm.so.2 not found")


def bind(lib):
    lib.drmModeGetResources.restype = ctypes.POINTER(drmModeRes)
    lib.drmModeGetResources.argtypes = [ctypes.c_int]
    lib.drmModeFreeResources.restype = None
    lib.drmModeFreeResources.argtypes = [ctypes.POINTER(drmModeRes)]
    lib.drmModeObjectGetProperties.restype = ctypes.POINTER(drmModeObjectProperties)
    lib.drmModeObjectGetProperties.argtypes = [ctypes.c_int, ctypes.c_uint32,
                                               ctypes.c_uint32]
    lib.drmModeFreeObjectProperties.restype = None
    lib.drmModeFreeObjectProperties.argtypes = [ctypes.POINTER(drmModeObjectProperties)]
    lib.drmModeGetProperty.restype = ctypes.POINTER(drmModePropertyRes)
    lib.drmModeGetProperty.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeFreeProperty.restype = None
    lib.drmModeFreeProperty.argtypes = [ctypes.POINTER(drmModePropertyRes)]
    lib.drmSetClientCap.restype = ctypes.c_int
    lib.drmSetClientCap.argtypes = [ctypes.c_int, ctypes.c_uint64, ctypes.c_uint64]


def read_crtc(lib, fd, crtc_id):
    entry = {"crtc_id": crtc_id, "properties": []}
    props = lib.drmModeObjectGetProperties(fd, crtc_id, DRM_MODE_OBJECT_CRTC)
    if not props:
        entry["error"] = "drmModeObjectGetProperties returned NULL"
        return entry
    try:
        p = props.contents
        if not 0 <= p.count_props <= MAX_COUNT:
            entry["error"] = "implausible count_props=%d" % p.count_props
            return entry
        if p.count_props > 0 and (not p.props or not p.prop_values):
            entry["error"] = "count_props=%d but props/prop_values NULL" % p.count_props
            return entry
        for i in range(p.count_props):
            pid = int(p.props[i])
            value = int(p.prop_values[i])
            prop = lib.drmModeGetProperty(fd, pid)
            if not prop:
                entry["properties"].append(
                    {"prop_id": pid, "value": value,
                     "error": "drmModeGetProperty returned NULL"})
                continue
            try:
                pr = prop.contents
                name = pr.name.split(b"\x00", 1)[0].decode("ascii", "replace")
                entry["properties"].append({
                    "prop_id": pid,
                    "name": name,
                    "flags": int(pr.flags),
                    "value": value,
                    "count_values": int(pr.count_values),
                    "count_enums": int(pr.count_enums),
                    "count_blobs": int(pr.count_blobs),
                })
            finally:
                lib.drmModeFreeProperty(prop)
    finally:
        lib.drmModeFreeObjectProperties(props)
    return entry


def build_report(lib, device):
    fd = os.open(device, os.O_RDONLY | O_CLOEXEC)
    try:
        st = os.fstat(fd)
        report = {"device": device, "dev_t": int(st.st_rdev),
                  "atomic_cap": None, "crtcs": []}

        ctypes.set_errno(0)
        rc = lib.drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1)
        err = ctypes.get_errno()
        report["atomic_cap"] = {"return": int(rc), "errno": int(err)}
        if rc != 0:
            return report, "drmSetClientCap(ATOMIC) unsupported, errno=%d" % err

        res = lib.drmModeGetResources(fd)
        if not res:
            return report, "drmModeGetResources returned NULL"
        try:
            r = res.contents
            if not 0 <= r.count_crtcs <= MAX_COUNT:
                return report, "implausible count_crtcs=%d" % r.count_crtcs
            for i in range(r.count_crtcs):
                report["crtcs"].append(read_crtc(lib, fd, int(r.crtcs[i])))
        finally:
            lib.drmModeFreeResources(res)
    finally:
        os.close(fd)

    if not report["crtcs"]:
        return report, "incomplete property query: no CRTCs reported"
    for crtc in report["crtcs"]:
        if "error" in crtc:
            return report, ("incomplete property query: crtc %d: %s"
                            % (crtc["crtc_id"], crtc["error"]))
        for prop in crtc["properties"]:
            if "error" in prop:
                return report, ("incomplete property query: crtc %d prop %d: %s"
                                % (crtc["crtc_id"], prop["prop_id"], prop["error"]))

    seen = set()
    for crtc in report["crtcs"]:
        for prop in crtc["properties"]:
            if "name" in prop:
                seen.add(prop["name"])
    report["property_presence"] = {n: (n in seen) for n in WATCHED_PROPS}
    return report, None


def emit(payload, code):
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser(description="Read-only DRM CRTC property dump")
    ap.add_argument("--device", default="/dev/dri/card1")
    args = ap.parse_args()

    try:
        lib = load_libdrm()
    except OSError as e:
        emit({"ok": False, "error": "libdrm unavailable: %s" % e,
              "device": args.device}, 1)
    try:
        bind(lib)
    except AttributeError as e:
        emit({"ok": False, "error": "libdrm missing symbol: %s" % e,
              "device": args.device}, 1)

    try:
        report, err = build_report(lib, args.device)
    except OSError as e:
        emit({"ok": False, "error": "device open/fstat failed: %s" %
              (e.strerror or e), "device": args.device, "errno": e.errno}, 1)

    report["ok"] = err is None
    if err:
        report["error"] = err
        emit(report, 1)
    emit(report, 0)


if __name__ == "__main__":
    main()
