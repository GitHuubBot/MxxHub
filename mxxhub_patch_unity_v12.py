#!/usr/bin/env python3
from pathlib import Path
import re
import sys

MARKER = "MXXHUB_STACK_VIRTUALQUERY_FIX_V12"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v12.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
mapper_p = wg / "Sources/Win32/wg_dll_mapper.c"

for p in (engine_p, mapper_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

e = engine_p.read_text(encoding="utf-8")
m = mapper_p.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# V12A — the main thread was registered in the cooperative scheduler without
# its stack_base/stack_size. Unity/Boehm is now far enough to inspect the
# current thread stack, so make scheduler metadata agree with the Win64 TEB.
# ---------------------------------------------------------------------------
if MARKER not in e:
    main_anchor = '''        mt->exit_code = 259;
        mt->regs.fs_base = pe->is_64bit ? 0 : s_main_teb;
'''
    if main_anchor not in e:
        raise SystemExit("ERROR: V12 main-thread scheduler anchor changed")

    main_repl = '''        mt->exit_code = 259;
        /* MXXHUB_STACK_VIRTUALQUERY_FIX_V12
         * Match the 1 MiB stack installed by wg_blink_setup_stack() and the
         * StackLimit/StackBase values written into the Win64 TEB.
         */
        mt->stack_base = 0x7FEF0000u;
        mt->stack_size = 0x00100000u;
        mt->regs.fs_base = pe->is_64bit ? 0 : s_main_teb;
'''
    e = e.replace(main_anchor, main_repl, 1)
    print("V12: main scheduler thread now has real stack bounds")
else:
    print("V12: main scheduler stack metadata already present")

# V12.1: persist V12A before V12B re-reads wg_engine.c.
# The original V12 accidentally threw away the stack_base/stack_size edit here.
engine_p.write_text(e, encoding="utf-8")
print("V12.1: persisted main-thread stack metadata before VirtualQuery patch")

# ---------------------------------------------------------------------------
# V12B — VirtualQuery previously reported every non-V6 allocation as MEM_FREE.
# That includes:
#   * the live main stack around 0x7FFDxxxx,
#   * scheduler worker stacks from 0x30000000,
#   * wg_guest_alloc() memory at 0x20000000.
#
# The device log proves Boehm queried those addresses and then emitted:
# "GC Warning: Thread stack pointer ... out of range".
# ---------------------------------------------------------------------------
e = engine_p.read_text(encoding="utf-8")

vq_anchor = '''    MxxVaRegion *r = mxx_va_find_region(addr, 1);
    MxxVaCommit *c = mxx_va_find_commit(addr);
'''
if "VirtualQuery V12 stack" not in e:
    if vq_anchor not in e:
        raise SystemExit("ERROR: V12 mxx_virtual_query anchor changed")

    vq_special = r'''    /* MXXHUB_STACK_VIRTUALQUERY_FIX_V12
     * Report real Blink/scheduler memory before consulting the V6 metadata
     * allocator. Otherwise GC sees the actual stack and legacy heap as FREE.
     */
    if (!is_32bit && engine->scheduler) {
        for (int ti = 0; ti < WG_MAX_THREADS; ++ti) {
            WGThread *t = &engine->scheduler->threads[ti];
            if (t->state == WG_THREAD_FREE || !t->stack_size) continue;
            uint64_t lo = (uint64_t)t->stack_base;
            uint64_t hi = lo + (uint64_t)t->stack_size;
            if (addr >= lo && addr < hi) {
                if (out_len < 48) return 0;
                uint8_t mbi[48] = {0};
                uint64_t base = lo;
                uint64_t alloc_base = lo;
                uint32_t alloc_prot = MXX_PAGE_READWRITE;
                uint64_t region_size = hi - lo;
                uint32_t state = MXX_MEM_COMMIT;
                uint32_t prot = MXX_PAGE_READWRITE;
                uint32_t type = MXX_MEM_PRIVATE;
                memcpy(mbi + 0,  &base,        8);
                memcpy(mbi + 8,  &alloc_base,  8);
                memcpy(mbi + 16, &alloc_prot,  4);
                memcpy(mbi + 24, &region_size, 8);
                memcpy(mbi + 32, &state,       4);
                memcpy(mbi + 36, &prot,        4);
                memcpy(mbi + 40, &type,        4);
                if (!wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi))
                    return 0;
                static int v12_stack_logs = 0;
                if (v12_stack_logs++ < 12) {
                    WG_LOGI(TAG,
                            "VirtualQuery V12 stack addr=0x%llX -> "
                            "0x%llX-0x%llX COMMIT tid=0x%X",
                            (unsigned long long)addr,
                            (unsigned long long)lo,
                            (unsigned long long)hi, t->id);
                }
                return sizeof mbi;
            }
        }
    }

    /* wg_guest_alloc() is a mapped, page-backed bump arena beginning at
       0x20000000. It backs TEB/PEB/TLS/CRT/GC allocations and must not look
       like MEM_FREE to Windows runtime code. Keep queries page-granular. */
    if (!is_32bit && addr >= 0x20000000ULL && addr < 0x30000000ULL) {
        if (out_len < 48) return 0;
        uint8_t mbi[48] = {0};
        uint64_t base = mxx_align_down(addr, 0x1000);
        uint64_t alloc_base = base;
        uint32_t alloc_prot = MXX_PAGE_READWRITE;
        uint64_t region_size = 0x1000;
        uint32_t state = MXX_MEM_COMMIT;
        uint32_t prot = MXX_PAGE_READWRITE;
        uint32_t type = MXX_MEM_PRIVATE;
        memcpy(mbi + 0,  &base,        8);
        memcpy(mbi + 8,  &alloc_base,  8);
        memcpy(mbi + 16, &alloc_prot,  4);
        memcpy(mbi + 24, &region_size, 8);
        memcpy(mbi + 32, &state,       4);
        memcpy(mbi + 36, &prot,        4);
        memcpy(mbi + 40, &type,        4);
        if (!wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi))
            return 0;
        static int v12_heap_logs = 0;
        if (v12_heap_logs++ < 12) {
            WG_LOGI(TAG,
                    "VirtualQuery V12 guest-heap addr=0x%llX -> "
                    "page=0x%llX COMMIT",
                    (unsigned long long)addr,
                    (unsigned long long)base);
        }
        return sizeof mbi;
    }

'''
    e = e.replace(vq_anchor, vq_special + vq_anchor, 1)
    print("V12: VirtualQuery now reports scheduler stacks + guest heap as committed")

# ---------------------------------------------------------------------------
# V12C — expose GetCurrentThreadStackLimits for modern Unity/MSVC code.
# ---------------------------------------------------------------------------
if "GetCurrentThreadStackLimits V12" not in e:
    dispatch_anchor = '''        } else if (strcmp(fn, "GetCurrentThread") == 0) {
'''
    if dispatch_anchor not in e:
        raise SystemExit("ERROR: V12 GetCurrentThread dispatch anchor changed")

    stack_limits_branch = r'''        } else if (strcmp(fn, "GetCurrentThreadStackLimits") == 0) {
            uint64_t lo = 0x7FEF0000ULL;
            uint64_t hi = 0x7FFF0000ULL;
            WGThread *ct = engine->scheduler
                ? wg_sched_current(engine->scheduler) : NULL;
            if (ct && ct->stack_size) {
                lo = (uint64_t)ct->stack_base;
                hi = lo + (uint64_t)ct->stack_size;
            }
            if (args[0])
                wg_blink_write_mem(engine->blink, args[0], &lo, 8);
            if (args[1])
                wg_blink_write_mem(engine->blink, args[1], &hi, 8);
            WG_LOGI(TAG,
                    "GetCurrentThreadStackLimits V12 -> "
                    "0x%llX-0x%llX",
                    (unsigned long long)lo,
                    (unsigned long long)hi);
            ret_val = 0; /* VOID */
'''
    e = e.replace(dispatch_anchor, stack_limits_branch + dispatch_anchor, 1)
    print("V12: implemented GetCurrentThreadStackLimits")

engine_p.write_text(e, encoding="utf-8")

if "GetCurrentThreadStackLimits" not in m:
    # Structural insertion after GetCurrentThread registration.
    pat = re.compile(
        r'(?m)^(?P<line>\s*RS\s*\(\s*"KERNEL32\.dll"\s*,\s*'
        r'GetCurrentThread\s*,\s*0\s*\)\s*;\s*\n)'
    )
    mm = pat.search(m)
    if not mm:
        raise SystemExit(
            "ERROR: V12 could not locate GetCurrentThread registration structurally"
        )
    reg = '    RS ("KERNEL32.dll", GetCurrentThreadStackLimits, 2);\n'
    m = m[:mm.end()] + reg + m[mm.end():]
    mapper_p.write_text(m, encoding="utf-8")
    print("V12: registered GetCurrentThreadStackLimits")
else:
    print("V12: GetCurrentThreadStackLimits already registered")

# ---------------------------------------------------------------------------
# Verification.
# ---------------------------------------------------------------------------
ev = engine_p.read_text(encoding="utf-8")
mv = mapper_p.read_text(encoding="utf-8")

for token in (
    MARKER,
    "VirtualQuery V12 stack",
    "VirtualQuery V12 guest-heap",
    "GetCurrentThreadStackLimits V12",
):
    if token not in ev:
        raise SystemExit("ERROR: V12.1 engine verification failed: " + token)

stack_base_ok = re.search(
    r'mt->stack_base\s*=\s*0x7FEF0000[uUlL]*\s*;', ev
)
stack_size_ok = re.search(
    r'mt->stack_size\s*=\s*0x0*100000[uUlL]*\s*;', ev
)
if not stack_base_ok or not stack_size_ok:
    raise SystemExit(
        "ERROR: V12.1 main-thread stack metadata missing after final engine write"
    )

if "GetCurrentThreadStackLimits" not in mv:
    raise SystemExit("ERROR: V12.1 mapper verification failed")

print("V12.1: final engine contains persisted main-thread stack metadata")
print("MXXHUB_STACK_VIRTUALQUERY_FIX_V12_1_OK")
print("MXXHUB_STACK_VIRTUALQUERY_FIX_V12_OK")
