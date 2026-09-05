#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_tls_teb_v23.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_TEB_TLS_MIRROR_FIX_V23"

if MARKER not in s:
    # Host-side TLS arrays alone are not enough on Windows. MSVC/Mono may read
    # dynamic TLS directly from the TEB:
    #
    #   x64 TEB.TlsSlots[64]     @ GS:[0x1480]
    #   x64 TEB.TlsExpansionSlots @ GS:[0x1780]
    #   x86 TEB.TlsSlots[64]     @ FS:[0x0E10]
    #   x86 TEB.TlsExpansionSlots @ FS:[0x0F94]
    #
    # The V22 log shows slot 0x22 being set again and again to a newly allocated
    # 16-byte pointer. That is exactly what happens when inline TLS reads keep
    # seeing zero even though our side-array TlsSetValue succeeded.

    state_anchor = "static uint32_t s_tls_next = 0;\n"
    if state_anchor not in s:
        raise SystemExit("ERROR: V23 TLS state anchor changed")

    s = s.replace(
        state_anchor,
        state_anchor +
        "/* MXXHUB_TEB_TLS_MIRROR_FIX_V23 */\n"
        "static uint32_t s_mxx_tls_expansion[WG_MAX_THREADS] = {0};\n",
        1
    )

    # Replace TlsGetValue + TlsSetValue + TlsFree as one stable block.
    start = s.find('        } else if (strcmp(fn, "TlsGetValue") == 0) {')
    end = s.find('        } else if (strcmp(fn, "FlsAlloc") == 0) {', start)
    if start < 0 or end < 0:
        raise SystemExit("ERROR: V23 TLS API block changed")

    new_block = r'''        } else if (strcmp(fn, "TlsGetValue") == 0) {
            int ti = (engine->scheduler && engine->scheduler->current >= 0)
                     ? engine->scheduler->current : 0;
            uint32_t slot = (uint32_t)args[0];
            ret_val = (slot < 1088) ? s_tls_slots[ti][slot] : 0;
            s_last_error = 0;

        } else if (strcmp(fn, "TlsSetValue") == 0) {
            int ti = (engine->scheduler && engine->scheduler->current >= 0)
                     ? engine->scheduler->current : 0;
            uint32_t slot = (uint32_t)args[0];
            uint64_t value = args[1];

            if (slot < 1088) {
                s_tls_slots[ti][slot] = value;

                /* MXXHUB_TEB_TLS_MIRROR_FIX_V23
                 * Mirror Win32 dynamic TLS into the actual current TEB too.
                 * Guest CRT/Mono code may fetch TLS with a direct GS/FS load
                 * instead of calling TlsGetValue().
                 */
                uint32_t teb = s_main_teb;
                if (engine->scheduler && ti >= 0 && ti < WG_MAX_THREADS) {
                    WGThread *ct = &engine->scheduler->threads[ti];
                    if (ct->teb) teb = ct->teb;
                }

                if (teb && engine->blink && engine->pe_image) {
                    if (engine->pe_image->is_64bit) {
                        if (slot < 64) {
                            uint64_t dst = (uint64_t)teb + 0x1480ULL +
                                           (uint64_t)slot * 8ULL;
                            wg_blink_write_mem(engine->blink, dst, &value, 8);
                        } else {
                            uint32_t exp = s_mxx_tls_expansion[ti];
                            if (!exp) {
                                exp = wg_guest_alloc(engine, 1024u * 8u);
                                if (exp) {
                                    s_mxx_tls_expansion[ti] = exp;
                                    uint64_t exp64 = exp;
                                    wg_blink_write_mem(engine->blink,
                                                       (uint64_t)teb + 0x1780ULL,
                                                       &exp64, 8);
                                }
                            }
                            if (exp && slot < 1088) {
                                uint64_t dst = (uint64_t)exp +
                                               (uint64_t)(slot - 64) * 8ULL;
                                wg_blink_write_mem(engine->blink, dst, &value, 8);
                            }
                        }
                    } else {
                        uint32_t v32 = (uint32_t)value;
                        if (slot < 64) {
                            uint32_t dst = teb + 0x0E10u + slot * 4u;
                            wg_blink_write_mem(engine->blink, dst, &v32, 4);
                        } else {
                            uint32_t exp = s_mxx_tls_expansion[ti];
                            if (!exp) {
                                exp = wg_guest_alloc(engine, 1024u * 4u);
                                if (exp) {
                                    s_mxx_tls_expansion[ti] = exp;
                                    wg_blink_write_mem(engine->blink,
                                                       teb + 0x0F94u,
                                                       &exp, 4);
                                }
                            }
                            if (exp && slot < 1088) {
                                uint32_t dst = exp + (slot - 64u) * 4u;
                                wg_blink_write_mem(engine->blink, dst, &v32, 4);
                            }
                        }
                    }
                }

                static unsigned v23_mirror_logs = 0;
                if (v23_mirror_logs++ < 48) {
                    WG_LOGI(TAG,
                            "TLS TEB V23 MIRROR: tid=%d slot=%u value=0x%llX teb=0x%X",
                            ti, slot, (unsigned long long)value, teb);
                }

                static unsigned v18_tls64_logs = 0;
                if ((value >> 32) && v18_tls64_logs++ < 32) {
                    WG_LOGI(TAG,
                            "TLS64 V18 SET: tid=%d slot=%u value=0x%llX "
                            "(high32=0x%llX preserved)",
                            ti, slot,
                            (unsigned long long)value,
                            (unsigned long long)(value >> 32));
                }
            }

            static unsigned v17_tls_calls = 0;
            v17_tls_calls++;
            if ((v17_tls_calls & 0x7F) == 0 &&
                engine->pe_image && engine->pe_image->is_64bit) {
                uint64_t rsp_now = wg_blink_get_reg(engine->blink, 4);
                uint64_t used = rsp_now < 0x7FFF0000ULL
                    ? 0x7FFF0000ULL - rsp_now : 0;
                long long remain = (long long)(
                    ((int64_t)rsp_now - (int64_t)0x7EFF0000ULL) / 1024LL);
                WG_LOGI(TAG,
                        "STACK V17 waterline: RSP=0x%llX used=%llu KiB "
                        "remaining=%lld KiB tlsCalls=%u",
                        (unsigned long long)rsp_now,
                        (unsigned long long)(used / 1024ULL),
                        remain,
                        v17_tls_calls);
            }

            ret_val = 1;

        } else if (strcmp(fn, "TlsFree") == 0) {
            uint32_t slot = (uint32_t)args[0];
            if (slot < 1088) {
                for (int ti = 0; ti < WG_MAX_THREADS; ti++) {
                    s_tls_slots[ti][slot] = 0;
                }
            }
            ret_val = 1;

'''
    s = s[:start] + new_block + s[end:]

    # Reset expansion-array guest addresses on every fresh program load.
    reset_anchor = "    memset(s_tls_slots, 0, sizeof(s_tls_slots));\n"
    if reset_anchor not in s:
        raise SystemExit("ERROR: V23 TLS reset anchor changed")
    s = s.replace(
        reset_anchor,
        reset_anchor +
        "    memset(s_mxx_tls_expansion, 0, sizeof(s_mxx_tls_expansion));\n",
        1
    )

    engine_p.write_text(s, encoding="utf-8")
    print("V23: dynamic TLS is now mirrored into the guest TEB")
else:
    print("V23: TEB TLS mirror already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "TLS TEB V23 MIRROR:",
    "0x1480ULL",
    "0x1780ULL",
    "0x0E10u",
    "0x0F94u",
    "s_mxx_tls_expansion",
):
    if token not in final:
        raise SystemExit("ERROR: V23 verification failed: " + token)

print("MXXHUB_TEB_TLS_MIRROR_FIX_V23_OK")
