#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_win64_string_fileptr_v33.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
files_c_p = wg / "Sources/Win32/wg_win32_files.c"
files_h_p = wg / "Sources/Win32/wg_win32_files.h"

for p in (engine_p, files_c_p, files_h_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

MARKER = "MXXHUB_WINDOWS_V33_WIN64_STRING_FILEPTR_FIX"

s = engine_p.read_text(encoding="utf-8")

if MARKER not in s:
    old = r'''            uint32_t wstr = args[2];
            int32_t  cch  = (int32_t)args[3];
            uint32_t mbstr = args[4];
            int32_t  cbmb = (int32_t)args[5];
'''
    new = r'''            /* MXXHUB_WINDOWS_V33_WIN64_STRING_FILEPTR_FIX
             * Win64 guest pointers are 64-bit. V32's device log showed Unity
             * passing buffers at 0x10A00000xx. Truncating those addresses to
             * uint32_t silently redirected the conversion below 4 GiB.
             */
            uint64_t wstr = args[2];
            int32_t  cch  = (int32_t)args[3];
            uint64_t mbstr = args[4];
            int32_t  cbmb = (int32_t)args[5];

            if ((wstr >> 32) || (mbstr >> 32)) {
                WG_LOGI(TAG,
                        "UTF64 V33: WideCharToMultiByte src=0x%llX dst=0x%llX "
                        "cch=%d cb=%d",
                        (unsigned long long)wstr,
                        (unsigned long long)mbstr,
                        cch, cbmb);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 WideCharToMultiByte pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            uint32_t mbstr = args[2];
            int32_t  cbmb  = (int32_t)args[3];
            uint32_t wstr  = args[4];
            int32_t  cch   = (int32_t)args[5];
'''
    new = r'''            uint64_t mbstr = args[2];
            int32_t  cbmb  = (int32_t)args[3];
            uint64_t wstr  = args[4];
            int32_t  cch   = (int32_t)args[5];

            if ((mbstr >> 32) || (wstr >> 32)) {
                WG_LOGI(TAG,
                        "UTF64 V33: MultiByteToWideChar src=0x%llX dst=0x%llX "
                        "cb=%d cch=%d",
                        (unsigned long long)mbstr,
                        (unsigned long long)wstr,
                        cbmb, cch);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 MultiByteToWideChar pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            uint32_t dst = args[0], n = args[2];
'''
    new = r'''            uint64_t dst = args[0];
            uint32_t n = (uint32_t)args[2];
            if (dst >> 32) {
                WG_LOGI(TAG, "MEM64 V33: memset dst=0x%llX n=%u",
                        (unsigned long long)dst, n);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 memset pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            uint32_t dst = args[0], src = args[1], n = args[2];
'''
    new = r'''            uint64_t dst = args[0], src = args[1];
            uint32_t n = (uint32_t)args[2];
            if ((dst >> 32) || (src >> 32)) {
                WG_LOGI(TAG,
                        "MEM64 V33: %s dst=0x%llX src=0x%llX n=%u",
                        fn,
                        (unsigned long long)dst,
                        (unsigned long long)src,
                        n);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 memcpy/memmove pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            uint32_t handle = args[0];
            uint32_t buf_addr = args[1];
            uint32_t nbytes = args[2];
            uint32_t bytes_read_addr = args[3];
'''
    new = r'''            uint32_t handle = (uint32_t)args[0];
            uint64_t buf_addr = args[1];
            uint32_t nbytes = (uint32_t)args[2];
            uint64_t bytes_read_addr = args[3];

            if ((buf_addr >> 32) || (bytes_read_addr >> 32)) {
                WG_LOGI(TAG,
                        "FILE64 V33 ReadFile: h=0x%X buf=0x%llX out=0x%llX n=%u",
                        handle,
                        (unsigned long long)buf_addr,
                        (unsigned long long)bytes_read_addr,
                        nbytes);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 ReadFile pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            uint32_t handle = args[0];
            uint32_t buf_addr = args[1];
            uint32_t nbytes = args[2];
            uint32_t bytes_written_addr = args[3];
'''
    new = r'''            uint32_t handle = (uint32_t)args[0];
            uint64_t buf_addr = args[1];
            uint32_t nbytes = (uint32_t)args[2];
            uint64_t bytes_written_addr = args[3];

            if ((buf_addr >> 32) || (bytes_written_addr >> 32)) {
                WG_LOGI(TAG,
                        "FILE64 V33 WriteFile: h=0x%X buf=0x%llX out=0x%llX n=%u",
                        handle,
                        (unsigned long long)buf_addr,
                        (unsigned long long)bytes_written_addr,
                        nbytes);
            }
'''
    if old not in s:
        raise SystemExit("ERROR: V33 WriteFile pointer anchor changed")
    s = s.replace(old, new, 1)

    old = r'''            const char *real = NULL;
            if (!args[0] || !apath[0]) {
'''
    new = r'''            if (args[0] >> 32) {
                WG_LOGI(TAG,
                        "FILE64 V33 CreateFileW: ptr=0x%llX decoded='%s'",
                        (unsigned long long)args[0], apath);
            }

            const char *real = NULL;
            if (!args[0] || !apath[0]) {
'''
    if old not in s:
        raise SystemExit("ERROR: V33 CreateFileW V26 anchor changed")
    s = s.replace(old, new, 1)

    # V8 introduced explicit downcasts at path-map call sites.
    s = s.replace("(uint32_t)args[0], engine->blink",
                  "args[0], engine->blink")

    engine_p.write_text(s, encoding="utf-8")
    print("V33: Win64 UTF/CRT/file guest pointers widened in wg_engine.c")
else:
    print("V33: engine pointer-width patch already present")

for p in (files_c_p, files_h_p):
    text = p.read_text(encoding="utf-8")
    old = "wg_files_map_path(uint32_t guest_path_addr, void *blink,"
    new = "wg_files_map_path(uint64_t guest_path_addr, void *blink,"
    if old in text:
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        print(f"V33: widened wg_files_map_path signature in {p.name}")
    elif new in text:
        print(f"V33: wg_files_map_path already widened in {p.name}")
    else:
        raise SystemExit(f"ERROR: V33 wg_files_map_path signature anchor changed in {p}")

engine = engine_p.read_text(encoding="utf-8")
files_c = files_c_p.read_text(encoding="utf-8")
files_h = files_h_p.read_text(encoding="utf-8")

required = (
    MARKER,
    "uint64_t wstr = args[2];",
    "uint64_t mbstr = args[2];",
    "UTF64 V33: MultiByteToWideChar",
    "UTF64 V33: WideCharToMultiByte",
    "MEM64 V33:",
    "FILE64 V33 ReadFile:",
    "FILE64 V33 WriteFile:",
    "FILE64 V33 CreateFileW:",
)

for token in required:
    if token not in engine:
        raise SystemExit("ERROR: V33 source verification failed: " + token)

if "uint32_t mbstr = args[2];" in engine:
    raise SystemExit("ERROR: stale 32-bit MultiByteToWideChar source pointer remains")
if "uint32_t wstr = args[2];" in engine:
    raise SystemExit("ERROR: stale 32-bit WideCharToMultiByte source pointer remains")

if "wg_files_map_path(uint64_t guest_path_addr, void *blink," not in files_c:
    raise SystemExit("ERROR: V33 files.c signature verification failed")
if "wg_files_map_path(uint64_t guest_path_addr, void *blink," not in files_h:
    raise SystemExit("ERROR: V33 files.h signature verification failed")

print("MXXHUB_WINDOWS_V33_WIN64_STRING_FILEPTR_FIX_OK")
