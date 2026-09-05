#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_UNITY_ANSI_DLL_PATH_FIX_V8"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v8.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
files_p = wg / "Sources/Win32/wg_win32_files.c"

for p in (engine_p, files_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

# ---------------------------------------------------------------------------
# V8A — expose the selected game's real directory as C:\\MxxGame.
# ---------------------------------------------------------------------------
f = files_p.read_text(encoding="utf-8")

if MARKER not in f:
    old_fallback = '            snprintf(win, sizeof(win), "C:\\\\a.exe");'
    new_fallback = '''            /* MXXHUB_UNITY_ANSI_DLL_PATH_FIX_V8
             * The selected game lives beside its *_Data folder and native DLLs,
             * not in Bottle/drive_c. Expose that real directory as C:\\\\MxxGame.
             */
            snprintf(win, sizeof(win), "C:\\\\MxxGame\\\\%s", fname);'''
    if old_fallback not in f:
        raise SystemExit("ERROR: wg_files_exe_win_path fallback changed before V8")
    f = f.replace(old_fallback, new_fallback, 1)

    src_anchor = '    const char *src = buf;\n'
    if src_anchor not in f:
        raise SystemExit("ERROR: wg_files_map_path src anchor changed before V8")

    game_map = r'''    /* MXXHUB_UNITY_ANSI_DLL_PATH_FIX_V8
     * C:\MxxGame is a synthetic Windows view of the actual selected EXE folder.
     */
    static char mxx_game_mapped[1024];
    if (strncasecmp(src, "C:\\MxxGame\\", 11) == 0 ||
        strncasecmp(src, "C:/MxxGame/", 11) == 0) {
        const char *rel = src + 11;
        snprintf(mxx_game_mapped, sizeof(mxx_game_mapped), "%s%s",
                 s_exe_dir, rel);
        fix_separators(mxx_game_mapped);
        return mxx_game_mapped;
    }

'''
    f = f.replace(src_anchor, src_anchor + game_map, 1)

    rel_anchor = '''    bool is_abs = (src[0] && src[1] == ':') || src[0] == '\\\\' || src[0] == '/';
'''
    if rel_anchor not in f:
        raise SystemExit("ERROR: wg_files_map_path relative-path anchor changed before V8")

    rel_extra = r'''    if (!is_abs && !s_cwd_win[0] && s_exe_dir[0]) {
        snprintf(mxx_game_mapped, sizeof(mxx_game_mapped), "%s%s",
                 s_exe_dir, src);
        fix_separators(mxx_game_mapped);
        if (access(mxx_game_mapped, F_OK) == 0) {
            return mxx_game_mapped;
        }
    }
'''
    f = f.replace(rel_anchor, rel_anchor + rel_extra, 1)

    files_p.write_text(f, encoding="utf-8")
    print("V8: mapped selected game directory as C:\\\\MxxGame")
else:
    print("V8: game-path mapping already present")

# ---------------------------------------------------------------------------
# V8B — implement the ANSI APIs Unity is actually using at the exit point.
# ---------------------------------------------------------------------------
e = engine_p.read_text(encoding="utf-8")

if MARKER not in e:
    load_anchor = '''        } else if (strcmp(fn, "LoadLibraryExW") == 0 ||
                   strcmp(fn, "LoadLibraryW") == 0) {
'''
    if load_anchor not in e:
        raise SystemExit("ERROR: LoadLibraryW dispatch anchor changed before V8")

    ansi_loader = r'''        } else if (strcmp(fn, "LoadLibraryA") == 0 ||
                   strcmp(fn, "LoadLibraryExA") == 0) {
            /* MXXHUB_UNITY_ANSI_DLL_PATH_FIX_V8
             * Unity's Win64 player uses ANSI dynamic loading during late startup.
             */
            char ascii[1024] = {0};
            if (args[0]) {
                wg_blink_read_mem(engine->blink, args[0], ascii,
                                  sizeof(ascii) - 1);
                ascii[sizeof(ascii) - 1] = 0;
            }

            WG_LOGI(TAG,
                    "%s V8 request='%s' guest_ptr=0x%llX flags=0x%llX",
                    fn, ascii,
                    (unsigned long long)args[0],
                    (unsigned long long)(strcmp(fn, "LoadLibraryExA") == 0
                                             ? args[2] : 0));

            if (!args[0] || !ascii[0]) {
                ret_val = engine->pe_image
                              ? (uint32_t)engine->pe_image->image_base
                              : 0x400000;
                s_last_error = 0;
                WG_LOGI(TAG, "%s V8 -> main module 0x%llX",
                        fn, (unsigned long long)ret_val);
            } else {
                uint32_t already = wg_module_find(ascii);
                if (already) {
                    ret_val = already;
                    s_last_error = 0;
                    WG_LOGI(TAG, "%s V8 '%s' -> already loaded 0x%X",
                            fn, ascii, already);
                } else {
                    char mapbuf[1024];
                    strncpy(mapbuf, ascii, sizeof(mapbuf) - 1);
                    mapbuf[sizeof(mapbuf) - 1] = 0;

                    const char *real = wg_files_map_path(
                        (uint32_t)args[0], engine->blink,
                        mapbuf, sizeof(mapbuf));

                    bool disk_exists = real && access(real, R_OK) == 0;
                    uint32_t loaded = 0;

                    if (disk_exists) {
                        WG_LOGI(TAG, "%s V8 mapped '%s' -> '%s'",
                                fn, ascii, real);
                        loaded = wg_load_dll(engine->blink,
                                             engine->dll_mapper,
                                             real, ascii);
                    }

                    if (loaded) {
                        ret_val = loaded;
                        s_last_error = 0;
                        WG_LOGI(TAG, "%s V8 real DLL '%s' -> 0x%X",
                                fn, ascii, loaded);
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
                                      (uint32_t)(engine->dll_mapper->count *
                                                 0x1000u);
                            s_last_error = 0;
                            WG_LOGI(TAG,
                                    "%s V8 emulated system DLL '%s' -> 0x%llX "
                                    "(disk='%s')",
                                    fn, ascii,
                                    (unsigned long long)ret_val,
                                    real ? real : "<none>");
                        } else {
                            ret_val = 0;
                            s_last_error = disk_exists
                                               ? 1114
                                               : 126;
                            WG_LOGE(TAG,
                                    "%s V8 FAILED '%s': disk=%d real='%s' "
                                    "last_error=0x%X",
                                    fn, ascii, disk_exists ? 1 : 0,
                                    real ? real : "<none>", s_last_error);
                        }
                    }
                }
            }
'''
    e = e.replace(load_anchor, ansi_loader + load_anchor, 1)

    attr_anchor = '''        } else if (strcmp(fn, "GetFileAttributesW") == 0) {
'''
    if attr_anchor not in e:
        raise SystemExit("ERROR: GetFileAttributesW dispatch anchor changed before V8")

    ansi_attr = r'''        } else if (strcmp(fn, "GetFileAttributesA") == 0) {
            char apath[1024] = {0};
            if (args[0]) {
                wg_blink_read_mem(engine->blink, args[0],
                                  apath, sizeof(apath) - 1);
                apath[sizeof(apath) - 1] = 0;
            }

            char mapbuf[1024];
            strncpy(mapbuf, apath, sizeof(mapbuf) - 1);
            mapbuf[sizeof(mapbuf) - 1] = 0;
            const char *real = wg_files_map_path(
                (uint32_t)args[0], engine->blink,
                mapbuf, sizeof(mapbuf));

            struct stat st;
            if (real && stat(real, &st) == 0) {
                ret_val = S_ISDIR(st.st_mode) ? 0x10 : 0x80;
                s_last_error = 0;
            } else {
                ret_val = 0xFFFFFFFFu;
                s_last_error = 2;
            }

            WG_LOGI(TAG,
                    "GetFileAttributesA V8('%s') -> 0x%llX real='%s' err=0x%X",
                    apath, (unsigned long long)ret_val,
                    real ? real : "<none>", s_last_error);
'''
    e = e.replace(attr_anchor, ansi_attr + attr_anchor, 1)

    module_tail = '''            ret_val = len;
        } else if (strcmp(fn, "GetModuleFileNameW") == 0) {
'''
    module_new = '''            ret_val = len;
            WG_LOGI(TAG, "GetModuleFileNameA V8 -> '%s' (len=%d)", path, len);
        } else if (strcmp(fn, "GetModuleFileNameW") == 0) {
'''
    if module_tail not in e:
        raise SystemExit("ERROR: GetModuleFileNameA tail changed before V8")
    e = e.replace(module_tail, module_new, 1)

    cwd_tail = '''            ret_val = dirlen;
        } else if (strcmp(fn, "GetFullPathNameW") == 0) {
'''
    cwd_new = '''            ret_val = dirlen;
            WG_LOGI(TAG, "GetCurrentDirectory%s V8 -> '%s' (len=%d)",
                    wide ? "W" : "A", dir, dirlen);
        } else if (strcmp(fn, "GetFullPathNameW") == 0) {
'''
    if cwd_tail not in e:
        raise SystemExit("ERROR: GetCurrentDirectory tail changed before V8")
    e = e.replace(cwd_tail, cwd_new, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V8: implemented LoadLibraryA/ExA + GetFileAttributesA diagnostics")
else:
    print("V8: engine ANSI DLL/path patch already present")

fv = files_p.read_text(encoding="utf-8")
ev = engine_p.read_text(encoding="utf-8")

for token in (
    MARKER,
    'C:\\\\MxxGame\\\\%s',
    'strncasecmp(src, "C:\\\\MxxGame\\\\", 11)',
    'access(mxx_game_mapped, F_OK)',
):
    if token not in fv:
        raise SystemExit("ERROR: V8 file-path verification failed: " + token)

for token in (
    MARKER,
    'strcmp(fn, "LoadLibraryA")',
    'strcmp(fn, "LoadLibraryExA")',
    "V8 request='%s'",
    'strcmp(fn, "GetFileAttributesA")',
    "GetFileAttributesA V8",
    "GetModuleFileNameA V8",
    "GetCurrentDirectory%s V8",
):
    if token not in ev:
        raise SystemExit("ERROR: V8 engine verification failed: " + token)

print("MXXHUB_UNITY_ANSI_DLL_PATH_FIX_V8_OK")
