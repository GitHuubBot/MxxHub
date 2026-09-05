#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_shcore_dialogdiag_v42.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file():
    raise SystemExit(f'ERROR: missing {p}')
s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V42_SHCORE_DPI_AND_DIALOG_DIAG'

if MARKER not in s:
    # Unity dynamically loads SHCore on supported Windows. V8/V29 knew many
    # Windows system DLLs but omitted SHCore, so the V41 device run returned
    # ERROR_MOD_NOT_FOUND immediately before display/window bootstrap.
    a_old = '                            strcasestr(ascii, "shlwapi") ||\n                            strcasestr(ascii, "setupapi") ||'
    a_new = '                            strcasestr(ascii, "shlwapi") ||\n                            strcasestr(ascii, "shcore") || /* V42 */\n                            strcasestr(ascii, "setupapi") ||'
    if a_old not in s:
        raise SystemExit('ERROR: V42 ANSI system-DLL anchor changed')
    s = s.replace(a_old, a_new, 1)

    w_old = '                            strcasestr(load_name, "shlwapi") ||\n                            strcasestr(load_name, "setupapi") ||'
    w_new = '                            strcasestr(load_name, "shlwapi") ||\n                            strcasestr(load_name, "shcore") || /* V42 */\n                            strcasestr(load_name, "setupapi") ||'
    if w_old not in s:
        raise SystemExit('ERROR: V42 wide system-DLL anchor changed')
    s = s.replace(w_old, w_new, 1)

    # Add a resource diagnostic helper. V41 correctly removed the fake Steam
    # UI, but that also hid Unity's real resource-105 error text. Read the
    # dialog template from the mapped hInstance and log its static strings.
    helper_anchor = 'static void wg_parse_dialog(WGEngine *engine, uint32_t hwnd, uint32_t dlg_id) {\n'
    if helper_anchor not in s:
        raise SystemExit('ERROR: V42 dialog helper anchor changed')
    helper = r'''/* MXXHUB_WINDOWS_V42_SHCORE_DPI_AND_DIALOG_DIAG
 * Log the real static title/control strings from an x64 DLL dialog resource.
 * This never creates a host/guest window and never pauses the engine.
 */
static uint32_t s_mxx_v42_dpi_awareness = 2; /* PROCESS_PER_MONITOR_DPI_AWARE */
static void mxx_v42_log_dialog_resource(WGEngine *engine, uint64_t hinst,
                                        uint32_t dlg_id) {
    WGPEImage *pe = NULL;
    const char *mod = "<unknown>";
    if (engine->pe_image &&
        (hinst == 0 || hinst == (uint64_t)engine->pe_image->image_base)) {
        pe = engine->pe_image;
        mod = "<main-exe>";
    } else {
        for (int i = 0; i < 16; i++) {
            if (s_modules[i].in_use && (uint64_t)s_modules[i].base == hinst) {
                pe = s_modules[i].img;
                mod = s_modules[i].name;
                break;
            }
        }
    }
    if (!pe) {
        WG_LOGI(TAG, "DIALOG V42 RESOURCE: id=%u hinst=0x%llX module=%s no PE image",
                dlg_id, (unsigned long long)hinst, mod);
        return;
    }
    const uint8_t *t = pe_find_dialog(pe, dlg_id);
    if (!t) {
        WG_LOGI(TAG, "DIALOG V42 RESOURCE: id=%u module=%s template not found",
                dlg_id, mod);
        return;
    }

    uint16_t dlgVer = 0, sig = 0;
    memcpy(&dlgVer, t, 2); memcpy(&sig, t + 2, 2);
    const uint8_t *q = t;
    uint32_t style = 0;
    uint16_t items = 0;
    bool ex = (dlgVer == 1 && sig == 0xFFFF);
    if (ex) {
        q = t + 12;
        memcpy(&style, q, 4); q += 4;
        memcpy(&items, q, 2); q += 2;
        q += 8; /* x,y,cx,cy */
    } else {
        memcpy(&style, q, 4); q += 8; /* style + exStyle */
        memcpy(&items, q, 2); q += 2;
        q += 8; /* x,y,cx,cy */
    }
    q = res_skip_sz(q, NULL, 0); /* menu */
    q = res_skip_sz(q, NULL, 0); /* class */
    uint16_t titlew[256] = {0};
    q = res_skip_sz(q, titlew, 256);
    char title[256] = {0};
    for (int k = 0; k < 255 && titlew[k]; k++)
        title[k] = titlew[k] < 128 ? (char)titlew[k] : '?';
    WG_LOGI(TAG,
            "DIALOG V42 RESOURCE: id=%u module=%s format=%s title='%s' items=%u style=0x%X",
            dlg_id, mod, ex ? "DLGTEMPLATEEX" : "DLGTEMPLATE",
            title, (unsigned)items, style);

    if (style & 0x40u) { /* DS_SETFONT */
        if (ex) q += 6; /* pointsize, weight, italic, charset */
        else q += 2;    /* pointsize */
        q = res_skip_sz(q, NULL, 0); /* typeface */
    }

    for (uint16_t n = 0; n < items && n < 64; n++) {
        size_t off = ((size_t)(q - t) + 3u) & ~(size_t)3u;
        q = t + off;
        uint32_t cid = 0;
        if (ex) {
            q += 12; /* helpID, exStyle, style */
            q += 8;  /* x,y,cx,cy */
            memcpy(&cid, q, 4); q += 4;
        } else {
            q += 8; /* style, exStyle */
            q += 8; /* x,y,cx,cy */
            uint16_t id16 = 0; memcpy(&id16, q, 2); q += 2;
            cid = id16;
        }
        q = res_skip_sz(q, NULL, 0); /* class */
        uint16_t textw[512] = {0};
        q = res_skip_sz(q, textw, 512);
        char text[512] = {0};
        for (int k = 0; k < 511 && textw[k]; k++)
            text[k] = textw[k] < 128 ? (char)textw[k] : '?';
        uint16_t extra = 0; memcpy(&extra, q, 2); q += 2;
        if (extra) q += extra;
        if (text[0])
            WG_LOGI(TAG, "DIALOG V42 TEXT: id=%u control=%u text='%s'",
                    dlg_id, cid, text);
    }
}

'''
    s = s.replace(helper_anchor, helper + helper_anchor, 1)

    # SHCore DPI APIs return HRESULT. Use a coherent 96-DPI, per-monitor-aware
    # virtual display and always initialize required out-pointers.
    dispatch_anchor = '} else if (strcmp(fn, "DialogBoxParamW") == 0 && !is_32bit) {\n'
    if dispatch_anchor not in s:
        raise SystemExit('ERROR: V42 V41 dialog dispatch anchor changed')
    dpi = r'''} else if (strcmp(fn, "SetProcessDpiAwareness") == 0) {
            /* SHCore.dll, Windows 8.1+: HRESULT SetProcessDpiAwareness(enum). */
            if (args[0] <= 2) {
                s_mxx_v42_dpi_awareness = (uint32_t)args[0];
                WG_LOGI(TAG, "SHCORE V42 SetProcessDpiAwareness(%llu) -> S_OK",
                        (unsigned long long)args[0]);
                s_last_error = 0;
                ret_val = 0; /* S_OK */
            } else {
                ret_val = 0x80070057u; /* E_INVALIDARG */
            }
        } else if (strcmp(fn, "GetProcessDpiAwareness") == 0) {
            /* HRESULT GetProcessDpiAwareness(HANDLE, PROCESS_DPI_AWARENESS*). */
            if (args[1]) {
                uint32_t awareness = s_mxx_v42_dpi_awareness;
                wg_blink_write_mem(engine->blink, args[1], &awareness, 4);
                ret_val = 0; /* S_OK */
                WG_LOGI(TAG, "SHCORE V42 GetProcessDpiAwareness -> %u", awareness);
            } else {
                ret_val = 0x80070057u; /* E_INVALIDARG */
            }
        } else if (strcmp(fn, "GetDpiForMonitor") == 0) {
            /* HRESULT GetDpiForMonitor(HMONITOR, type, UINT *x, UINT *y). */
            if (args[2] && args[3]) {
                uint32_t dpi96 = 96;
                wg_blink_write_mem(engine->blink, args[2], &dpi96, 4);
                wg_blink_write_mem(engine->blink, args[3], &dpi96, 4);
                ret_val = 0; /* S_OK */
                WG_LOGI(TAG, "SHCORE V42 GetDpiForMonitor monitor=0x%llX type=%llu -> 96x96",
                        (unsigned long long)args[0], (unsigned long long)args[1]);
            } else {
                ret_val = 0x80070057u;
            }
        } else if (strcmp(fn, "GetScaleFactorForMonitor") == 0) {
            /* DEVICE_SCALE_FACTOR SCALE_100_PERCENT == 100. */
            if (args[1]) {
                uint32_t scale = 100;
                wg_blink_write_mem(engine->blink, args[1], &scale, 4);
                ret_val = 0; /* S_OK */
                WG_LOGI(TAG, "SHCORE V42 GetScaleFactorForMonitor -> 100%%");
            } else {
                ret_val = 0x80070057u;
            }
        } else if (strcmp(fn, "DialogBoxParamW") == 0 && !is_32bit) {
'''
    s = s.replace(dispatch_anchor, dpi, 1)

    # Upgrade the V41 line: preserve bypass semantics but expose the actual
    # UnityPlayer resource strings before returning from the unsupported modal.
    old = '''            uint32_t dlg_id_v41 = (uint32_t)args[1];\n            WG_LOGI(TAG,\n                    "DIALOG V41 X64 BYPASS: id=%u hinst=0x%llX dlgproc=0x%llX -> IDOK; no Steam/NSIS window",\n                    dlg_id_v41,\n                    (unsigned long long)args[0],\n                    (unsigned long long)args[3]);\n            ret_val = 1; /* IDOK */\n'''
    new = '''            uint32_t dlg_id_v41 = (uint32_t)args[1];\n            mxx_v42_log_dialog_resource(engine, args[0], dlg_id_v41);\n            WG_LOGI(TAG,\n                    "DIALOG V42 X64 BYPASS: id=%u hinst=0x%llX dlgproc=0x%llX -> IDOK; diagnostic captured, no Steam/NSIS window",\n                    dlg_id_v41,\n                    (unsigned long long)args[0],\n                    (unsigned long long)args[3]);\n            ret_val = 1; /* IDOK */\n'''
    if old not in s:
        raise SystemExit('ERROR: V42 V41 bypass body anchor changed')
    s = s.replace(old, new, 1)

    p.write_text(s, encoding='utf-8')
    print('V42: SHCore recognized as a Windows system DLL for ANSI + wide loaders')
    print('V42: SHCore DPI APIs provide initialized 96-DPI outputs')
    print('V42: Win64 Unity dialog resources are decoded to the runtime log before bypass')
else:
    print('V42: patch already present')

f = p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'strcasestr(ascii, "shcore")',
    'strcasestr(load_name, "shcore")',
    'SHCORE V42 GetDpiForMonitor',
    'SHCORE V42 SetProcessDpiAwareness',
    'DIALOG V42 RESOURCE:',
    'DIALOG V42 TEXT:',
    'DIALOG V42 X64 BYPASS:',
):
    if token not in f:
        raise SystemExit('ERROR: V42 verification failed: ' + token)
print('MXXHUB_WINDOWS_V42_SHCORE_DPI_AND_DIALOG_DIAG_OK')
