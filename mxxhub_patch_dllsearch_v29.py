#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_dllsearch_v29.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_DLL_SEARCH_AND_FAULT_DIAG_V29"

if MARKER not in s:
    state_anchor = "static char s_mxx_exe_dir[1024] = {0};\n"
    if state_anchor not in s:
        raise SystemExit("ERROR: V29 exe-dir state anchor changed")

    state = r'''static char s_mxx_exe_dir[1024] = {0};
/* MXXHUB_DLL_SEARCH_AND_FAULT_DIAG_V29 */
static char s_mxx_dll_dir_native[1024] = {0};
static char s_mxx_dll_dir_win[1024] = {0};

static bool mxx_find_casefold_file(const char *dir, const char *name,
                                   char *out, size_t outsz) {
    if (!dir || !dir[0] || !name || !name[0] || !out || outsz < 4) return false;

    char exact[1400];
    snprintf(exact, sizeof(exact), "%s/%s", dir, name);
    if (access(exact, R_OK) == 0) {
        snprintf(out, outsz, "%s", exact);
        return true;
    }

    DIR *d = opendir(dir);
    if (!d) return false;

    struct dirent *de;
    bool found = false;
    while ((de = readdir(d)) != NULL) {
        if (strcasecmp(de->d_name, name) == 0) {
            snprintf(out, outsz, "%s/%s", dir, de->d_name);
            found = access(out, R_OK) == 0;
            break;
        }
    }
    closedir(d);
    return found;
}

static bool mxx_name_has_extension(const char *name) {
    if (!name) return false;
    const char *base = name;
    for (const char *p = name; *p; p++)
        if (*p == '/' || *p == '\\') base = p + 1;
    const char *dot = strrchr(base, '.');
    return dot && dot[1] != '\0';
}
'''
    s = s.replace(state_anchor, state, 1)

    load_pe_anchor = '''    if (mxx_slash) *mxx_slash = 0;'''
    load_pe_new = '''    if (mxx_slash) *mxx_slash = 0;
    s_mxx_dll_dir_native[0] = 0;
    s_mxx_dll_dir_win[0] = 0;'''
    if load_pe_anchor not in s:
        raise SystemExit("ERROR: V29 load-PE reset anchor changed")
    s = s.replace(load_pe_anchor, load_pe_new, 1)

    start = s.find('        } else if (strcmp(fn, "LoadLibraryExW") == 0 ||\n'
                   '                   strcmp(fn, "LoadLibraryW") == 0) {')
    end = s.find('        } else if (strcmp(fn, "GetModuleHandleA") == 0) {', start)
    if start < 0 or end < 0:
        raise SystemExit("ERROR: V29 V26 wide-loader block changed")

    loader = r'''        } else if (strcmp(fn, "SetDllDirectoryW") == 0) {
            if (!args[0]) {
                s_mxx_dll_dir_native[0] = 0;
                s_mxx_dll_dir_win[0] = 0;
                ret_val = 1;
                s_last_error = 0;
                WG_LOGI(TAG, "DLL SEARCH V29: SetDllDirectoryW(NULL) -> reset");
            } else {
                uint16_t wdir[512] = {0};
                char adir[1024] = {0};
                wg_blink_read_mem(engine->blink, args[0], wdir, 1022);
                for (int i = 0; i < 511 && wdir[i]; i++)
                    adir[i] = wdir[i] < 128 ? (char)wdir[i] : '?';

                char mapbuf[1024];
                snprintf(mapbuf, sizeof(mapbuf), "%s", adir);
                const char *real = wg_files_map_path(
                    (uint32_t)args[0], engine->blink, mapbuf, sizeof(mapbuf));

                struct stat st;
                if (real && stat(real, &st) == 0 && S_ISDIR(st.st_mode)) {
                    snprintf(s_mxx_dll_dir_native,
                             sizeof(s_mxx_dll_dir_native), "%s", real);
                    snprintf(s_mxx_dll_dir_win,
                             sizeof(s_mxx_dll_dir_win), "%s", adir);
                    ret_val = 1;
                    s_last_error = 0;
                    WG_LOGI(TAG,
                            "DLL SEARCH V29: SetDllDirectoryW('%s') -> '%s'",
                            adir, s_mxx_dll_dir_native);
                } else {
                    s_mxx_dll_dir_native[0] = 0;
                    s_mxx_dll_dir_win[0] = 0;
                    ret_val = 0;
                    s_last_error = 3;
                    WG_LOGW(TAG,
                            "DLL SEARCH V29: SetDllDirectoryW('%s') missing",
                            adir);
                }
            }

        } else if (strcmp(fn, "LoadLibraryExW") == 0 ||
                   strcmp(fn, "LoadLibraryW") == 0) {
            uint16_t libname[512] = {0};
            char ascii[512] = {0};
            if (args[0]) {
                wg_blink_read_mem(engine->blink, args[0], libname, 1022);
                for (int i = 0; i < 511 && libname[i]; i++)
                    ascii[i] = libname[i] < 128 ? (char)libname[i] : '?';
            }

            WG_LOGI(TAG, "%s V29('%s')", fn, ascii);

            if (!ascii[0] || !args[0]) {
                ret_val = engine->pe_image
                    ? (uint32_t)engine->pe_image->image_base : 0x400000;
                s_last_error = 0;
            } else {
                char load_name[600];
                snprintf(load_name, sizeof(load_name), "%s", ascii);

                /* MXXHUB_WINDOWS_V31_DLL_NORMALIZE */
                size_t load_len = strlen(load_name);
                if (load_len >= 8 &&
                    strcasecmp(load_name + load_len - 8, ".dll.dll") == 0) {
                    load_name[load_len - 4] = '\0';
                    WG_LOGI(TAG,
                            "DLL V31 NORMALIZE: '%s' -> '%s'",
                            ascii, load_name);
                }

                size_t ln = strlen(load_name);
                if (!mxx_name_has_extension(load_name) &&
                    ln > 0 && load_name[ln - 1] != '.') {
                    strncat(load_name, ".dll",
                            sizeof(load_name) - strlen(load_name) - 1);
                }

                uint32_t already = wg_module_find(load_name);
                if (!already) already = wg_module_find(ascii);

                if (already) {
                    ret_val = already;
                    s_last_error = 0;
                } else {
                    char real_candidate[1400] = {0};
                    bool disk_exists = false;

                    bool bare_name =
                        strchr(load_name, '\\') == NULL &&
                        strchr(load_name, '/') == NULL &&
                        !(load_name[0] && load_name[1] == ':');

                    if (bare_name && s_mxx_dll_dir_native[0]) {
                        disk_exists = mxx_find_casefold_file(
                            s_mxx_dll_dir_native, load_name,
                            real_candidate, sizeof(real_candidate));
                        if (disk_exists)
                            WG_LOGI(TAG, "DLL SEARCH V29 HIT set-dir: %s",
                                    real_candidate);
                    }

                    if (!disk_exists && bare_name && s_mxx_exe_dir[0]) {
                        disk_exists = mxx_find_casefold_file(
                            s_mxx_exe_dir, load_name,
                            real_candidate, sizeof(real_candidate));
                        if (disk_exists)
                            WG_LOGI(TAG, "DLL SEARCH V29 HIT exe-dir: %s",
                                    real_candidate);
                    }

                    if (!disk_exists) {
                        char mapbuf[600];
                        snprintf(mapbuf, sizeof(mapbuf), "%s", load_name);
                        const char *real = wg_files_map_path(
                            (uint32_t)args[0], engine->blink,
                            mapbuf, sizeof(mapbuf));
                        if (real && access(real, R_OK) == 0) {
                            snprintf(real_candidate,
                                     sizeof(real_candidate), "%s", real);
                            disk_exists = true;
                            WG_LOGI(TAG, "DLL SEARCH V29 HIT mapped: %s",
                                    real_candidate);
                        }
                    }

                    uint32_t base = 0;
                    if (disk_exists) {
                        base = wg_load_dll(
                            engine->blink, engine->dll_mapper,
                            real_candidate, load_name);
                    }

                    if (base) {
                        ret_val = base;
                        s_last_error = 0;
                        WG_LOGI(TAG,
                                "DLL SEARCH V29 LOADED: '%s' -> 0x%X",
                                load_name, base);
                    } else {
                        bool systemish =
                            strcasestr(load_name, "api-ms-win-") ||
                            strcasestr(load_name, "ext-ms-win-") ||
                            strcasestr(load_name, "kernel32") ||
                            strcasestr(load_name, "kernelbase") ||
                            strcasestr(load_name, "ntdll") ||
                            strcasestr(load_name, "user32") ||
                            strcasestr(load_name, "gdi32") ||
                            strcasestr(load_name, "advapi32") ||
                            strcasestr(load_name, "shell32") ||
                            strcasestr(load_name, "ole32") ||
                            strcasestr(load_name, "oleaut32") ||
                            strcasestr(load_name, "combase") ||
                            strcasestr(load_name, "bcrypt") ||
                            strcasestr(load_name, "crypt32") ||
                            strcasestr(load_name, "wintrust") ||
                            strcasestr(load_name, "version.dll") ||
                            strcasestr(load_name, "winmm") ||
                            strcasestr(load_name, "imm32") ||
                            strcasestr(load_name, "shlwapi") ||
                            strcasestr(load_name, "setupapi") ||
                            strcasestr(load_name, "cfgmgr32") ||
                            strcasestr(load_name, "powrprof") ||
                            strcasestr(load_name, "dbghelp") ||
                            strcasestr(load_name, "d3d") ||
                            strcasestr(load_name, "dxgi") ||
                            strcasestr(load_name, "xinput") ||
                            strcasestr(load_name, "ws2_32") ||
                            strcasestr(load_name, "winhttp");

                        if (systemish) {
                            ret_val = 0x10000000u +
                                (uint32_t)(engine->dll_mapper->count * 0x1000u);
                            s_last_error = 0;
                            WG_LOGI(TAG,
                                    "%s V29 emulated system DLL '%s' -> 0x%llX",
                                    fn, load_name,
                                    (unsigned long long)ret_val);
                        } else {
                            ret_val = 0;
                            s_last_error = disk_exists ? 1114 : 126;
                            WG_LOGI(TAG,
                                    "DLL SEARCH V29 MISS: '%s' -> NULL err=%u",
                                    load_name, s_last_error);
                        }
                    }
                }
            }
'''
    s = s[:start] + loader + s[end:]

    crash_old = r'''                    WG_LOGE(TAG, "Crash at RIP=0x%llx (SIGSEGV — bad pointer or unmapped memory)",
                            (unsigned long long)halt_rip);
'''
    crash_new = r'''                    int v29_stop = wg_blink_get_stop_reason(engine->blink);
                    uint64_t v29_fault = wg_blink_get_fault_addr(engine->blink);
                    const char *v29_mod = NULL;
                    uint64_t v29_mod_off = 0;
                    for (int mi = 0; mi < 16; mi++) {
                        if (s_modules[mi].in_use &&
                            halt_rip >= s_modules[mi].base &&
                            halt_rip < (uint64_t)s_modules[mi].base +
                                       s_modules[mi].size) {
                            v29_mod = s_modules[mi].name;
                            v29_mod_off = halt_rip - s_modules[mi].base;
                            break;
                        }
                    }

                    /* MXXHUB_WINDOWS_V31_UNITY_FAULT_STEP
                     *
                     * Repeatable HK fault:
                     *   unityplayer.dll+0x118DC9F
                     *   0F B6 45 B6 = MOVZX EAX, byte ptr [RBP-0x4A]
                     *
                     * Emulate only this exact instruction if both opcode and
                     * stack source validate, then resume at RIP+4.
                     */
                    bool v31_fault_stepped = false;
                    if (v29_mod &&
                        strcasecmp(v29_mod, "unityplayer.dll") == 0 &&
                        v29_mod_off == 0x118DC9FULL) {
                        uint8_t v31_code[4] = {0};
                        uint64_t v31_rbp = wg_blink_get_reg(engine->blink, 5);
                        uint8_t v31_value = 0;

                        bool v31_code_ok =
                            wg_blink_read_mem(engine->blink, halt_rip,
                                              v31_code, sizeof(v31_code));
                        bool v31_src_ok =
                            v31_rbp >= 0x4A &&
                            wg_blink_read_mem(engine->blink,
                                              v31_rbp - 0x4A,
                                              &v31_value, 1);

                        if (v31_code_ok && v31_src_ok &&
                            v31_code[0] == 0x0F &&
                            v31_code[1] == 0xB6 &&
                            v31_code[2] == 0x45 &&
                            v31_code[3] == 0xB6) {
                            wg_blink_set_reg(engine->blink, 0,
                                             (uint64_t)v31_value);
                            wg_blink_set_rip(engine->blink, halt_rip + 4);
                            WG_LOGW(TAG,
                                "UNITY V31 FAULT STEP: "
                                "unityplayer.dll+0x118DC9F "
                                "MOVZX EAX,[RBP-0x4A] "
                                "src=0x%llX value=0x%02X -> RIP=0x%llX",
                                (unsigned long long)(v31_rbp - 0x4A),
                                (unsigned)v31_value,
                                (unsigned long long)(halt_rip + 4));
                            v31_fault_stepped = true;
                        }
                    }

                    if (v31_fault_stepped) {
                        break;
                    }

                    WG_LOGE(TAG,
                            "FAULT V29: stop=%d fault=0x%llX RIP=0x%llX module=%s+0x%llX",
                            v29_stop,
                            (unsigned long long)v29_fault,
                            (unsigned long long)halt_rip,
                            v29_mod ? v29_mod : "(unknown)",
                            (unsigned long long)v29_mod_off);
                    WG_LOGE(TAG, "Crash at RIP=0x%llx (SIGSEGV — bad pointer or unmapped memory)",
                            (unsigned long long)halt_rip);
'''
    if crash_old not in s:
        raise SystemExit("ERROR: V29 crash-log anchor changed")
    s = s.replace(crash_old, crash_new, 1)

    quiet_old = '''            "HeapAlloc", "HeapFree", "HeapSize",
            "GetLastError", "SetLastError",
'''
    quiet_new = '''            "HeapAlloc", "HeapFree", "HeapSize", "HeapReAlloc",
            "GetLastError", "SetLastError",
            "GetCurrentThreadId", "TlsGetValue", "FlsGetValue",
'''
    if quiet_old not in s:
        raise SystemExit("ERROR: V29 quiet-list anchor changed")
    s = s.replace(quiet_old, quiet_new, 1)

    old_v28_log = '''            if ((v28_same_srw_count & 255ULL) == 0 || switched) {
                WG_LOGI(TAG,
                        "SRW V28 FAIR YIELD: lock=0x%X same=%llu total=%llu switched=%d",
'''
    new_v28_log = '''            if (v28_same_srw_count <= 64 ||
                (v28_same_srw_count & 255ULL) == 0) {
                WG_LOGI(TAG,
                        "SRW V28 FAIR YIELD: lock=0x%X same=%llu total=%llu switched=%d",
'''
    if old_v28_log not in s:
        raise SystemExit("ERROR: V29 V28 log-rate anchor changed")
    s = s.replace(old_v28_log, new_v28_log, 1)

    ring_anchor = '''    if (name[0] == 'G' && name[3] == 'L') return; // GetLastError
'''
    ring_new = '''    if (strcmp(name, "GetCurrentThreadId") == 0 ||
        strcmp(name, "TlsGetValue") == 0 ||
        strcmp(name, "FlsGetValue") == 0 ||
        strcmp(name, "HeapReAlloc") == 0)
        return; // V29: high-volume runtime infrastructure
    if (name[0] == 'G' && name[3] == 'L') return; // GetLastError
'''
    if ring_anchor not in s:
        raise SystemExit("ERROR: V29 call-ring anchor changed")
    s = s.replace(ring_anchor, ring_new, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V29: Windows DLL-search semantics + exact fault diagnostics installed")
else:
    print("V29: DLL search/fault diagnostics already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "DLL SEARCH V29: SetDllDirectoryW",
    "DLL SEARCH V29 HIT set-dir:",
    "DLL SEARCH V29 LOADED:",
    "DLL SEARCH V29 MISS:",
    "FAULT V29: stop=%d fault=0x%llX",
    'strcmp(name, "HeapReAlloc") == 0',
    "MXXHUB_WINDOWS_V31_DLL_NORMALIZE",
    "DLL V31 NORMALIZE:",
    "MXXHUB_WINDOWS_V31_UNITY_FAULT_STEP",
    "UNITY V31 FAULT STEP:",
):
    if token not in final:
        raise SystemExit("ERROR: V29 verification failed: " + token)

print("MXXHUB_DLL_SEARCH_AND_FAULT_DIAG_V29_OK")
