#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_VIRTUALQUERY_SEMANTICS_FIX_V13"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_virtualquery_v13.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

e = engine_p.read_text(encoding="utf-8")

# V13 requires the V12 stack/heap special cases.
for token in (
    "VirtualQuery V12 stack",
    "VirtualQuery V12 guest-heap",
    "MXXHUB_STACK_VIRTUALQUERY_FIX_V12",
):
    if token not in e:
        raise SystemExit("ERROR: V13 requires V12 runtime token: " + token)

if MARKER not in e:
    # ------------------------------------------------------------------
    # V13A: fix stack VirtualQuery semantics.
    #
    # Windows VirtualQuery reports a region BEGINNING AT the queried page
    # (lpAddress rounded down), with RegionSize covering the bytes from that
    # queried page to the end of the matching run.
    #
    # V12 incorrectly returned:
    #   BaseAddress = full stack low address (0x7FEF0000)
    #   RegionSize  = full 1 MiB stack size
    #
    # When Boehm queried 0x7FFDE000 it therefore computed:
    #   0x7FFDE000 + 0x100000 = 0x800DE000
    # as the stack base. The device log then literally queried 0x800DE000,
    # 0x8009FFFF, 0x8005FFFF, ... and warned that the real SP was out of range.
    # ------------------------------------------------------------------
    old_stack = '''                uint64_t base = lo;
                uint64_t alloc_base = lo;
                uint32_t alloc_prot = MXX_PAGE_READWRITE;
                uint64_t region_size = hi - lo;
'''
    new_stack = '''                /* MXXHUB_VIRTUALQUERY_SEMANTICS_FIX_V13
                 * BaseAddress is the queried page, not AllocationBase.
                 * RegionSize is the remaining run from BaseAddress upward.
                 */
                uint64_t base = mxx_align_down(addr, 0x1000);
                if (base < lo) base = lo;
                uint64_t alloc_base = lo;
                uint32_t alloc_prot = MXX_PAGE_READWRITE;
                uint64_t region_size = hi > base ? hi - base : 0x1000;
'''
    if old_stack not in e:
        raise SystemExit("ERROR: V13 stack VirtualQuery body changed")
    e = e.replace(old_stack, new_stack, 1)

    old_stack_log = '''                            "VirtualQuery V12 stack addr=0x%llX -> "
                            "0x%llX-0x%llX COMMIT tid=0x%X",
                            (unsigned long long)addr,
                            (unsigned long long)lo,
                            (unsigned long long)hi, t->id);
'''
    new_stack_log = '''                            "VirtualQuery V13 stack addr=0x%llX -> "
                            "Base=0x%llX Remain=0x%llX "
                            "AllocBase=0x%llX Top=0x%llX COMMIT tid=0x%X",
                            (unsigned long long)addr,
                            (unsigned long long)base,
                            (unsigned long long)region_size,
                            (unsigned long long)lo,
                            (unsigned long long)hi, t->id);
'''
    if old_stack_log not in e:
        raise SystemExit("ERROR: V13 stack VirtualQuery log changed")
    e = e.replace(old_stack_log, new_stack_log, 1)

    # ------------------------------------------------------------------
    # V13B: fix the same semantics in the normal x64 VirtualQuery path.
    # V6/V7 returned an allocation/commit's original base and entire size
    # even when the query was in the middle of it. That is not Windows
    # behavior and breaks address-space walkers.
    # ------------------------------------------------------------------
    old_x64 = '''        uint8_t mbi[48] = {0};
        uint64_t base = r ? (c ? c->base : r->base) : mxx_align_down(addr, 0x1000);
        uint64_t alloc_base = r ? r->base : 0;
        uint32_t alloc_prot = r ? r->protect : 0;
        uint64_t region_size = r ? (c ? c->size : r->size) : 0x10000;
        uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
'''
    new_x64 = '''        uint8_t mbi[48] = {0};
        uint64_t base = mxx_align_down(addr, 0x1000);
        uint64_t alloc_base = r ? r->base : 0;
        uint32_t alloc_prot = r ? r->protect : 0;
        uint64_t region_end = base + 0x10000;
        if (r) {
            region_end = c ? (c->base + c->size) : (r->base + r->size);
        }
        uint64_t region_size =
            region_end > base ? (region_end - base) : 0x1000;
        uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
'''
    if old_x64 not in e:
        raise SystemExit("ERROR: V13 x64 VirtualQuery body changed")
    e = e.replace(old_x64, new_x64, 1)

    # Add a small x64 diagnostic for the low VirtualAlloc arena that GC scans.
    old_x64_return = '''        wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi);
        return sizeof mbi;
    }

    if (out_len < 28) return 0;
'''
    new_x64_return = '''        wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi);
        static int v13_va_logs = 0;
        if (addr >= 0x80000000ULL && addr < 0x90000000ULL &&
            v13_va_logs++ < 24) {
            WG_LOGI(TAG,
                    "VirtualQuery V13 VA addr=0x%llX -> "
                    "Base=0x%llX Remain=0x%llX State=0x%X Protect=0x%X",
                    (unsigned long long)addr,
                    (unsigned long long)base,
                    (unsigned long long)region_size,
                    state, prot);
        }
        return sizeof mbi;
    }

    if (out_len < 28) return 0;
'''
    if old_x64_return not in e:
        raise SystemExit("ERROR: V13 x64 VirtualQuery return anchor changed")
    e = e.replace(old_x64_return, new_x64_return, 1)

    # ------------------------------------------------------------------
    # V13C: keep x86 behavior consistent.
    # ------------------------------------------------------------------
    old_x86 = '''    uint8_t mbi[28] = {0};
    uint32_t base = (uint32_t)(r ? (c ? c->base : r->base) : mxx_align_down(addr, 0x1000));
    uint32_t alloc_base = (uint32_t)(r ? r->base : 0);
    uint32_t alloc_prot = r ? r->protect : 0;
    uint32_t region_size = (uint32_t)(r ? (c ? c->size : r->size) : 0x10000);
    uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
'''
    new_x86 = '''    uint8_t mbi[28] = {0};
    uint64_t base64 = mxx_align_down(addr, 0x1000);
    uint64_t region_end64 = base64 + 0x10000;
    if (r) {
        region_end64 = c ? (c->base + c->size) : (r->base + r->size);
    }
    uint32_t base = (uint32_t)base64;
    uint32_t alloc_base = (uint32_t)(r ? r->base : 0);
    uint32_t alloc_prot = r ? r->protect : 0;
    uint32_t region_size = (uint32_t)(
        region_end64 > base64 ? region_end64 - base64 : 0x1000);
    uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
'''
    if old_x86 not in e:
        raise SystemExit("ERROR: V13 x86 VirtualQuery body changed")
    e = e.replace(old_x86, new_x86, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V13: corrected VirtualQuery BaseAddress/RegionSize semantics")
else:
    print("V13: VirtualQuery semantics patch already present")

ev = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "VirtualQuery V13 stack",
    "VirtualQuery V13 VA",
    "uint64_t base = mxx_align_down(addr, 0x1000)",
    "hi > base ? hi - base : 0x1000",
    "region_end > base ? (region_end - base) : 0x1000",
):
    if token not in ev:
        raise SystemExit("ERROR: V13 verification failed: " + token)

print("MXXHUB_VIRTUALQUERY_SEMANTICS_FIX_V13_OK")
