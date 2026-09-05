#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_virtualquery_v24.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_STACK_VIRTUALQUERY_REGION_FIX_V24"

if MARKER not in s:
    old = r'''                /* MXXHUB_VIRTUALQUERY_SEMANTICS_FIX_V13
                 * BaseAddress is the queried page, not AllocationBase.
                 * RegionSize is the remaining run from BaseAddress upward.
                 */
                uint64_t base = mxx_align_down(addr, 0x1000);
                if (base < lo) base = lo;
                uint64_t alloc_base = lo;
                uint32_t alloc_prot = MXX_PAGE_READWRITE;
                uint64_t region_size = hi > base ? hi - base : 0x1000;
'''
    new = r'''                /* MXXHUB_STACK_VIRTUALQUERY_REGION_FIX_V24
                 *
                 * A stack is one contiguous committed region with identical
                 * state/protection. MEMORY_BASIC_INFORMATION.BaseAddress must
                 * identify the beginning of that region, not the individual
                 * queried 4 KiB page.
                 *
                 * V13 returning the queried page caused Mono/Boehm's downward
                 * stack walker to do:
                 *   0x7FFEFFFF, 0x7FFEEFFF, 0x7FFEDFFF, ...
                 * one VirtualQuery call per page. With a 16 MiB stack that is
                 * thousands of calls before startup can continue.
                 */
                uint64_t base = lo;
                uint64_t alloc_base = lo;
                uint32_t alloc_prot = MXX_PAGE_READWRITE;
                uint64_t region_size = hi - lo;
'''
    if old not in s:
        raise SystemExit("ERROR: V24 V13 stack VirtualQuery block changed")
    s = s.replace(old, new, 1)

    old_log = r'''                            "VirtualQuery V13 stack addr=0x%llX -> "
                            "Base=0x%llX Remain=0x%llX "
                            "AllocBase=0x%llX Top=0x%llX COMMIT tid=0x%X",
'''
    new_log = r'''                            "VirtualQuery V24 stack-region addr=0x%llX -> "
                            "Base=0x%llX Size=0x%llX "
                            "AllocBase=0x%llX Top=0x%llX COMMIT tid=0x%X",
'''
    if old_log not in s:
        raise SystemExit("ERROR: V24 V13 stack log anchor changed")
    s = s.replace(old_log, new_log, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V24: stack VirtualQuery now returns the full contiguous stack region")
else:
    print("V24: stack VirtualQuery region fix already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "VirtualQuery V24 stack-region",
    "uint64_t base = lo;",
    "uint64_t region_size = hi - lo;",
):
    if token not in final:
        raise SystemExit("ERROR: V24 verification failed: " + token)

# The old stack-page semantics must be gone from the stack special case.
if "BaseAddress is the queried page, not AllocationBase" in final:
    raise SystemExit("ERROR: old V13 stack-page semantics survived V24")

print("MXXHUB_STACK_VIRTUALQUERY_REGION_FIX_V24_OK")
