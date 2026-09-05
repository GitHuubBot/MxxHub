#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_real_module_gpa_v40.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
engine_p = wg / 'Sources/Core/wg_engine.c'
if not engine_p.is_file():
    raise SystemExit(f'ERROR: missing {engine_p}')

s = engine_p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V40_REAL_MODULE_GPA_SEMANTICS'

if MARKER not in s:
    # V40A: V31's generic .dll.dll normalization accidentally converted Mono's
    # managed-assembly AOT probe (.../Managed/mscorlib.dll.dll) into the real
    # managed mscorlib.dll and then loaded that PE as if it were a native DLL.
    # Preserve the doubled suffix for Managed/ probes so normal file lookup
    # misses and LoadLibrary returns NULL, matching Windows.
    old_norm = r'''                /* MXXHUB_WINDOWS_V31_DLL_NORMALIZE */
                size_t load_len = strlen(load_name);
                if (load_len >= 8 &&
                    strcasecmp(load_name + load_len - 8, ".dll.dll") == 0) {
                    load_name[load_len - 4] = '\0';
                    WG_LOGI(TAG,
                            "DLL V31 NORMALIZE: '%s' -> '%s'",
                            ascii, load_name);
                }
'''
    new_norm = r'''                /* MXXHUB_WINDOWS_V31_DLL_NORMALIZE */
                /* MXXHUB_WINDOWS_V40_REAL_MODULE_GPA_SEMANTICS
                 * Mono appends a native-library suffix while probing managed
                 * assemblies for optional AOT companions. A path such as
                 *   ...\\Managed\\mscorlib.dll.dll
                 * is intentionally a miss on Windows. Do not normalize that
                 * probe back to the managed mscorlib.dll. */
                size_t load_len = strlen(load_name);
                bool v40_managed_native_probe =
                    strcasestr(load_name, "\\Managed\\") != NULL ||
                    strcasestr(load_name, "/Managed/") != NULL;
                if (load_len >= 8 &&
                    strcasecmp(load_name + load_len - 8, ".dll.dll") == 0) {
                    if (v40_managed_native_probe) {
                        WG_LOGI(TAG,
                                "GPA V40 MANAGED-PROBE PRESERVE: '%s' -> expected LoadLibrary miss",
                                load_name);
                    } else {
                        load_name[load_len - 4] = '\0';
                        WG_LOGI(TAG,
                                "DLL V31 NORMALIZE: '%s' -> '%s'",
                                ascii, load_name);
                    }
                }
'''
    if old_norm not in s:
        raise SystemExit('ERROR: V40 V31 normalization anchor changed')
    s = s.replace(old_norm, new_norm, 1)

    # V40B: GetProcAddress must not fabricate a system thunk when a real PE
    # module (or the main EXE) simply does not export the requested symbol.
    # This was observed directly with mono_profiler_startup on hollow_knight.exe
    # and mono_aot_* on mscorlib.dll. Windows returns NULL +
    # ERROR_PROC_NOT_FOUND (127) for these optional probes.
    gpa_start = s.find('        } else if (strcmp(fn, "GetProcAddress") == 0) {')
    if gpa_start < 0:
        raise SystemExit('ERROR: V40 GetProcAddress block not found')
    gpa_end = s.find('        gpa_done: ;', gpa_start)
    if gpa_end < 0:
        raise SystemExit('ERROR: V40 GetProcAddress end not found')

    gpa = s[gpa_start:gpa_end]
    exp_start = gpa.find('                uint32_t exp = wg_module_export(hmod, func_name);\n')
    ordinal_marker = '            } else {\n                // Ordinal import — map known ordinals\n'
    exp_end = gpa.find(ordinal_marker, exp_start)
    if exp_start < 0 or exp_end < 0:
        raise SystemExit('ERROR: V40 GetProcAddress export/fallback anchor changed')

    replacement = r'''                uint32_t exp = wg_module_export(hmod, func_name);
                if (exp) {
                    ret_val = exp;
                    static uint32_t s_v40_real_export_logs = 0;
                    if (s_v40_real_export_logs < 24) {
                        WG_LOGI(TAG, "GetProcAddress(%s) -> 0x%X (module export)",
                                func_name, exp);
                    } else if (s_v40_real_export_logs == 24) {
                        WG_LOGI(TAG,
                                "GPA V40: suppressing further successful real-module export logs");
                    }
                    s_v40_real_export_logs++;
                } else {
                    uint32_t v40_main_base = engine->pe_image
                        ? (uint32_t)engine->pe_image->image_base : 0;
                    bool v40_real_main = v40_main_base && hmod == v40_main_base;
                    int v40_real_slot = -1;
                    for (int v40i = 0; v40i < 16; ++v40i) {
                        if (s_modules[v40i].in_use && s_modules[v40i].base == hmod) {
                            v40_real_slot = v40i;
                            break;
                        }
                    }

                    /* nsProcess/nsExec are deliberate compatibility shims: their
                     * real exports are routed into native MxxHub emulation. Keep
                     * that legacy fallback, but make every other real-module miss
                     * obey Windows GetProcAddress semantics. */
                    bool v40_plugin_emulation = false;
                    if (v40_real_slot >= 0) {
                        const char *v40n = s_modules[v40_real_slot].name;
                        v40_plugin_emulation =
                            strcmp(v40n, "nsprocess.dll") == 0 ||
                            strcmp(v40n, "nsexec.dll") == 0;
                    }

                    if ((v40_real_main || v40_real_slot >= 0) &&
                        !v40_plugin_emulation) {
                        ret_val = 0;
                        s_last_error = 127; /* ERROR_PROC_NOT_FOUND */
                        WG_LOGI(TAG,
                                "GPA V40 REAL-MODULE MISS: h=0x%X fn='%s' module=%s -> NULL err=127",
                                hmod, func_name,
                                v40_real_main ? "<main-exe>" :
                                s_modules[v40_real_slot].name);
                    } else {
                        // Emulated/fake system handles still resolve through the
                        // mapper. This is where kernel32/user32/etc live in MxxHub.
                        ret_val = wg_dll_mapper_find_any(engine->dll_mapper, func_name);
                        if (!ret_val) {
                            ret_val = wg_dll_mapper_resolve(engine->dll_mapper, dll, func_name);
                        }
                        if (ret_val >= 0xC00000ULL && ret_val < 0xC00000ULL + 0x20000) {
                            uint8_t hlt = 0xF4;
                            wg_blink_write_mem(engine->blink, ret_val, &hlt, 1);
                        }
                        WG_LOGI(TAG, "GetProcAddress(%s) -> 0x%llx",
                                func_name, (unsigned long long)ret_val);
                    }
                }
'''
    gpa = gpa[:exp_start] + replacement + gpa[exp_end:]
    s = s[:gpa_start] + gpa + s[gpa_end:]

    # V40C: the app-side snapshot is capped; generic GetProcAddress tracing and
    # hundreds of successful Mono export lines used most of it before the real
    # fault. Keep semantic/miss logs above, but suppress the generic API line and
    # call-ring noise for GetProcAddress.
    quiet_old = '            "QueryPerformanceFrequency", "TlsSetValue", "GetEnvironmentVariableW",\n'
    quiet_new = '            "QueryPerformanceFrequency", "TlsSetValue", "GetEnvironmentVariableW", "GetProcAddress",\n'
    if quiet_new not in s:
        if quiet_old not in s:
            raise SystemExit('ERROR: V40 quiet-list anchor changed')
        s = s.replace(quiet_old, quiet_new, 1)

    ring_anchor = '        return; // V39: high-rate Mono startup noise\n'
    ring_new = ring_anchor + '''    if (strcmp(name, "GetProcAddress") == 0)\n        return; // V40: preserve crash-ring space for post-loader calls\n'''
    if 'return; // V40: preserve crash-ring space for post-loader calls' not in s:
        if ring_anchor not in s:
            raise SystemExit('ERROR: V40 call-ring anchor changed')
        s = s.replace(ring_anchor, ring_new, 1)

    engine_p.write_text(s, encoding='utf-8')
    print('V40: Managed/*.dll.dll Mono AOT probes are no longer normalized to managed assemblies')
    print('V40: missing exports on real PE modules/main EXE now return NULL + ERROR_PROC_NOT_FOUND')
    print('V40: NSIS nsProcess/nsExec compatibility fallback preserved')
    print('V40: high-volume GetProcAddress tracing reduced so crash snapshots keep the useful tail')
else:
    print('V40: real-module GetProcAddress semantics already patched')

final = engine_p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'GPA V40 MANAGED-PROBE PRESERVE:',
    'GPA V40 REAL-MODULE MISS:',
    'ERROR_PROC_NOT_FOUND',
    'v40_plugin_emulation',
    's_v40_real_export_logs',
    'return; // V40: preserve crash-ring space for post-loader calls',
):
    if token not in final:
        raise SystemExit('ERROR: V40 verification failed: ' + token)

print('MXXHUB_WINDOWS_V40_REAL_MODULE_GPA_SEMANTICS_OK')
