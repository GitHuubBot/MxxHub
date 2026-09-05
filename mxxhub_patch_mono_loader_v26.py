#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_mono_loader_v26.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_MONO_LOADER_SEMANTICS_V26"

if MARKER not in s:
    # ------------------------------------------------------------------
    # V26A — correct LoadLibraryW/LoadLibraryExW failure semantics.
    #
    # Device proof from V25:
    #   LoadLibraryExW("...\mscorlib.dll.dll")
    #   -> disk load fails
    #   -> old WineGlass fabricates fake HMODULE 0x103E9000
    #   -> GetProcAddress(fake, "mono_aot_version") auto-stubs a function
    #   -> GetProcAddress(fake, "mono_aot_file_info") auto-stubs a function
    #
    # Windows does NOT turn an arbitrary missing non-system DLL into a valid
    # module handle. Mono uses this failed load as an AOT-probe; it expects NULL.
    # ------------------------------------------------------------------
    start = s.find('        } else if (strcmp(fn, "LoadLibraryExW") == 0 ||\n'
                   '                   strcmp(fn, "LoadLibraryW") == 0) {')
    end = s.find('        } else if (strcmp(fn, "GetModuleHandleA") == 0) {', start)
    if start < 0 or end < 0:
        raise SystemExit("ERROR: V26 wide loader block changed")

    new_loader = r'''        } else if (strcmp(fn, "LoadLibraryExW") == 0 ||
                   strcmp(fn, "LoadLibraryW") == 0) {
            /* MXXHUB_MONO_LOADER_SEMANTICS_V26 */
            uint16_t libname[512] = {0};
            char ascii[512] = {0};
            if (args[0]) {
                wg_blink_read_mem(engine->blink, args[0], libname, 1022);
                for (int i = 0; i < 511 && libname[i]; i++)
                    ascii[i] = libname[i] < 128 ? (char)libname[i] : '?';
            }

            WG_LOGI(TAG, "%s V26('%s')", fn, ascii);

            if (!ascii[0] || !args[0]) {
                ret_val = engine->pe_image
                    ? (uint32_t)engine->pe_image->image_base : 0x400000;
                s_last_error = 0;
            } else {
                uint32_t already = wg_module_find(ascii);
                if (already) {
                    ret_val = already;
                    s_last_error = 0;
                } else {
                    char mapbuf[512];
                    strncpy(mapbuf, ascii, sizeof(mapbuf) - 1);
                    mapbuf[sizeof(mapbuf) - 1] = '\0';

                    const char *real = wg_files_map_path(
                        args[0], engine->blink, mapbuf, sizeof(mapbuf));
                    bool disk_exists = real && access(real, R_OK) == 0;
                    uint32_t base = 0;

                    if (disk_exists) {
                        base = wg_load_dll(
                            engine->blink, engine->dll_mapper, real, ascii);
                    }

                    if (base) {
                        ret_val = base;
                        s_last_error = 0;
                        WG_LOGI(TAG,
                                "%s V26 real DLL '%s' -> 0x%X",
                                fn, ascii, base);
                    } else {
                        bool systemish =
                            strcasestr(ascii, "api-ms-win-") ||
                            strcasestr(ascii, "ext-ms-win-") ||
                            strcasestr(ascii, "kernel32") ||
                            strcasestr(ascii, "kernelbase") ||
                            strcasestr(ascii, "ntdll") ||
                            strcasestr(ascii, "user32") ||
                            strcasestr(ascii, "gdi32") ||
                            strcasestr(ascii, "advapi32") ||
                            strcasestr(ascii, "shell32") ||
                            strcasestr(ascii, "ole32") ||
                            strcasestr(ascii, "oleaut32") ||
                            strcasestr(ascii, "combase") ||
                            strcasestr(ascii, "bcrypt") ||
                            strcasestr(ascii, "crypt32") ||
                            strcasestr(ascii, "wintrust") ||
                            strcasestr(ascii, "version.dll") ||
                            strcasestr(ascii, "winmm") ||
                            strcasestr(ascii, "imm32") ||
                            strcasestr(ascii, "shlwapi") ||
                            strcasestr(ascii, "setupapi") ||
                            strcasestr(ascii, "cfgmgr32") ||
                            strcasestr(ascii, "powrprof") ||
                            strcasestr(ascii, "dbghelp") ||
                            strcasestr(ascii, "d3d") ||
                            strcasestr(ascii, "dxgi") ||
                            strcasestr(ascii, "xinput") ||
                            strcasestr(ascii, "ws2_32") ||
                            strcasestr(ascii, "winhttp");

                        if (systemish) {
                            ret_val = 0x10000000u +
                                (uint32_t)(engine->dll_mapper->count * 0x1000u);
                            s_last_error = 0;
                            WG_LOGI(TAG,
                                    "%s V26 emulated system DLL '%s' -> 0x%llX",
                                    fn, ascii, (unsigned long long)ret_val);
                        } else {
                            /* This is the key Mono fix: optional native/AOT
                             * probes must fail like Windows when no module
                             * exists, rather than receiving a fabricated HMODULE.
                             */
                            ret_val = 0;
                            s_last_error = disk_exists ? 1114 : 126;
                            WG_LOGI(TAG,
                                    "MONO LOAD V26 EXPECTED MISS: '%s' -> NULL err=%u",
                                    ascii, s_last_error);
                        }
                    }
                }
            }
'''
    s = s[:start] + new_loader + s[end:]

    # ------------------------------------------------------------------
    # V26B — CreateFileW("") must fail.
    #
    # V25 showed:
    #   CreateFileW('') -> handle 0x107
    # and the file layer opened the Hollow Knight directory itself. Windows
    # CreateFileW with an empty path returns INVALID_HANDLE_VALUE.
    # ------------------------------------------------------------------
    old_cf = r'''            const char *real = wg_files_map_path(args[0], engine->blink, apath, sizeof(apath));
            if (real) {
                ret_val = wg_files_create(real, args[1], args[4]);
            } else {
                ret_val = 0xFFFFFFFF;
            }
            WG_LOGI(TAG, "CreateFileW('%s') -> 0x%X", apath, (uint32_t)ret_val);
'''
    new_cf = r'''            const char *real = NULL;
            if (!args[0] || !apath[0]) {
                ret_val = 0xFFFFFFFFu; /* INVALID_HANDLE_VALUE */
                s_last_error = 2;      /* ERROR_FILE_NOT_FOUND */
                WG_LOGI(TAG, "CreateFileW V26 empty path -> INVALID_HANDLE_VALUE");
            } else {
                real = wg_files_map_path(
                    args[0], engine->blink, apath, sizeof(apath));
                if (real) {
                    ret_val = wg_files_create(real, args[1], args[4]);
                    if ((uint32_t)ret_val == 0xFFFFFFFFu) {
                        s_last_error = 2;
                    } else {
                        s_last_error = 0;
                    }
                } else {
                    ret_val = 0xFFFFFFFFu;
                    s_last_error = 3; /* ERROR_PATH_NOT_FOUND */
                }
                WG_LOGI(TAG, "CreateFileW V26('%s') -> 0x%X",
                        apath, (uint32_t)ret_val);
            }
'''
    if old_cf not in s:
        raise SystemExit("ERROR: V26 CreateFileW block changed")
    s = s.replace(old_cf, new_cf, 1)

    # ------------------------------------------------------------------
    # V26C — keep the useful tail of the runtime log.
    # V25 got so far that HeapAlloc spam filled the app's 500-line ring.
    # Suppress infrastructure noise but keep mapping/load/error/API milestones.
    # ------------------------------------------------------------------
    quiet_anchor = '''            "EnterCriticalSection", "LeaveCriticalSection",
            "TryEnterCriticalSection", // V25: cooperative-lock poll noise
            "QueryPerformanceCounter", "GetSystemTimePreciseAsFileTime",
'''
    quiet_new = '''            "EnterCriticalSection", "LeaveCriticalSection",
            "TryEnterCriticalSection", // V25: cooperative-lock poll noise
            "HeapAlloc", "HeapFree", "HeapSize",
            "GetLastError", "SetLastError",
            "QueryPerformanceCounter", "GetSystemTimePreciseAsFileTime",
'''
    if quiet_anchor not in s:
        raise SystemExit("ERROR: V26 quiet-list anchor changed")
    s = s.replace(quiet_anchor, quiet_new, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V26: Mono native/AOT loader probes now get real Windows failure semantics")
else:
    print("V26: Mono loader semantics already patched")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "MONO LOAD V26 EXPECTED MISS:",
    "CreateFileW V26 empty path -> INVALID_HANDLE_VALUE",
    '"HeapAlloc", "HeapFree", "HeapSize"',
):
    if token not in final:
        raise SystemExit("ERROR: V26 verification failed: " + token)

# The old arbitrary fake-handle fallback in the wide loader must be gone.
wide_start = final.find('strcmp(fn, "LoadLibraryExW") == 0')
wide_end = final.find('strcmp(fn, "GetModuleHandleA") == 0', wide_start)
wide = final[wide_start:wide_end]
if "ret_val = base ? base" in wide:
    raise SystemExit("ERROR: old wide LoadLibrary fake-handle fallback survived")

print("MXXHUB_MONO_LOADER_SEMANTICS_V26_OK")
