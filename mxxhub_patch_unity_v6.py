#!/usr/bin/env python3
from pathlib import Path
import re
import sys

MARKER = "MXXHUB_UNITY_VIRTUALALLOC_FIX_V6"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v6.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
impl = wg / "Vendor/blink/wg_blink_impl.c"
bridge_c = wg / "Sources/Core/wg_blink_bridge.c"
bridge_h = wg / "Sources/Core/wg_blink_bridge.h"
engine_p = wg / "Sources/Core/wg_engine.c"
mapper_p = wg / "Sources/Win32/wg_dll_mapper.c"

for p in (impl, bridge_c, bridge_h, engine_p, mapper_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

def find_function(src: str, name: str):
    m = re.search(
        rf'(?m)^[ \t]*(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t\*]*\b'
        rf'{re.escape(name)}[ \t]*\([^;]*?\)[ \t]*\{{',
        src,
        re.S,
    )
    if not m:
        raise SystemExit(f"ERROR: could not locate C function {name}()")
    open_brace = src.find("{", m.start(), m.end())
    depth = 0
    i = open_brace
    in_str = in_chr = in_line = in_block = False
    esc = False
    while i < len(src):
        c = src[i]
        n = src[i + 1] if i + 1 < len(src) else ""
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "*" and n == "/":
                in_block = False
                i += 1
        elif in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif in_chr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == "'":
                in_chr = False
        else:
            if c == "/" and n == "/":
                in_line = True
                i += 1
            elif c == "/" and n == "*":
                in_block = True
                i += 1
            elif c == '"':
                in_str = True
            elif c == "'":
                in_chr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        i += 1
    raise SystemExit(f"ERROR: unterminated C function {name}()")

s = impl.read_text(encoding="utf-8")
if "WGBlinkVM_ReserveGuestV6" not in s:
    anchor = "int WGBlinkVM_GetV5Stage(void) { return s_mxx_v5_stage; }"
    if anchor not in s:
        raise SystemExit("ERROR: V5 direct PML4 helper missing; V6 must run after V5")
    extra = r'''
int WGBlinkVM_ReserveGuestV6(struct WGBlinkVM *vm,
                             unsigned long long addr,
                             unsigned long long size) {
    if (!vm) return 0;
    s_mxx_v5_stage = 0;
    s_mxx_v5_errno = 0;
    s_mxx_v5_level = 0;
    s_mxx_v5_page = addr & ~4095ULL;
    return mxx_v5_reserve_range(vm, addr, size);
}
'''
    s = s.replace(anchor, extra + "\n" + anchor, 1)
    impl.write_text(s, encoding="utf-8")
    print("V6: exported direct guest reserve helper")

b = bridge_c.read_text(encoding="utf-8")
if "WGBlinkVM_ReserveGuestV6" not in b:
    anchor = "extern int WGBlinkVM_GetV5Stage(void);\n"
    if anchor not in b:
        raise SystemExit("ERROR: V5 bridge declaration anchor missing")
    b = b.replace(
        anchor,
        "extern int WGBlinkVM_ReserveGuestV6(WGBlinkVM *vm, "
        "unsigned long long addr, unsigned long long size);\n" + anchor,
        1,
    )

if "wg_blink_reserve_memory_v6" not in b:
    insert_at, _ = find_function(b, "wg_blink_write_mem")
    fn = r'''bool wg_blink_reserve_memory_v6(WGBlinkInstance *inst,
                                  uint64_t addr, uint64_t size) {
    if (!inst || !inst->vm || !size) return false;

    sigjmp_buf recovery;
    wg_blink_set_abort_recovery(&recovery);
    if (sigsetjmp(recovery, 0)) {
        wg_blink_set_abort_recovery(NULL);
        WG_LOGE(TAG,
                "Blink reserve_memory V6 ABORT addr=0x%llx size=0x%llx "
                "stage=%d (%s) level=%d page=0x%llx errno=%d",
                (unsigned long long)addr, (unsigned long long)size,
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno());
        return false;
    }

    int ok = WGBlinkVM_ReserveGuestV6(inst->vm, addr, size);
    wg_blink_set_abort_recovery(NULL);
    if (!ok) {
        WG_LOGE(TAG,
                "Blink reserve_memory V6 FAILED addr=0x%llx size=0x%llx "
                "stage=%d (%s) level=%d page=0x%llx errno=%d",
                (unsigned long long)addr, (unsigned long long)size,
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno());
        return false;
    }
    return true;
}

'''
    b = b[:insert_at] + fn + b[insert_at:]

bridge_c.write_text(b, encoding="utf-8")

h = bridge_h.read_text(encoding="utf-8")
if "wg_blink_reserve_memory_v6" not in h:
    anchor = "bool wg_blink_write_mem(WGBlinkInstance *inst, uint64_t addr,\n"
    if anchor not in h:
        raise SystemExit("ERROR: Blink bridge header memory anchor changed")
    decl = (
        "bool wg_blink_reserve_memory_v6(WGBlinkInstance *inst, uint64_t addr,\n"
        "                                 uint64_t size);\n"
    )
    h = h.replace(anchor, decl + anchor, 1)
    bridge_h.write_text(h, encoding="utf-8")

m = mapper_p.read_text(encoding="utf-8")
if "VirtualProtect, 4" not in m:
    anchor = 'RS ("KERNEL32.dll", VirtualQuery, 3);'
    if anchor not in m:
        anchor = 'RS("KERNEL32.dll", VirtualQuery, 3);'
    if anchor not in m:
        raise SystemExit("ERROR: VirtualQuery registration anchor changed")
    m = m.replace(anchor, anchor + '\n    R1S("KERNEL32.dll", VirtualProtect, 4);', 1)
    mapper_p.write_text(m, encoding="utf-8")
    print("V6: registered VirtualProtect")

e = engine_p.read_text(encoding="utf-8")
if MARKER not in e:
    state_anchor = "static uint32_t s_heap_ptr = WG_GUEST_HEAP_BASE;\n"
    if state_anchor not in e:
        raise SystemExit("ERROR: guest heap state anchor changed")
    state = r'''/* MXXHUB_UNITY_VIRTUALALLOC_FIX_V6
 *
 * Unity reserves very large address ranges with VirtualAlloc(MEM_RESERVE),
 * then commits smaller pieces later. Returning NULL caused the V5 crash.
 */
#define MXX_MEM_COMMIT   0x00001000u
#define MXX_MEM_RESERVE  0x00002000u
#define MXX_MEM_DECOMMIT 0x00004000u
#define MXX_MEM_RELEASE  0x00008000u
#define MXX_MEM_FREE     0x00010000u
#define MXX_MEM_PRIVATE  0x00020000u
#define MXX_PAGE_READWRITE 0x00000004u
#define MXX_VA_MAX_REGIONS 64
#define MXX_VA_MAX_COMMITS 512

typedef struct {
    bool used;
    uint64_t base;
    uint64_t size;
    uint32_t protect;
} MxxVaRegion;

typedef struct {
    bool used;
    uint64_t base;
    uint64_t size;
    uint32_t protect;
} MxxVaCommit;

static MxxVaRegion s_mxx_va_regions[MXX_VA_MAX_REGIONS];
static MxxVaCommit s_mxx_va_commits[MXX_VA_MAX_COMMITS];
static uint64_t s_mxx_va_next = 0x80000000ULL;
/* V7: once the legacy low arena is full, x64 reservations continue in a
   canonical 64-bit Windows user VA range instead of failing at 0xE0000000. */
static uint64_t s_mxx_va_next64 = 0x0000001000000000ULL;
#define MXX_VA_LOW_TOP   0x00000000E0000000ULL
#define MXX_VA_64_TOP    0x00007FFF00000000ULL
#define MXX_VA_BIG_RESERVE (64ULL * 1024ULL * 1024ULL)

'''
    e = e.replace(state_anchor, state_anchor + state, 1)

    _, alloc_end = find_function(e, "wg_guest_alloc")
    helpers = r'''

static uint64_t mxx_align_down(uint64_t v, uint64_t a) {
    return v & ~(a - 1);
}
static uint64_t mxx_align_up(uint64_t v, uint64_t a) {
    if (v > ~(uint64_t)0 - (a - 1)) return 0;
    return (v + a - 1) & ~(a - 1);
}
static bool mxx_ranges_overlap(uint64_t a, uint64_t asz,
                               uint64_t b, uint64_t bsz) {
    if (!asz || !bsz) return false;
    if (a + asz < a || b + bsz < b) return true;
    return a < b + bsz && b < a + asz;
}
static MxxVaRegion *mxx_va_find_region(uint64_t addr, uint64_t size) {
    uint64_t end = addr + size;
    if (end < addr) return NULL;
    for (int i = 0; i < MXX_VA_MAX_REGIONS; ++i) {
        MxxVaRegion *r = &s_mxx_va_regions[i];
        if (!r->used) continue;
        if (addr >= r->base && end <= r->base + r->size) return r;
    }
    return NULL;
}
static bool mxx_va_range_free(uint64_t base, uint64_t size) {
    if (!size || base + size < base) return false;
    for (int i = 0; i < MXX_VA_MAX_REGIONS; ++i) {
        MxxVaRegion *r = &s_mxx_va_regions[i];
        if (r->used && mxx_ranges_overlap(base, size, r->base, r->size))
            return false;
    }
    return true;
}
static MxxVaRegion *mxx_va_add_region(uint64_t base, uint64_t size,
                                      uint32_t protect) {
    for (int i = 0; i < MXX_VA_MAX_REGIONS; ++i) {
        if (!s_mxx_va_regions[i].used) {
            s_mxx_va_regions[i].used = true;
            s_mxx_va_regions[i].base = base;
            s_mxx_va_regions[i].size = size;
            s_mxx_va_regions[i].protect = protect ? protect : MXX_PAGE_READWRITE;
            return &s_mxx_va_regions[i];
        }
    }
    return NULL;
}
static bool mxx_va_add_commit(uint64_t base, uint64_t size, uint32_t protect) {
    for (int i = 0; i < MXX_VA_MAX_COMMITS; ++i) {
        MxxVaCommit *c = &s_mxx_va_commits[i];
        if (c->used && base >= c->base && base + size <= c->base + c->size)
            return true;
    }
    for (int i = 0; i < MXX_VA_MAX_COMMITS; ++i) {
        if (!s_mxx_va_commits[i].used) {
            s_mxx_va_commits[i].used = true;
            s_mxx_va_commits[i].base = base;
            s_mxx_va_commits[i].size = size;
            s_mxx_va_commits[i].protect = protect ? protect : MXX_PAGE_READWRITE;
            return true;
        }
    }
    return false;
}
static MxxVaCommit *mxx_va_find_commit(uint64_t addr) {
    for (int i = 0; i < MXX_VA_MAX_COMMITS; ++i) {
        MxxVaCommit *c = &s_mxx_va_commits[i];
        if (c->used && addr >= c->base && addr < c->base + c->size)
            return c;
    }
    return NULL;
}

static uint64_t mxx_virtual_alloc(WGEngine *engine, uint64_t requested,
                                  uint64_t size, uint32_t type,
                                  uint32_t protect, bool is_32bit) {
    if (!engine || !engine->blink || !size) {
        s_last_error = 87;
        return 0;
    }

    const uint64_t page_size = 0x1000ULL;
    const uint64_t gran = 0x10000ULL;
    uint64_t page_count_size = mxx_align_up(size, page_size);
    if (!page_count_size) {
        s_last_error = 8;
        return 0;
    }

    bool want_reserve = (type & MXX_MEM_RESERVE) != 0;
    bool want_commit = (type & MXX_MEM_COMMIT) != 0;
    if (!want_reserve && !want_commit) {
        s_last_error = 87;
        return 0;
    }

    uint64_t base = requested;
    MxxVaRegion *region = NULL;

    if (want_reserve || (!requested && want_commit)) {
        uint64_t reserve_size = mxx_align_up(size, gran);
        if (!reserve_size) {
            s_last_error = 8;
            return 0;
        }

        bool used_high_arena = false;

        if (requested) {
            base = mxx_align_down(requested, gran);
            if (!mxx_va_range_free(base, reserve_size)) {
                WG_LOGE(TAG,
                        "VirtualAlloc V7 requested range busy: req=0x%llX "
                        "base=0x%llX size=0x%llX",
                        (unsigned long long)requested,
                        (unsigned long long)base,
                        (unsigned long long)reserve_size);
                s_last_error = 487;
                return 0;
            }
        } else {
            /*
             * V6 only searched below 0xE0000000. The device log showed the
             * low arena had reached about 0xC0010000, so Unity's 0x1FFFF000
             * (~512 MiB) MEM_RESERVE could never fit and returned NULL.
             *
             * Keep using the low arena for normal/small allocations, but for
             * x64 large reservations (or when low space is exhausted) move to
             * a separate canonical 64-bit arena.
             */
            bool try_high_first = !is_32bit &&
                                  reserve_size >= MXX_VA_BIG_RESERVE;

            if (!try_high_first) {
                base = mxx_align_up(s_mxx_va_next, gran);
                if (base) {
                    while (base + reserve_size >= base &&
                           base + reserve_size <= MXX_VA_LOW_TOP &&
                           !mxx_va_range_free(base, reserve_size)) {
                        base = mxx_align_up(base + gran, gran);
                    }
                }

                if (!base || base + reserve_size < base ||
                    base + reserve_size > MXX_VA_LOW_TOP) {
                    base = 0;
                }
            } else {
                base = 0;
            }

            if (!base && !is_32bit) {
                used_high_arena = true;
                base = mxx_align_up(s_mxx_va_next64, gran);
                if (!base) {
                    s_last_error = 8;
                    return 0;
                }
                while (base + reserve_size >= base &&
                       base + reserve_size <= MXX_VA_64_TOP &&
                       !mxx_va_range_free(base, reserve_size)) {
                    base = mxx_align_up(base + gran, gran);
                }
                if (base + reserve_size < base ||
                    base + reserve_size > MXX_VA_64_TOP) {
                    WG_LOGE(TAG,
                            "VirtualAlloc V7 x64 arena exhausted: "
                            "next=0x%llX size=0x%llX",
                            (unsigned long long)s_mxx_va_next64,
                            (unsigned long long)reserve_size);
                    s_last_error = 8;
                    return 0;
                }
            }

            if (!base) {
                WG_LOGE(TAG,
                        "VirtualAlloc V7 low arena exhausted: "
                        "next=0x%llX size=0x%llX is32=%d",
                        (unsigned long long)s_mxx_va_next,
                        (unsigned long long)reserve_size,
                        is_32bit ? 1 : 0);
                s_last_error = 8;
                return 0;
            }
        }

        region = mxx_va_add_region(base, reserve_size, protect);
        if (!region) {
            WG_LOGE(TAG, "VirtualAlloc V7 region table full");
            s_last_error = 8;
            return 0;
        }

        if (used_high_arena) {
            s_mxx_va_next64 = base + reserve_size;
        } else if (!requested && base < MXX_VA_LOW_TOP) {
            s_mxx_va_next = base + reserve_size;
        }
    } else {
        base = mxx_align_down(requested, page_size);
        region = mxx_va_find_region(base, page_count_size);
        if (!region) {
            s_last_error = 487;
            return 0;
        }
    }

    if (want_commit) {
        uint64_t target = requested ? requested : base;
        uint64_t commit_base = mxx_align_down(target, page_size);
        uint64_t commit_end = mxx_align_up(target + size, page_size);
        if (!commit_end || commit_end <= commit_base) {
            s_last_error = 8;
            return 0;
        }
        uint64_t commit_size = commit_end - commit_base;

        if (!mxx_va_find_region(commit_base, commit_size)) {
            s_last_error = 487;
            return 0;
        }
        if (!wg_blink_reserve_memory_v6(engine->blink, commit_base, commit_size)) {
            s_last_error = 8;
            return 0;
        }
        if (!mxx_va_add_commit(commit_base, commit_size, protect)) {
            s_last_error = 8;
            return 0;
        }
    }

    s_last_error = 0;
    WG_LOGI(TAG,
            "VirtualAlloc V7(req=0x%llX size=0x%llX type=0x%X prot=0x%X) "
            "-> 0x%llX%s%s",
            (unsigned long long)requested, (unsigned long long)size,
            type, protect, (unsigned long long)base,
            want_reserve ? " RESERVE" : "",
            want_commit ? " COMMIT" : "");
    return base;
}

static bool mxx_virtual_free(uint64_t addr, uint64_t size, uint32_t type) {
    if (type & MXX_MEM_RELEASE) {
        for (int i = 0; i < MXX_VA_MAX_REGIONS; ++i) {
            MxxVaRegion *r = &s_mxx_va_regions[i];
            if (r->used && r->base == addr) {
                uint64_t end = r->base + r->size;
                r->used = false;
                for (int j = 0; j < MXX_VA_MAX_COMMITS; ++j) {
                    MxxVaCommit *c = &s_mxx_va_commits[j];
                    if (c->used && c->base >= addr && c->base < end)
                        c->used = false;
                }
                s_last_error = 0;
                return true;
            }
        }
        s_last_error = 487;
        return false;
    }
    if (type & MXX_MEM_DECOMMIT) {
        for (int j = 0; j < MXX_VA_MAX_COMMITS; ++j) {
            MxxVaCommit *c = &s_mxx_va_commits[j];
            if (c->used && mxx_ranges_overlap(addr, size, c->base, c->size))
                c->used = false;
        }
        s_last_error = 0;
        return true;
    }
    s_last_error = 87;
    return false;
}

static uint64_t mxx_virtual_query(WGEngine *engine, uint64_t addr,
                                  uint64_t out, uint64_t out_len,
                                  bool is_32bit) {
    if (!engine || !engine->blink || !out) return 0;
    MxxVaRegion *r = mxx_va_find_region(addr, 1);
    MxxVaCommit *c = mxx_va_find_commit(addr);

    if (!is_32bit) {
        if (out_len < 48) return 0;
        uint8_t mbi[48] = {0};
        uint64_t base = r ? (c ? c->base : r->base) : mxx_align_down(addr, 0x1000);
        uint64_t alloc_base = r ? r->base : 0;
        uint32_t alloc_prot = r ? r->protect : 0;
        uint64_t region_size = r ? (c ? c->size : r->size) : 0x10000;
        uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
        uint32_t prot = c ? c->protect : 0;
        uint32_t type = r ? MXX_MEM_PRIVATE : 0;
        memcpy(mbi + 0,  &base,        8);
        memcpy(mbi + 8,  &alloc_base,  8);
        memcpy(mbi + 16, &alloc_prot,  4);
        memcpy(mbi + 24, &region_size, 8);
        memcpy(mbi + 32, &state,       4);
        memcpy(mbi + 36, &prot,        4);
        memcpy(mbi + 40, &type,        4);
        wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi);
        return sizeof mbi;
    }

    if (out_len < 28) return 0;
    uint8_t mbi[28] = {0};
    uint32_t base = (uint32_t)(r ? (c ? c->base : r->base) : mxx_align_down(addr, 0x1000));
    uint32_t alloc_base = (uint32_t)(r ? r->base : 0);
    uint32_t alloc_prot = r ? r->protect : 0;
    uint32_t region_size = (uint32_t)(r ? (c ? c->size : r->size) : 0x10000);
    uint32_t state = r ? (c ? MXX_MEM_COMMIT : MXX_MEM_RESERVE) : MXX_MEM_FREE;
    uint32_t prot = c ? c->protect : 0;
    uint32_t type = r ? MXX_MEM_PRIVATE : 0;
    memcpy(mbi + 0,  &base,        4);
    memcpy(mbi + 4,  &alloc_base,  4);
    memcpy(mbi + 8,  &alloc_prot,  4);
    memcpy(mbi + 12, &region_size, 4);
    memcpy(mbi + 16, &state,       4);
    memcpy(mbi + 20, &prot,        4);
    memcpy(mbi + 24, &type,        4);
    wg_blink_write_mem(engine->blink, out, mbi, sizeof mbi);
    return sizeof mbi;
}
'''
    e = e[:alloc_end] + helpers + e[alloc_end:]

    old_sys = '''        } else if (strcmp(fn, "GetSystemInfo") == 0 ||
                   strcmp(fn, "GetNativeSystemInfo") == 0) {
            // SYSTEM_INFO (36 bytes). Report 1 processor so apps that gate IOCP
            // on CPU count (Steam's BUseIOCP) use the synchronous socket path we
            // support. Must be a real handler — auto-stub (num_args=0) on this
            // 1-arg stdcall would corrupt the guest stack for the next call.
            if (args[0]) {
                uint8_t si[36] = {0};
                uint32_t v32;
                v32 = 4096;       memcpy(si + 4,  &v32, 4); // dwPageSize
                v32 = 0x00010000; memcpy(si + 8,  &v32, 4); // lpMinimumApplicationAddress
                v32 = 0x7FFE0000; memcpy(si + 12, &v32, 4); // lpMaximumApplicationAddress
                v32 = 1;          memcpy(si + 16, &v32, 4); // dwActiveProcessorMask
                v32 = 1;          memcpy(si + 20, &v32, 4); // dwNumberOfProcessors
                v32 = 586;        memcpy(si + 24, &v32, 4); // dwProcessorType (PROCESSOR_INTEL_PENTIUM)
                v32 = 0x00010000; memcpy(si + 28, &v32, 4); // dwAllocationGranularity
                uint16_t w16 = 6; memcpy(si + 32, &w16, 2); // wProcessorLevel
                wg_blink_write_mem(engine->blink, args[0], si, 36);
            }
            WG_LOGI(TAG, "%s -> 1 processor", fn);
            ret_val = 0;'''
    new_sys = '''        } else if (strcmp(fn, "GetSystemInfo") == 0 ||
                   strcmp(fn, "GetNativeSystemInfo") == 0) {
            if (args[0]) {
                if (is_32bit) {
                    uint8_t si[36] = {0};
                    uint16_t arch = 0;
                    uint32_t page = 4096, min = 0x00010000u, max = 0x7FFEFFFFu;
                    uint32_t mask = 1, ncpu = 1, type = 586, gran = 0x10000u;
                    uint16_t level = 6;
                    memcpy(si + 0,  &arch,  2);
                    memcpy(si + 4,  &page,  4);
                    memcpy(si + 8,  &min,   4);
                    memcpy(si + 12, &max,   4);
                    memcpy(si + 16, &mask,  4);
                    memcpy(si + 20, &ncpu,  4);
                    memcpy(si + 24, &type,  4);
                    memcpy(si + 28, &gran,  4);
                    memcpy(si + 32, &level, 2);
                    wg_blink_write_mem(engine->blink, args[0], si, sizeof si);
                } else {
                    uint8_t si[48] = {0};
                    uint16_t arch = 9;
                    uint32_t page = 4096, ncpu = 1, type = 8664, gran = 0x10000u;
                    uint64_t min = 0x0000000000010000ULL;
                    uint64_t max = 0x00007FFFFFFEFFFFULL;
                    uint64_t mask = 1;
                    uint16_t level = 6;
                    memcpy(si + 0,  &arch,  2);
                    memcpy(si + 4,  &page,  4);
                    memcpy(si + 8,  &min,   8);
                    memcpy(si + 16, &max,   8);
                    memcpy(si + 24, &mask,  8);
                    memcpy(si + 32, &ncpu,  4);
                    memcpy(si + 36, &type,  4);
                    memcpy(si + 40, &gran,  4);
                    memcpy(si + 44, &level, 2);
                    wg_blink_write_mem(engine->blink, args[0], si, sizeof si);
                }
            }
            WG_LOGI(TAG, "%s -> %s SYSTEM_INFO, page=4096 gran=65536",
                    fn, is_32bit ? "x86" : "x64");
            ret_val = 0;'''
    if old_sys in e:
        e = e.replace(old_sys, new_sys, 1)
        print("V6.1: upgraded legacy GetSystemInfo block to Win64 layout")
    else:
        # patch_hollow_knight_boot.py already upgrades this handler before V6
        # runs.  The old V6 helper expected the pre-HK text byte-for-byte and
        # aborted CI even though the handler was already correct.
        sys_start = e.find('} else if (strcmp(fn, "GetSystemInfo") == 0 ||')
        if sys_start < 0:
            raise SystemExit("ERROR: GetSystemInfo dispatch handler not found")
        sys_end = e.find('} else if (', sys_start + 10)
        if sys_end < 0:
            raise SystemExit("ERROR: could not locate end of GetSystemInfo handler")
        sys_block = e[sys_start:sys_end]
        if ('if (is_32bit)' in sys_block and
                'uint8_t si[36]' in sys_block and
                'uint8_t si[48]' in sys_block and
                ('uint16_t arch = 9' in sys_block or
                 'PROCESSOR_ARCHITECTURE_AMD64' in sys_block)):
            print("V6.1: GetSystemInfo already Win64-aware from HK boot patch; keeping it")
        else:
            # Unknown source layout: replace the entire dispatch branch
            # structurally rather than depending on comments/whitespace.
            replacement = new_sys + "\n"
            e = e[:sys_start] + replacement + e[sys_end:]
            print("V6.1: structurally replaced GetSystemInfo handler")

    va_anchor = '''        } else if (strcmp(fn, "GetProcessHeap") == 0) {
            ret_val = 0x00D00000;   // matches PEB->ProcessHeap in the TEB setup'''
    if va_anchor not in e:
        # Accept harmless comment/spacing changes around the same dispatch.
        gp = e.find('} else if (strcmp(fn, "GetProcessHeap") == 0) {')
        if gp < 0:
            raise SystemExit("ERROR: GetProcessHeap dispatch handler not found")
        line_end = e.find("\n", gp)
        next_else = e.find('} else if (', line_end)
        if next_else < 0:
            raise SystemExit("ERROR: could not locate end of GetProcessHeap handler")
        existing_gp = e[gp:next_else]
        va_anchor = existing_gp.rstrip()
        print("V6.1: using structural GetProcessHeap dispatch anchor")

    va_handlers = r'''        } else if (strcmp(fn, "VirtualAlloc") == 0) {
            ret_val = mxx_virtual_alloc(engine, args[0], args[1],
                                        (uint32_t)args[2], (uint32_t)args[3],
                                        is_32bit);
        } else if (strcmp(fn, "VirtualFree") == 0) {
            ret_val = mxx_virtual_free(args[0], args[1],
                                       (uint32_t)args[2]) ? 1 : 0;
            WG_LOGI(TAG, "VirtualFree V6(addr=0x%llX size=0x%llX type=0x%X) -> %llu",
                    (unsigned long long)args[0], (unsigned long long)args[1],
                    (uint32_t)args[2], (unsigned long long)ret_val);
        } else if (strcmp(fn, "VirtualProtect") == 0) {
            if (args[3]) {
                uint32_t oldp = MXX_PAGE_READWRITE;
                wg_blink_write_mem(engine->blink, args[3], &oldp, 4);
            }
            ret_val = 1;
        } else if (strcmp(fn, "VirtualQuery") == 0) {
            ret_val = mxx_virtual_query(engine, args[0], args[1], args[2], is_32bit);
        } else if (strcmp(fn, "GlobalMemoryStatusEx") == 0) {
            if (args[0]) {
                uint8_t ms[64] = {0};
                uint32_t len = 64, load = 25;
                uint64_t total_phys = 4ULL * 1024 * 1024 * 1024;
                uint64_t avail_phys = 3ULL * 1024 * 1024 * 1024;
                uint64_t total_pf   = 8ULL * 1024 * 1024 * 1024;
                uint64_t avail_pf   = 6ULL * 1024 * 1024 * 1024;
                uint64_t total_virt = is_32bit
                    ? 0x00000000FFF00000ULL
                    : 0x00007FFFFFFEFFFFULL;
                uint64_t avail_virt = total_virt - 0x10000000ULL;
                uint64_t ext = 0;
                memcpy(ms + 0,  &len,        4);
                memcpy(ms + 4,  &load,       4);
                memcpy(ms + 8,  &total_phys, 8);
                memcpy(ms + 16, &avail_phys, 8);
                memcpy(ms + 24, &total_pf,   8);
                memcpy(ms + 32, &avail_pf,   8);
                memcpy(ms + 40, &total_virt, 8);
                memcpy(ms + 48, &avail_virt, 8);
                memcpy(ms + 56, &ext,        8);
                wg_blink_write_mem(engine->blink, args[0], ms, sizeof ms);
            }
            ret_val = 1;
''' + va_anchor
    e = e.replace(va_anchor, va_handlers, 1)
    engine_p.write_text(e, encoding="utf-8")
    print("V6: patched Win64 SYSTEM_INFO + VirtualAlloc/Free/Query/Protect")

for token in ("WGBlinkVM_ReserveGuestV6", "mxx_v5_reserve_range(vm, addr, size)"):
    if token not in impl.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: V6 Blink impl verification failed: " + token)
for token in ("wg_blink_reserve_memory_v6", "Blink reserve_memory V6 FAILED"):
    if token not in bridge_c.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: V6 bridge verification failed: " + token)
if "wg_blink_reserve_memory_v6" not in bridge_h.read_text(encoding="utf-8"):
    raise SystemExit("ERROR: V6 bridge header declaration missing")
engine_verify = engine_p.read_text(encoding="utf-8")

# Verify capabilities, not one exact spelling of the SYSTEM_INFO declaration.
# patch_hollow_knight_boot.py writes:
#   uint16_t arch = 9, level = 6;
# while V6's own replacement writes:
#   uint16_t arch = 9;
# Both are valid Win64-aware handlers.
for token in (
    MARKER,
    "mxx_virtual_alloc",
    'strcmp(fn, "VirtualAlloc")',
    'strcmp(fn, "VirtualProtect")',
    'strcmp(fn, "GlobalMemoryStatusEx")',
):
    if token not in engine_verify:
        raise SystemExit("ERROR: V6 engine verification failed: " + token)

sys_start = engine_verify.find('strcmp(fn, "GetSystemInfo")')
if sys_start < 0:
    raise SystemExit("ERROR: V6 engine verification failed: GetSystemInfo handler missing")
sys_end = engine_verify.find('} else if (', sys_start + 10)
if sys_end < 0:
    sys_end = min(len(engine_verify), sys_start + 6000)
sys_verify = engine_verify[sys_start:sys_end]

required_sys = (
    'strcmp(fn, "GetNativeSystemInfo")',
    'if (is_32bit)',
    'uint8_t si[36]',
    'uint8_t si[48]',
    'wg_blink_write_mem',
)
for token in required_sys:
    if token not in sys_verify:
        raise SystemExit("ERROR: V6 SYSTEM_INFO verification failed: " + token)

if not (
    'uint16_t arch = 9' in sys_verify
    or 'PROCESSOR_ARCHITECTURE_AMD64' in sys_verify
):
    raise SystemExit(
        "ERROR: V6 SYSTEM_INFO verification failed: AMD64 architecture missing"
    )

print("V6.2: SYSTEM_INFO verification accepts existing HK Win64 layout")

engine_verify = engine_p.read_text(encoding="utf-8")
for token in (
    "MXX_VA_64_TOP",
    "s_mxx_va_next64",
    "VirtualAlloc V7",
    "MXX_VA_BIG_RESERVE",
    "is_32bit);",
):
    if token not in engine_verify:
        raise SystemExit("ERROR: V7 x64 VA arena verification failed: " + token)

print("MXXHUB_UNITY_64BIT_VA_ARENA_FIX_V7_OK")
if "VirtualProtect, 4" not in mapper_p.read_text(encoding="utf-8"):
    raise SystemExit("ERROR: VirtualProtect registration missing")

print("MXXHUB_UNITY_VIRTUALALLOC_FIX_V6_OK")
