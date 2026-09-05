#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_windows_display_v32.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine = wg / "Sources/Core/wg_engine.c"
if not engine.is_file():
    raise SystemExit(f"ERROR: missing {engine}")

s = engine.read_text(encoding="utf-8")
MARKER = "MXXHUB_WINDOWS_V32_DISPLAY_BOOT"

if MARKER not in s:
    helper_anchor = r'''static bool wg_call_wndproc(WGEngine *engine, uint32_t proc, uint32_t hwnd,
                            uint32_t msg, uint32_t wp, uint32_t lp,
                            uint32_t ret_addr, uint32_t clean_rsp) {
    return wg_call_wndproc_ovr(engine, proc, hwnd, msg, wp, lp,
                               ret_addr, clean_rsp, false, 0);
}
'''

    helper_new = helper_anchor + r'''
/* MXXHUB_WINDOWS_V32_DISPLAY_BOOT
 *
 * Microsoft x64 nested callback bridge for Win32 APIs such as
 * EnumDisplayMonitors. The existing WG_SENDMSG_SENTINEL call stack already
 * restores the guest caller after a nested callback returns.
 */
static bool wg_call_guest4_x64_ovr(WGEngine *engine, uint64_t proc,
                                   uint64_t a0, uint64_t a1,
                                   uint64_t a2, uint64_t a3,
                                   uint64_t ret_addr, uint64_t clean_rsp,
                                   bool ovr, uint32_t ovr_eax) {
    if (!proc || s_callstack_depth >= 64) return false;

    WGPendingCall *fr = &s_callstack[s_callstack_depth];
    fr->ret_addr = (uint32_t)ret_addr;
    fr->ret_rsp  = (uint32_t)clean_rsp;
    fr->ovr      = ovr;
    fr->ovr_eax  = ovr_eax;

    for (int i = 0; i < 16; i++)
        fr->saved_regs[i] = wg_blink_get_reg(engine->blink, i);

    s_callstack_depth++;

    /*
     * Win64 callback entry:
     *   RCX,RDX,R8,R9 = first four args
     *   [RSP]          = return address
     *   next 32 bytes  = mandatory shadow space
     * Entry RSP must be 8 mod 16.
     */
    uint64_t new_rsp = clean_rsp - 0x28;
    uint64_t sentinel = (uint64_t)WG_SENDMSG_SENTINEL;
    uint64_t shadow[4] = {0, 0, 0, 0};

    wg_blink_write_mem(engine->blink, new_rsp, &sentinel, 8);
    wg_blink_write_mem(engine->blink, new_rsp + 8, shadow, sizeof(shadow));

    wg_blink_set_reg(engine->blink, 4, new_rsp); /* RSP */
    wg_blink_set_reg(engine->blink, 1, a0);      /* RCX */
    wg_blink_set_reg(engine->blink, 2, a1);      /* RDX */
    wg_blink_set_reg(engine->blink, 8, a2);      /* R8  */
    wg_blink_set_reg(engine->blink, 9, a3);      /* R9  */
    wg_blink_set_reg(engine->blink, 0, 0);       /* RAX */
    wg_blink_set_rip(engine->blink, proc);
    return true;
}
'''

    if helper_anchor not in s:
        raise SystemExit("ERROR: V32 callback helper anchor changed")
    s = s.replace(helper_anchor, helper_new, 1)

    metrics_old = r'''        } else if (strcmp(fn, "GetSystemMetrics") == 0) {
            // SM_CXSCREEN=0 -> 800, SM_CYSCREEN=1 -> 600
            if (args[0] == 0) ret_val = 800;
            else if (args[0] == 1) ret_val = 600;
            else ret_val = 0;
        } else if (strcmp(fn, "GetSysColor") == 0) {
'''

    metrics_new = r'''        } else if (strcmp(fn, "GetSystemMetrics") == 0) {
            /*
             * V31 reached Unity's Windows display bootstrap. Unity queried
             * SM_X/Y/CX/CYVIRTUALSCREEN (76..79), but the old generic metrics
             * shim returned 0 for all of them. Report one coherent 800x600
             * primary/virtual desktop.
             */
            switch ((uint32_t)args[0]) {
                case 0:  ret_val = 800; break; /* SM_CXSCREEN */
                case 1:  ret_val = 600; break; /* SM_CYSCREEN */
                case 76: ret_val = 0;   break; /* SM_XVIRTUALSCREEN */
                case 77: ret_val = 0;   break; /* SM_YVIRTUALSCREEN */
                case 78: ret_val = 800; break; /* SM_CXVIRTUALSCREEN */
                case 79: ret_val = 600; break; /* SM_CYVIRTUALSCREEN */
                case 80: ret_val = 1;   break; /* SM_CMONITORS */
                default: ret_val = 0;   break;
            }
            if ((uint32_t)args[0] >= 76 && (uint32_t)args[0] <= 80) {
                WG_LOGI(TAG, "DISPLAY V32 METRIC: index=%u -> %llu",
                        (unsigned)args[0],
                        (unsigned long long)ret_val);
            }

        } else if (strcmp(fn, "EnumDisplayDevicesA") == 0) {
            /*
             * BOOL EnumDisplayDevicesA(LPCSTR device, DWORD index,
             *                          PDISPLAY_DEVICEA out, DWORD flags)
             * DISPLAY_DEVICEA is 424 bytes.
             */
            uint32_t index = (uint32_t)args[1];
            uint64_t outp = args[2];

            if (index != 0 || !outp) {
                ret_val = 0;
            } else {
                uint32_t cb = 0;
                wg_blink_read_mem(engine->blink, outp, &cb, 4);
                if (cb < 4) cb = 424;
                if (cb > 424) cb = 424;

                uint8_t dd[424];
                memset(dd, 0, sizeof(dd));

                uint32_t canonical_cb = 424;
                uint32_t state = 0x00000001u | 0x00000004u; /* attached + primary */
                memcpy(dd + 0, &canonical_cb, 4);
                snprintf((char *)dd + 4,   32,  "\\\\.\\DISPLAY1");
                snprintf((char *)dd + 36,  128, "MxxHub Virtual Display");
                memcpy(dd + 164, &state, 4);
                snprintf((char *)dd + 168, 128, "MXXHUB\\DISPLAY1");
                snprintf((char *)dd + 296, 128,
                         "\\Registry\\Machine\\System\\MxxHub\\Display1");

                wg_blink_write_mem(engine->blink, outp, dd, cb);
                WG_LOGI(TAG,
                        "DISPLAY V32 DEVICE: EnumDisplayDevicesA index=0 -> "
                        "\\\\.\\DISPLAY1 primary attached cb=%u",
                        cb);
                ret_val = 1;
            }

        } else if (strcmp(fn, "EnumDisplayDevicesW") == 0) {
            /* Wide DISPLAY_DEVICEW is 840 bytes. */
            uint32_t index = (uint32_t)args[1];
            uint64_t outp = args[2];

            if (index != 0 || !outp) {
                ret_val = 0;
            } else {
                uint32_t cb = 0;
                wg_blink_read_mem(engine->blink, outp, &cb, 4);
                if (cb < 4) cb = 840;
                if (cb > 840) cb = 840;

                uint8_t dd[840];
                memset(dd, 0, sizeof(dd));
                uint32_t canonical_cb = 840;
                uint32_t state = 0x00000001u | 0x00000004u;
                memcpy(dd + 0, &canonical_cb, 4);

                const uint16_t name[] = {
                    '\\','\\','.','\\','D','I','S','P','L','A','Y','1',0
                };
                const uint16_t desc[] = {
                    'M','x','x','H','u','b',' ','V','i','r','t','u','a','l',' ',
                    'D','i','s','p','l','a','y',0
                };
                const uint16_t id[] = {
                    'M','X','X','H','U','B','\\','D','I','S','P','L','A','Y','1',0
                };
                memcpy(dd + 4, name, sizeof(name));
                memcpy(dd + 68, desc, sizeof(desc));
                memcpy(dd + 324, &state, 4);
                memcpy(dd + 328, id, sizeof(id));

                wg_blink_write_mem(engine->blink, outp, dd, cb);
                WG_LOGI(TAG,
                        "DISPLAY V32 DEVICE: EnumDisplayDevicesW index=0 -> "
                        "DISPLAY1 primary attached cb=%u",
                        cb);
                ret_val = 1;
            }

        } else if (strcmp(fn, "MonitorFromWindow") == 0 ||
                   strcmp(fn, "MonitorFromRect") == 0 ||
                   strcmp(fn, "MonitorFromPoint") == 0) {
            ret_val = 0x501; /* one synthetic primary HMONITOR */

        } else if (strcmp(fn, "GetMonitorInfoA") == 0) {
            uint64_t outp = args[1];
            if (!outp) {
                ret_val = 0;
            } else {
                uint32_t cb = 0;
                wg_blink_read_mem(engine->blink, outp, &cb, 4);
                if (cb < 40) cb = 40;
                if (cb > 72) cb = 72;

                uint8_t mi[72];
                memset(mi, 0, sizeof(mi));
                uint32_t canonical_cb = cb;
                int32_t rect[4] = {0, 0, 800, 600};
                uint32_t primary = 1;

                memcpy(mi + 0, &canonical_cb, 4);
                memcpy(mi + 4, rect, sizeof(rect));
                memcpy(mi + 20, rect, sizeof(rect));
                memcpy(mi + 36, &primary, 4);
                if (cb > 40)
                    snprintf((char *)mi + 40, 32, "\\\\.\\DISPLAY1");

                wg_blink_write_mem(engine->blink, outp, mi, cb);
                WG_LOGI(TAG, "DISPLAY V32 MONITORINFOA: 800x600 primary cb=%u", cb);
                ret_val = 1;
            }

        } else if (strcmp(fn, "GetMonitorInfoW") == 0) {
            uint64_t outp = args[1];
            if (!outp) {
                ret_val = 0;
            } else {
                uint32_t cb = 0;
                wg_blink_read_mem(engine->blink, outp, &cb, 4);
                if (cb < 40) cb = 40;
                if (cb > 104) cb = 104;

                uint8_t mi[104];
                memset(mi, 0, sizeof(mi));
                uint32_t canonical_cb = cb;
                int32_t rect[4] = {0, 0, 800, 600};
                uint32_t primary = 1;
                const uint16_t name[] = {
                    '\\','\\','.','\\','D','I','S','P','L','A','Y','1',0
                };

                memcpy(mi + 0, &canonical_cb, 4);
                memcpy(mi + 4, rect, sizeof(rect));
                memcpy(mi + 20, rect, sizeof(rect));
                memcpy(mi + 36, &primary, 4);
                if (cb > 40) {
                    uint32_t n = cb - 40;
                    if (n > sizeof(name)) n = sizeof(name);
                    memcpy(mi + 40, name, n);
                }

                wg_blink_write_mem(engine->blink, outp, mi, cb);
                WG_LOGI(TAG, "DISPLAY V32 MONITORINFOW: 800x600 primary cb=%u", cb);
                ret_val = 1;
            }

        } else if (strcmp(fn, "EnumDisplayMonitors") == 0) {
            /*
             * BOOL EnumDisplayMonitors(HDC, LPCRECT, MONITORENUMPROC, LPARAM)
             * Give Unity one 800x600 monitor and invoke its guest callback.
             */
            static uint32_t s_v32_monitor_rect = 0;
            if (!s_v32_monitor_rect)
                s_v32_monitor_rect = wg_guest_alloc(engine, 16);

            if (s_v32_monitor_rect) {
                int32_t rect[4] = {0, 0, 800, 600};
                wg_blink_write_mem(engine->blink, s_v32_monitor_rect,
                                   rect, sizeof(rect));
            }

            uint64_t proc = args[2];
            if (proc && s_v32_monitor_rect) {
                uint64_t clean_rsp = is_32bit ? rsp + 20 : rsp + 8;

                WG_LOGI(TAG,
                        "DISPLAY V32 MONITOR CALLBACK: proc=0x%llX "
                        "rect=0,0,800,600 lparam=0x%llX",
                        (unsigned long long)proc,
                        (unsigned long long)args[3]);

                if (is_32bit) {
                    if (wg_call_wndproc_ovr(
                            engine, (uint32_t)proc,
                            0x501, 0, s_v32_monitor_rect, (uint32_t)args[3],
                            (uint32_t)ret_addr, (uint32_t)clean_rsp,
                            true, 1)) {
                        return true;
                    }
                } else {
                    if (wg_call_guest4_x64_ovr(
                            engine, proc,
                            0x501, 0, s_v32_monitor_rect, args[3],
                            ret_addr, clean_rsp, true, 1)) {
                        return true;
                    }
                }
            }

            /* Windows treats a NULL callback as a successful no-op. */
            ret_val = 1;

        } else if (strcmp(fn, "GetSysColor") == 0) {
'''

    if metrics_old not in s:
        raise SystemExit("ERROR: V32 GetSystemMetrics anchor changed")
    s = s.replace(metrics_old, metrics_new, 1)

    msg_anchor = r'''        } else if (strcmp(fn, "MessageBoxIndirectW") == 0) {
'''

    msg_block = r'''        } else if (strcmp(fn, "MessageBoxA") == 0 ||
                   strcmp(fn, "MessageBoxExA") == 0) {
            char text[1024] = {0};
            char title[512] = {0};

            if (args[1])
                wg_blink_read_mem(engine->blink, args[1],
                                  text, sizeof(text) - 1);
            if (args[2])
                wg_blink_read_mem(engine->blink, args[2],
                                  title, sizeof(title) - 1);

            text[sizeof(text) - 1] = 0;
            title[sizeof(title) - 1] = 0;

            WG_LOGE(TAG,
                    "MESSAGEBOX V32: title='%s' text='%s' type=0x%llX",
                    title, text, (unsigned long long)args[3]);
            ret_val = 1; /* IDOK */

        } else if (strcmp(fn, "MessageBoxW") == 0 ||
                   strcmp(fn, "MessageBoxExW") == 0) {
            uint16_t wtext[512] = {0};
            uint16_t wtitle[256] = {0};
            char text[512] = {0};
            char title[256] = {0};

            if (args[1])
                wg_blink_read_mem(engine->blink, args[1],
                                  wtext, sizeof(wtext) - 2);
            if (args[2])
                wg_blink_read_mem(engine->blink, args[2],
                                  wtitle, sizeof(wtitle) - 2);

            for (int i = 0; i < 511 && wtext[i]; i++)
                text[i] = wtext[i] < 128 ? (char)wtext[i] : '?';
            for (int i = 0; i < 255 && wtitle[i]; i++)
                title[i] = wtitle[i] < 128 ? (char)wtitle[i] : '?';

            WG_LOGE(TAG,
                    "MESSAGEBOX V32: title='%s' text='%s' type=0x%llX",
                    title, text, (unsigned long long)args[3]);
            ret_val = 1; /* IDOK */

        } else if (strcmp(fn, "MessageBoxIndirectW") == 0) {
'''

    if msg_anchor not in s:
        raise SystemExit("ERROR: V32 MessageBox anchor changed")
    s = s.replace(msg_anchor, msg_block, 1)

    engine.write_text(s, encoding="utf-8")
    print("Windows V32: display topology + monitor callback + MessageBox diagnostics installed")
else:
    print("Windows V32: patch already present")

final = engine.read_text(encoding="utf-8")
required = (
    MARKER,
    "DISPLAY V32 METRIC:",
    "DISPLAY V32 DEVICE:",
    "DISPLAY V32 MONITOR CALLBACK:",
    "DISPLAY V32 MONITORINFOA:",
    "MESSAGEBOX V32:",
    "case 78: ret_val = 800",
    "case 79: ret_val = 600",
    "case 80: ret_val = 1",
    "wg_call_guest4_x64_ovr",
)
for token in required:
    if token not in final:
        raise SystemExit("ERROR: V32 verification failed: " + token)

print("MXXHUB_WINDOWS_V32_DISPLAY_BOOT_FIX_OK")
