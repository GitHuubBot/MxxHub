#!/usr/bin/env python3
from pathlib import Path
import re
import sys

MARKER = "MXXHUB_BLINK_IOS_ANONMEM_FIX_V5"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_blink_v5.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
blink = wg.parent / "blink"
impl = wg / "Vendor/blink/wg_blink_impl.c"
bridge = wg / "Sources/Core/wg_blink_bridge.c"

if not impl.is_file() or not bridge.is_file():
    raise SystemExit(f"ERROR: WineGlass Blink sources not found under {wg}")
if not blink.is_dir():
    raise SystemExit(f"ERROR: Blink checkout not found beside WineGlass: {blink}")

def find_function(src: str, name: str):
    m = re.search(
        rf'(?m)^[ \t]*(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t\*]*\b'
        rf'{re.escape(name)}[ \t]*\([^;]*?\)[ \t]*\{{',
        src,
        re.S,
    )
    if not m:
        raise ValueError(f"could not locate C function {name}()")
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
    raise ValueError(f"unterminated C function {name}()")

def replace_function(src: str, name: str, replacement: str) -> str:
    a, b = find_function(src, name)
    return src[:a] + replacement.rstrip() + src[b:]

def find_function_file(root: Path, name: str):
    hits = []
    for f in root.glob("*.c"):
        try:
            txt = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            a, b = find_function(txt, name)
        except ValueError:
            continue
        hits.append((f, txt, a, b))
    if len(hits) != 1:
        raise SystemExit(
            f"ERROR: expected one {name} definition, found {len(hits)}: "
            + ", ".join(str(x[0]) for x in hits)
        )
    return hits[0]

# ---------------------------------------------------------------------------
# 1) iOS anonymous 4 KiB guest backing pages
# ---------------------------------------------------------------------------
anon_file, anon_text, anon_a, anon_b = find_function_file(
    blink / "blink", "AllocateAnonymousPage"
)

if MARKER not in anon_text:
    original = anon_text[anon_a:anon_b]
    renamed = re.sub(
        r'\bAllocateAnonymousPage\s*\(',
        'MxxUpstreamAllocateAnonymousPage(',
        original,
        count=1,
    )
    if renamed == original:
        raise SystemExit("ERROR: could not rename upstream AllocateAnonymousPage")

    wrapper = r'''/* MXXHUB_BLINK_IOS_ANONMEM_FIX_V5
 *
 * Full-virtualization Blink stores host-backed pages in g_hostpages.  iOS ARM64
 * may expose a 16 KiB host VM page while the x86 guest page is always 4 KiB.
 * With JIT disabled, guest data/code can safely live in normal zeroed 4 KiB
 * heap blocks; no executable mmap is needed for these backing pages.
 */
#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
unsigned char *g_mxx_ios_owned_hostpages;
size_t g_mxx_ios_owned_capacity;
static unsigned long long g_mxx_ios_anon_allocs;

static bool MxxEnsureIOSOwnedCapacity(size_t need) {
  size_t oldcap, newcap;
  unsigned char *p;
  if (need <= g_mxx_ios_owned_capacity) return true;
  oldcap = g_mxx_ios_owned_capacity;
  newcap = oldcap ? oldcap : 256;
  while (newcap < need) {
    if (newcap > SIZE_MAX / 2) return false;
    newcap *= 2;
  }
  p = (unsigned char *)realloc(g_mxx_ios_owned_hostpages, newcap);
  if (!p) return false;
  memset(p + oldcap, 0, newcap - oldcap);
  g_mxx_ios_owned_hostpages = p;
  g_mxx_ios_owned_capacity = newcap;
  return true;
}

static bool MxxEnsureHostRegistryCapacity(size_t need) {
  size_t newcap;
  u8 **p;
  if (need <= g_hostpages.c) return true;
  newcap = g_hostpages.c ? g_hostpages.c : 256;
  while (newcap < need) {
    if (newcap > SIZE_MAX / 2) return false;
    newcap *= 2;
  }
  p = (u8 **)realloc(g_hostpages.p, newcap * sizeof(*g_hostpages.p));
  if (!p) return false;
  memset(p + g_hostpages.c, 0,
         (newcap - g_hostpages.c) * sizeof(*g_hostpages.p));
  g_hostpages.p = p;
  g_hostpages.c = newcap;
  return true;
}

static u64 MxxAllocateIOSAnonymousPage(struct System *s) {
  size_t index;
  u8 *page;

  if (!s) {
    errno = EINVAL;
    return (u64)-1;
  }

  index = g_hostpages.n;
  if (index > ((size_t)PAGE_TA >> 12)) {
    errno = ENOMEM;
    return (u64)-1;
  }
  if (!MxxEnsureHostRegistryCapacity(index + 1) ||
      !MxxEnsureIOSOwnedCapacity(index + 1)) {
    errno = ENOMEM;
    return (u64)-1;
  }

  page = (u8 *)calloc(1, 4096);
  if (!page) {
    errno = ENOMEM;
    return (u64)-1;
  }

  g_hostpages.p[index] = page;
  g_mxx_ios_owned_hostpages[index] = 1;
  g_hostpages.n = index + 1;
  ++g_mxx_ios_anon_allocs;
  s->rss += 1;

  return ((u64)index << 12) | PAGE_HOST;
}
#endif

u64 AllocateAnonymousPage(struct System *s) {
#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
  return MxxAllocateIOSAnonymousPage(s);
#else
  return MxxUpstreamAllocateAnonymousPage(s);
#endif
}

unsigned long long MxxBlinkIOSAnonAllocCount(void) {
#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
  return g_mxx_ios_anon_allocs;
#else
  return 0;
#endif
}

'''
    anon_text = anon_text[:anon_a] + renamed + "\n\n" + wrapper + anon_text[anon_b:]
    anon_file.write_text(anon_text, encoding="utf-8")
    print(f"Patched iOS anonymous-page allocator in {anon_file}")
else:
    print("iOS anonymous-page allocator already patched")

free_file, free_text, free_a, free_b = find_function_file(
    blink / "blink", "FreeAnonymousPage"
)

if "MxxUpstreamFreeAnonymousPage" not in free_text:
    original = free_text[free_a:free_b]
    renamed = re.sub(
        r'\bFreeAnonymousPage\s*\(',
        'MxxUpstreamFreeAnonymousPage(',
        original,
        count=1,
    )
    if renamed == original:
        raise SystemExit("ERROR: could not rename upstream FreeAnonymousPage")

    free_wrapper = r'''#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
extern unsigned char *g_mxx_ios_owned_hostpages;
extern size_t g_mxx_ios_owned_capacity;
#endif

void FreeAnonymousPage(struct System *s, u8 *encoded) {
#if defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
  size_t index = (size_t)((uintptr_t)encoded >> 12);
  if (index < g_mxx_ios_owned_capacity &&
      g_mxx_ios_owned_hostpages[index]) {
    if (index < g_hostpages.n && g_hostpages.p[index]) {
      free(g_hostpages.p[index]);
      g_hostpages.p[index] = 0;
    }
    g_mxx_ios_owned_hostpages[index] = 0;
    return;
  }
#endif
  MxxUpstreamFreeAnonymousPage(s, encoded);
}

'''
    free_text = free_text[:free_a] + renamed + "\n\n" + free_wrapper + free_text[free_b:]
    free_file.write_text(free_text, encoding="utf-8")
    print(f"Patched iOS anonymous-page free path in {free_file}")
else:
    print("iOS anonymous-page free path already patched")

# ---------------------------------------------------------------------------
# 2) WineGlass embedded Blink: direct 4 KiB PML4 reservation
# ---------------------------------------------------------------------------
s = impl.read_text(encoding="utf-8")
if "#include <errno.h>" not in s:
    s = s.replace("#include <stdio.h>", "#include <stdio.h>\n#include <errno.h>", 1)

if "MXXHUB_BLINK_DIRECT_PML4_FIX_V5" not in s:
    insert_at, _ = find_function(s, "WGBlinkVM_LoadCode")

    support = r'''/* MXXHUB_BLINK_DIRECT_PML4_FIX_V5
 * ReserveVirtual() is the proven blocker in this iPhoneOS embedding.
 * Build the normal 4-level x86-64 page-table path directly. Leaves are lazy
 * PAGE_RSRV entries; Blink's own CopyToUser/HandlePageFault still commits them.
 */
extern unsigned long long MxxBlinkIOSAnonAllocCount(void);

static int s_mxx_v5_stage;
static int s_mxx_v5_errno;
static int s_mxx_v5_level;
static unsigned long long s_mxx_v5_page;

static void mxx_v5_fail(int stage, int level,
                        unsigned long long page, int err) {
    s_mxx_v5_stage = stage;
    s_mxx_v5_level = level;
    s_mxx_v5_page = page;
    s_mxx_v5_errno = err;
}

static int mxx_v5_reserve_page(struct WGBlinkVM *vm, u64 page) {
    struct System *s;
    u64 entry;
    int level;

    if (!vm || !(s = vm->s) || !vm->m || !s->cr3) {
        mxx_v5_fail(1, 0, page, EINVAL);
        return 0;
    }

    entry = s->cr3;

    for (level = 39; level > 12; level -= 9) {
        u8 *table = GetPageAddress(s, entry, level == 39);
        if (!table) {
            mxx_v5_fail(2, level, page, EFAULT);
            return 0;
        }

        u8 *slot = table + (((page >> level) & 511) * 8);
        u64 next = LoadPte(slot);

        if (!(next & PAGE_V)) {
            errno = 0;
            u64 child = AllocatePageTable(s);
            if (child == (u64)-1) {
                mxx_v5_fail(3, level, page, errno ? errno : ENOMEM);
                return 0;
            }
            next = (child & (PAGE_TA | PAGE_HOST)) |
                   PAGE_V | PAGE_RW | PAGE_U;
            StorePte(slot, next);
        } else if (next & PAGE_PS) {
            mxx_v5_fail(4, level, page, EEXIST);
            return 0;
        }

        entry = next;
    }

    u8 *pt = GetPageAddress(s, entry, false);
    if (!pt) {
        mxx_v5_fail(5, 12, page, EFAULT);
        return 0;
    }

    u8 *leaf_slot = pt + (((page >> 12) & 511) * 8);
    u64 leaf = LoadPte(leaf_slot);

    if (leaf & PAGE_V) {
        if ((leaf & (PAGE_U | PAGE_RW)) != (PAGE_U | PAGE_RW)) {
            mxx_v5_fail(6, 12, page, EACCES);
            return 0;
        }
        return 1;
    }

    StorePte(leaf_slot, PAGE_V | PAGE_RSRV | PAGE_U | PAGE_RW);
    ++s->memstat.reserved;
    s->vss += 1;
    return 1;
}

static int mxx_v5_reserve_range(struct WGBlinkVM *vm,
                                unsigned long long addr,
                                unsigned long long size) {
    if (!size) return 1;

    u64 a = (u64)addr;
    u64 n = (u64)size;
    if (a > ~(u64)0 - n) {
        mxx_v5_fail(7, 0, a, EOVERFLOW);
        return 0;
    }

    u64 begin = a & ~4095ULL;
    u64 raw_end = a + n;
    if (raw_end > ~(u64)0 - 4095ULL) {
        mxx_v5_fail(7, 0, a, EOVERFLOW);
        return 0;
    }
    u64 end = (raw_end + 4095ULL) & ~4095ULL;

    for (u64 p = begin; p < end; p += 4096ULL) {
        if (!mxx_v5_reserve_page(vm, p)) return 0;
    }

    ResetTlb(vm->m);
    return 1;
}

int WGBlinkVM_GetV5Stage(void) { return s_mxx_v5_stage; }
int WGBlinkVM_GetV5Errno(void) { return s_mxx_v5_errno; }
int WGBlinkVM_GetV5Level(void) { return s_mxx_v5_level; }
unsigned long long WGBlinkVM_GetV5Page(void) { return s_mxx_v5_page; }

long WGBlinkVM_GetV5Tables(struct WGBlinkVM *vm) {
    return (vm && vm->s) ? vm->s->memstat.tables : 0;
}
long WGBlinkVM_GetV5Reserved(struct WGBlinkVM *vm) {
    return (vm && vm->s) ? vm->s->memstat.reserved : 0;
}
long WGBlinkVM_GetV5Committed(struct WGBlinkVM *vm) {
    return (vm && vm->s) ? vm->s->memstat.committed : 0;
}
unsigned long long WGBlinkVM_GetV5AnonAllocs(void) {
    return MxxBlinkIOSAnonAllocCount();
}

const char *WGBlinkVM_GetV5Reason(void) {
    switch (s_mxx_v5_stage) {
        case 0: return "success";
        case 1: return "invalid VM/root page table";
        case 2: return "page-table address translation failed";
        case 3: return "AllocatePageTable failed";
        case 4: return "unexpected huge-page entry";
        case 5: return "leaf page-table translation failed";
        case 6: return "existing leaf permissions conflict";
        case 7: return "guest range overflow";
        case 20: return "CopyToUser / anonymous backing page failed";
        case 30: return "stack CopyToUser / anonymous backing page failed";
        default: return "unknown V5 mapping stage";
    }
}

'''
    s = s[:insert_at] + support + s[insert_at:]

    s = replace_function(s, "WGBlinkVM_LoadCode", r'''int WGBlinkVM_LoadCode(struct WGBlinkVM *vm, unsigned long long addr,
                        const void *code, unsigned int size,
                        unsigned long long entry_rip) {
    if (!vm) return 0;

    s_mxx_v5_stage = 0;
    s_mxx_v5_errno = 0;
    s_mxx_v5_level = 0;
    s_mxx_v5_page = addr & ~4095ULL;

    if (size) {
        if (!code) {
            mxx_v5_fail(1, 0, addr, EINVAL);
            return 0;
        }
        if (!mxx_v5_reserve_range(vm, addr, size)) return 0;

        errno = 0;
        if (CopyToUser(vm->m, addr, (void *)code, size) == -1) {
            mxx_v5_fail(20, 12, addr & ~4095ULL,
                        errno ? errno : EFAULT);
            return 0;
        }
    }

    if (entry_rip) vm->m->ip = entry_rip;
    s_mxx_v5_stage = 0;
    s_mxx_v5_errno = 0;
    return 1;
}''')

    s = replace_function(s, "WGBlinkVM_SetupStack", r'''int WGBlinkVM_SetupStack(struct WGBlinkVM *vm, unsigned long long entry_rip) {
    if (!vm) return 0;

    int is_32bit = (vm->m->mode.omode == XED_MODE_LEGACY ||
                    vm->m->mode.omode == XED_MODE_REAL);
    unsigned long long stack_base = 0x7FFF0000ULL;
    unsigned long long stack_size = 0x100000ULL;

    s_mxx_v5_stage = 0;
    s_mxx_v5_errno = 0;

    if (!mxx_v5_reserve_range(vm, stack_base - stack_size, stack_size)) {
        return 0;
    }

    if (is_32bit) {
        unsigned int sp = (unsigned int)(stack_base - 0x100);
        sp -= 4;
        unsigned int zero = 0;
        errno = 0;
        if (CopyToUser(vm->m, sp, &zero, 4) == -1) {
            mxx_v5_fail(30, 12, sp & ~4095ULL,
                        errno ? errno : EFAULT);
            return 0;
        }
        Put32(vm->m->sp, sp);
        Put32(vm->m->bp, sp + 4);
    } else {
        unsigned long long sp = stack_base - 0x100;
        sp -= 8;
        unsigned char zero[8] = {0};
        errno = 0;
        if (CopyToUser(vm->m, sp, zero, 8) == -1) {
            mxx_v5_fail(30, 12, sp & ~4095ULL,
                        errno ? errno : EFAULT);
            return 0;
        }
        Put64(vm->m->sp, sp);
        Put64(vm->m->bp, sp + 8);
    }

    vm->m->ip = entry_rip;
    s_mxx_v5_stage = 0;
    s_mxx_v5_errno = 0;
    return 1;
}''')

    s = replace_function(s, "WGBlinkVM_WriteMem", r'''int WGBlinkVM_WriteMem(struct WGBlinkVM *vm, unsigned long long addr,
                        const void *buf, unsigned int len) {
    if (!vm) return 0;
    if (!len) return 1;
    if (!buf) return 0;
    return CopyToUser(vm->m, addr, (void *)buf, len) == 0;
}''')

    s = replace_function(s, "WGBlinkVM_ReadMem", r'''int WGBlinkVM_ReadMem(struct WGBlinkVM *vm, unsigned long long addr,
                       void *buf, unsigned int len) {
    if (!vm) return 0;
    if (!len) return 1;
    if (!buf) return 0;
    return CopyFromUser(vm->m, buf, addr, len) == 0;
}''')

    impl.write_text(s, encoding="utf-8")
    print("Patched WineGlass Blink implementation: V5")
else:
    print("WineGlass Blink V5 implementation already patched")

# ---------------------------------------------------------------------------
# 3) Bridge diagnostics for both longjmp/Abort and ordinary return false
# ---------------------------------------------------------------------------
b = bridge.read_text(encoding="utf-8")

decls = '''extern int WGBlinkVM_GetV5Stage(void);
extern int WGBlinkVM_GetV5Errno(void);
extern int WGBlinkVM_GetV5Level(void);
extern unsigned long long WGBlinkVM_GetV5Page(void);
extern long WGBlinkVM_GetV5Tables(WGBlinkVM *vm);
extern long WGBlinkVM_GetV5Reserved(WGBlinkVM *vm);
extern long WGBlinkVM_GetV5Committed(WGBlinkVM *vm);
extern unsigned long long WGBlinkVM_GetV5AnonAllocs(void);
extern const char *WGBlinkVM_GetV5Reason(void);
'''

if "WGBlinkVM_GetV5Stage" not in b:
    anchor = "extern unsigned long long WGBlinkVM_GetFaultAddr(WGBlinkVM *vm);\n"
    if anchor not in b:
        raise SystemExit("ERROR: bridge declaration anchor changed")
    b = b.replace(anchor, anchor + decls, 1)

b = replace_function(b, "wg_blink_setup_stack", r'''bool wg_blink_setup_stack(WGBlinkInstance *inst, uint64_t entry_rip) {
    if (!inst || !inst->vm) return false;

    sigjmp_buf recovery;
    wg_blink_set_abort_recovery(&recovery);
    if (sigsetjmp(recovery, 0)) {
        wg_blink_set_abort_recovery(NULL);
        WG_LOGE(TAG,
                "Blink setup_stack ABORT V5: stage=%d (%s), level=%d, "
                "guest_page=0x%llx errno=%d tables=%ld reserved=%ld "
                "committed=%ld anon=%llu",
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno(), WGBlinkVM_GetV5Tables(inst->vm),
                WGBlinkVM_GetV5Reserved(inst->vm),
                WGBlinkVM_GetV5Committed(inst->vm),
                WGBlinkVM_GetV5AnonAllocs());
        return false;
    }

    int ok = WGBlinkVM_SetupStack(inst->vm, entry_rip);
    wg_blink_set_abort_recovery(NULL);
    if (!ok) {
        WG_LOGE(TAG,
                "Blink setup_stack FAILED V5: stage=%d (%s), level=%d, "
                "guest_page=0x%llx errno=%d tables=%ld reserved=%ld "
                "committed=%ld anon=%llu",
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno(), WGBlinkVM_GetV5Tables(inst->vm),
                WGBlinkVM_GetV5Reserved(inst->vm),
                WGBlinkVM_GetV5Committed(inst->vm),
                WGBlinkVM_GetV5AnonAllocs());
        return false;
    }

    WG_LOGI(TAG, "Blink stack ready V5: tables=%ld committed=%ld anon=%llu",
            WGBlinkVM_GetV5Tables(inst->vm),
            WGBlinkVM_GetV5Committed(inst->vm),
            WGBlinkVM_GetV5AnonAllocs());
    return true;
}''')

b = replace_function(b, "wg_blink_load_code", r'''bool wg_blink_load_code(WGBlinkInstance *inst, uint64_t addr,
                         const uint8_t *code, uint32_t size,
                         uint64_t entry_rip) {
    if (!inst || !inst->vm) return false;

    sigjmp_buf recovery;
    wg_blink_set_abort_recovery(&recovery);
    if (sigsetjmp(recovery, 0)) {
        wg_blink_set_abort_recovery(NULL);
        WG_LOGE(TAG,
                "Blink load_code ABORT V5 at 0x%llx: stage=%d (%s), level=%d, "
                "guest_page=0x%llx errno=%d tables=%ld reserved=%ld "
                "committed=%ld anon=%llu",
                (unsigned long long)addr,
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno(), WGBlinkVM_GetV5Tables(inst->vm),
                WGBlinkVM_GetV5Reserved(inst->vm),
                WGBlinkVM_GetV5Committed(inst->vm),
                WGBlinkVM_GetV5AnonAllocs());
        return false;
    }

    int ok = WGBlinkVM_LoadCode(inst->vm, addr, code, size, entry_rip);
    wg_blink_set_abort_recovery(NULL);

    if (!ok) {
        WG_LOGE(TAG,
                "Blink load_code FAILED V5 at 0x%llx: stage=%d (%s), level=%d, "
                "guest_page=0x%llx errno=%d tables=%ld reserved=%ld "
                "committed=%ld anon=%llu",
                (unsigned long long)addr,
                WGBlinkVM_GetV5Stage(), WGBlinkVM_GetV5Reason(),
                WGBlinkVM_GetV5Level(), WGBlinkVM_GetV5Page(),
                WGBlinkVM_GetV5Errno(), WGBlinkVM_GetV5Tables(inst->vm),
                WGBlinkVM_GetV5Reserved(inst->vm),
                WGBlinkVM_GetV5Committed(inst->vm),
                WGBlinkVM_GetV5AnonAllocs());
        return false;
    }

    WG_LOGI(TAG,
            "Loaded %u bytes at 0x%llx, entry 0x%llx "
            "(V5 tables=%ld committed=%ld anon=%llu)",
            size, (unsigned long long)addr,
            (unsigned long long)entry_rip,
            WGBlinkVM_GetV5Tables(inst->vm),
            WGBlinkVM_GetV5Committed(inst->vm),
            WGBlinkVM_GetV5AnonAllocs());
    return true;
}''')

try:
    b = replace_function(b, "wg_blink_has_jit", r'''bool wg_blink_has_jit(void) {
    return false;
}''')
except ValueError:
    pass

bridge.write_text(b, encoding="utf-8")

for token in (
    "MXXHUB_BLINK_DIRECT_PML4_FIX_V5",
    "mxx_v5_reserve_page",
    "WGBlinkVM_GetV5AnonAllocs",
):
    if token not in impl.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: V5 impl verification failed: " + token)

for token in (
    "Blink load_code FAILED V5",
    "Blink setup_stack FAILED V5",
    "WGBlinkVM_GetV5AnonAllocs",
):
    if token not in bridge.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: V5 bridge verification failed: " + token)

print("MXXHUB_BLINK_IOS_ANONMEM_FIX_V5_OK")
