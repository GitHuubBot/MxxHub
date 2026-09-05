#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_BAD_RIP_RECOVERY_V15"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_bad_rip_v15.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

e = engine_p.read_text(encoding="utf-8")

if MARKER not in e:
    old = r'''                    // Try auto-recovery for calls to unmapped addresses (bad
                    // vtable / uninitialized function pointer): if RIP is
                    // outside all PE sections and the return address on the
                    // stack points into .text, return 0 to the caller.
                    uint32_t pe_end = engine->pe_image
                        ? (uint32_t)(engine->pe_image->image_base + 0x4C0000)
                        : 0x8C0000;
                    if (halt_rip > pe_end) {
                        uint32_t esp = (uint32_t)wg_blink_get_reg(engine->blink, 4);
                        uint32_t ret = 0;
                        wg_blink_read_mem(engine->blink, esp, &ret, 4);
                        uint32_t text_lo = engine->pe_image
                            ? (uint32_t)engine->pe_image->image_base + 0x1000
                            : 0x401000;
                        if (ret >= text_lo && ret < pe_end) {
                            WG_LOGW(TAG, "Auto-recover: call to unmapped 0x%llx, "
                                    "returning 0 to 0x%X",
                                    (unsigned long long)halt_rip, ret);
                            wg_blink_set_reg(engine->blink, 0, 0); // EAX = 0
                            wg_blink_set_reg(engine->blink, 4, esp + 4);
                            wg_blink_set_rip(engine->blink, ret);
                            break;
                        }
                    }
'''
    new = r'''                    /* MXXHUB_BAD_RIP_RECOVERY_V15
                     *
                     * The old auto-recovery below was effectively x86-only:
                     * it read a 32-bit ESP/return address and only accepted
                     * return addresses inside the main EXE's tiny .text range.
                     *
                     * Hollow Knight is x64 and UnityPlayer.dll lives around
                     * 0x60xxxxxx.  The repeatable crash we are targeting is:
                     *
                     *   RIP = 0x0AE64B77E88C8000
                     *   [RSP] = 0x0000000062666BFA
                     *
                     * That RIP is a non-canonical x64 address, while [RSP] is
                     * a believable return address inside a loaded PE module.
                     * This is the exact signature of an indirect CALL through
                     * a corrupted/uninitialized callback pointer.
                     *
                     * On real Windows, this path is normally swallowed by the
                     * runtime/SEH layer.  Our guest SEH is not complete enough
                     * yet, so for x64 only, recover the failed optional call:
                     * return NULL/0 to the caller and emulate RET.
                     */
                    if (engine->pe_image && engine->pe_image->is_64bit) {
                        uint64_t rsp64 = wg_blink_get_reg(engine->blink, 4);
                        uint64_t ret64 = 0;
                        bool have_ret = rsp64 &&
                            wg_blink_read_mem(engine->blink, rsp64, &ret64, 8);

                        /* x86-64 canonical address test. */
                        uint64_t top16 = halt_rip >> 48;
                        bool bad_noncanonical =
                            (top16 != 0x0000ULL && top16 != 0xFFFFULL);

                        /* This WineGlass x64 runtime deliberately maps guest
                           PE code below 4GB. */
                        bool bad_outside_guest =
                            halt_rip >= 0x0000000100000000ULL;

                        bool ret_in_module = false;
                        const char *ret_mod = NULL;

                        if (have_ret && ret64 < 0x0000000100000000ULL) {
                            /* Main executable. */
                            if (engine->pe_image) {
                                uint64_t lo = engine->pe_image->image_base;
                                uint64_t hi = lo + (engine->pe_image->size_of_image
                                    ? engine->pe_image->size_of_image : 0x800000ULL);
                                if (ret64 >= lo && ret64 < hi) {
                                    ret_in_module = true;
                                    ret_mod = "<main-exe>";
                                }
                            }

                            /* Real mapped DLLs, including UnityPlayer.dll. */
                            if (!ret_in_module) {
                                for (int mi = 0; mi < 16; ++mi) {
                                    if (!s_modules[mi].in_use) continue;
                                    uint64_t lo = s_modules[mi].base;
                                    uint64_t hi = lo + s_modules[mi].size;
                                    if (ret64 >= lo && ret64 < hi) {
                                        ret_in_module = true;
                                        ret_mod = s_modules[mi].name;
                                        break;
                                    }
                                }
                            }

                            /* Win32 thunk page is also a legal caller. */
                            if (!ret_in_module &&
                                ret64 >= 0x00C00000ULL &&
                                ret64 <  0x00C20000ULL) {
                                ret_in_module = true;
                                ret_mod = "<win32-thunk>";
                            }
                        }

                        if ((bad_noncanonical || bad_outside_guest) &&
                            have_ret && ret_in_module) {
                            static int s_v15_bad_rip_recoveries = 0;
                            if (s_v15_bad_rip_recoveries < 8) {
                                WG_LOGW(TAG,
                                    "BAD RIP V15 RECOVERY #%d: "
                                    "target=0x%llX rsp=0x%llX "
                                    "ret=0x%llX module=%s -> return 0",
                                    s_v15_bad_rip_recoveries + 1,
                                    (unsigned long long)halt_rip,
                                    (unsigned long long)rsp64,
                                    (unsigned long long)ret64,
                                    ret_mod ? ret_mod : "?");
                            }
                            s_v15_bad_rip_recoveries++;

                            /* Microsoft x64: CALL pushed exactly one 8-byte
                               return address. */
                            wg_blink_set_reg(engine->blink, 0, 0); /* RAX = 0 */
                            wg_blink_set_reg(engine->blink, 4, rsp64 + 8);
                            wg_blink_set_rip(engine->blink, ret64);
                            break;
                        }
                    }

                    // Legacy x86 auto-recovery.
                    uint32_t pe_end = engine->pe_image
                        ? (uint32_t)(engine->pe_image->image_base + 0x4C0000)
                        : 0x8C0000;
                    if (halt_rip > pe_end) {
                        uint32_t esp = (uint32_t)wg_blink_get_reg(engine->blink, 4);
                        uint32_t ret = 0;
                        wg_blink_read_mem(engine->blink, esp, &ret, 4);
                        uint32_t text_lo = engine->pe_image
                            ? (uint32_t)engine->pe_image->image_base + 0x1000
                            : 0x401000;
                        if (ret >= text_lo && ret < pe_end) {
                            WG_LOGW(TAG, "Auto-recover: call to unmapped 0x%llx, "
                                    "returning 0 to 0x%X",
                                    (unsigned long long)halt_rip, ret);
                            wg_blink_set_reg(engine->blink, 0, 0); // EAX = 0
                            wg_blink_set_reg(engine->blink, 4, esp + 4);
                            wg_blink_set_rip(engine->blink, ret);
                            break;
                        }
                    }
'''
    if old not in e:
        raise SystemExit("ERROR: V15 bad-RIP auto-recovery anchor changed")
    e = e.replace(old, new, 1)

    old_dump = r'''                    // Stack dump
                    uint32_t stk[8] = {0};
                    uint32_t esp = (uint32_t)wg_blink_get_reg(engine->blink, 4);
                    wg_blink_read_mem(engine->blink, esp, stk, sizeof(stk));
                    WG_LOGE(TAG, "  stack: %08X %08X %08X %08X  %08X %08X %08X %08X",
                        stk[0],stk[1],stk[2],stk[3],stk[4],stk[5],stk[6],stk[7]);
'''
    new_dump = r'''                    // Stack dump
                    uint32_t stk[8] = {0};
                    uint32_t esp = (uint32_t)wg_blink_get_reg(engine->blink, 4);
                    wg_blink_read_mem(engine->blink, esp, stk, sizeof(stk));
                    WG_LOGE(TAG, "  stack: %08X %08X %08X %08X  %08X %08X %08X %08X",
                        stk[0],stk[1],stk[2],stk[3],stk[4],stk[5],stk[6],stk[7]);
                    if (engine->pe_image && engine->pe_image->is_64bit) {
                        uint64_t rspq = wg_blink_get_reg(engine->blink, 4);
                        uint64_t qstk[6] = {0};
                        wg_blink_read_mem(engine->blink, rspq, qstk, sizeof(qstk));
                        WG_LOGE(TAG,
                            "  x64 stack qwords: %016llX %016llX %016llX "
                            "%016llX %016llX %016llX",
                            (unsigned long long)qstk[0],
                            (unsigned long long)qstk[1],
                            (unsigned long long)qstk[2],
                            (unsigned long long)qstk[3],
                            (unsigned long long)qstk[4],
                            (unsigned long long)qstk[5]);
                    }
'''
    if old_dump not in e:
        raise SystemExit("ERROR: V15 crash stack-dump anchor changed")
    e = e.replace(old_dump, new_dump, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V15: added x64 non-canonical bad-RIP recovery")
    print("V15: recovery validates [RSP] against main EXE / loaded DLLs")
    print("V15: added x64 qword stack diagnostics")
else:
    print("V15: bad-RIP recovery already present")

ev = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "BAD RIP V15 RECOVERY",
    "bad_noncanonical",
    "ret_in_module",
    "rsp64 + 8",
    "x64 stack qwords",
):
    if token not in ev:
        raise SystemExit("ERROR: V15 verification failed: " + token)

print("MXXHUB_BAD_RIP_RECOVERY_V15_OK")
