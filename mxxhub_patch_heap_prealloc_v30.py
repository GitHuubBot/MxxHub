#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_heap_prealloc_v30.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_HEAP_PREALLOC_NO_HOST_REALLOC_V30"

def find_function(text: str, name: str):
    m = re.search(
        rf'(?m)^[ \t]*(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t\*]*\b'
        rf'{re.escape(name)}[ \t]*\([^;]*?\)[ \t]*\{{',
        text,
        re.S,
    )
    if not m:
        raise SystemExit(f"ERROR: cannot locate {name}()")
    brace = text.find("{", m.start(), m.end())
    depth = 0
    i = brace
    in_s = in_c = in_line = in_block = False
    esc = False
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if in_line:
            if c == "\n": in_line = False
        elif in_block:
            if c == "*" and n == "/":
                in_block = False
                i += 1
        elif in_s:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_s = False
        elif in_c:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == "'": in_c = False
        else:
            if c == "/" and n == "/":
                in_line = True
                i += 1
            elif c == "/" and n == "*":
                in_block = True
                i += 1
            elif c == '"':
                in_s = True
            elif c == "'":
                in_c = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        i += 1
    raise SystemExit(f"ERROR: unterminated {name}()")

if MARKER not in s:
    # ------------------------------------------------------------------
    # V30A — pre-map the guest heap before Unity/Mono enters lock-heavy code.
    #
    # V22 fixed the catastrophic 4K-per-small-allocation amplification, but
    # when the packed heap crosses a page boundary wg_guest_alloc still maps
    # more guest pages at that exact moment. The user's V30 request is to move
    # expensive allocation work out of critical sections. At the compatibility
    # layer we cannot move Unity's own malloc call, but we CAN move the backing
    # page creation out of the hot path.
    #
    # Precommit 16 MiB at PE startup. If more is needed later, grow in 1 MiB
    # chunks using a static BSS zero buffer — no host calloc/malloc in the heap
    # mapping path.
    # ------------------------------------------------------------------
    state_anchor = "static uint64_t s_mxx_heap_alloc_calls = 0;\n"
    if state_anchor not in s:
        raise SystemExit("ERROR: V30 V22 heap-state anchor changed")

    state_extra = r'''static uint64_t s_mxx_heap_alloc_calls = 0;

/* MXXHUB_HEAP_PREALLOC_NO_HOST_REALLOC_V30 */
#define MXXHUB_HEAP_PRECOMMIT_BYTES (16u * 1024u * 1024u)
#define MXXHUB_HEAP_GROW_CHUNK       (1u * 1024u * 1024u)

/* BSS-backed zero source: no host heap allocation is needed when extending the
 * guest heap. This is deliberately static rather than calloc()'d.
 */
static uint8_t s_mxx_heap_zero_1m[MXXHUB_HEAP_GROW_CHUNK];
static uint64_t s_mxx_v30_map_chunks = 0;
static uint64_t s_mxx_v30_realloc_inplace = 0;
static uint64_t s_mxx_v30_realloc_copies = 0;
'''
    s = s.replace(state_anchor, state_extra, 1)

    # Insert allocation-size update + mapping helpers after lookup_alloc_size().
    _, lookup_end = find_function(s, "lookup_alloc_size")
    helpers = r'''

static bool mxx_update_alloc_size(uint32_t addr, uint32_t size) {
    for (int i = s_alloc_count - 1; i >= 0; i--) {
        if (s_alloc_sizes[i].addr == addr) {
            s_alloc_sizes[i].size = size;
            return true;
        }
    }
    return false;
}

static bool mxx_heap_ensure_mapped(WGEngine *engine, uint32_t need_end) {
    if (!engine || !engine->blink) return false;
    if (need_end <= s_heap_mapped_end) return true;
    if (need_end > 0x7E000000u) return false;

    uint64_t target64 =
        ((uint64_t)need_end + MXXHUB_HEAP_GROW_CHUNK - 1ULL) &
        ~((uint64_t)MXXHUB_HEAP_GROW_CHUNK - 1ULL);
    if (target64 > 0x7E000000ULL) target64 = 0x7E000000ULL;
    if (target64 < need_end) return false;

    uint32_t target = (uint32_t)target64;
    while (s_heap_mapped_end < target) {
        uint32_t left = target - s_heap_mapped_end;
        uint32_t n = left > MXXHUB_HEAP_GROW_CHUNK
            ? MXXHUB_HEAP_GROW_CHUNK : left;

        if (!wg_blink_load_code(engine->blink, s_heap_mapped_end,
                                s_mxx_heap_zero_1m, n, 0)) {
            WG_LOGE(TAG,
                    "HEAP V30 MAP FAILED: from=0x%X bytes=%u target=0x%X",
                    s_heap_mapped_end, n, target);
            return false;
        }

        s_heap_mapped_end += n;
        s_mxx_v30_map_chunks++;
    }
    return true;
}

static bool mxx_heap_precommit(WGEngine *engine) {
    uint32_t target = WG_GUEST_HEAP_BASE + MXXHUB_HEAP_PRECOMMIT_BYTES;
    if (!mxx_heap_ensure_mapped(engine, target)) return false;

    WG_LOGI(TAG,
            "HEAP V30 PREALLOC: base=0x%X mapped=%uMiB grow=%uMiB "
            "host_realloc_tmp=0 mapped_end=0x%X",
            WG_GUEST_HEAP_BASE,
            MXXHUB_HEAP_PRECOMMIT_BYTES / (1024u * 1024u),
            MXXHUB_HEAP_GROW_CHUNK / (1024u * 1024u),
            s_heap_mapped_end);
    return true;
}
'''
    s = s[:lookup_end] + helpers + s[lookup_end:]

    # Replace the V22 on-demand calloc mapping block with the preallocated/
    # chunked helper. This removes host dynamic allocation from wg_guest_alloc.
    old_map = r'''    if (need_mapped_end > s_heap_mapped_end) {
        uint32_t map_from = s_heap_mapped_end;
        uint32_t map_size = need_mapped_end - map_from;
        uint8_t *zeros = (uint8_t *)calloc(1, map_size);
        if (!zeros) return 0;
        bool ok = wg_blink_load_code(engine->blink, map_from, zeros, map_size, 0);
        free(zeros);
        if (!ok) return 0;
        s_heap_mapped_end = need_mapped_end;
    }
'''
    new_map = r'''    if (need_mapped_end > s_heap_mapped_end) {
        if (!mxx_heap_ensure_mapped(engine, need_mapped_end)) return 0;
    }
'''
    if old_map not in s:
        raise SystemExit("ERROR: V30 V22 guest-heap mapping block changed")
    s = s.replace(old_map, new_map, 1)

    # ------------------------------------------------------------------
    # V30B — remove host malloc/free from HeapReAlloc/realloc copy paths.
    #
    # V16 correctly limited copy length, but still did malloc(copy_n) for every
    # reallocation. In a lock-heavy Unity/Mono loop that means another host heap
    # operation inside the thunk. Copy through a fixed 4K stack scratch buffer.
    #
    # Also keep shrink/same-size reallocations in place, and grow the most recent
    # bump allocation in place when safe.
    # ------------------------------------------------------------------
    old_heap = r'''        } else if (strcmp(fn, "HeapReAlloc") == 0) {
            // HeapReAlloc(hHeap, dwFlags, lpMem=args[2], dwBytes=args[3])
            uint32_t oldsz = lookup_alloc_size((uint32_t)args[2]);
            uint32_t newsz = (uint32_t)args[3];
            uint32_t np = wg_guest_alloc(engine, newsz);
            uint32_t copy_n = oldsz < newsz ? oldsz : newsz;
            if (np && args[2] && copy_n) {
                uint8_t *tmp = malloc(copy_n);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[2], tmp, copy_n);
                    wg_blink_write_mem(engine->blink, np, tmp, copy_n);
                    free(tmp);
                }
            }
            static int v16_realloc_logs = 0;
            if (v16_realloc_logs++ < 20) {
                WG_LOGI(TAG,
                        "HeapReAlloc V16(old=0x%llX oldsz=%u newsz=%u) "
                        "-> 0x%X copied=%u",
                        (unsigned long long)args[2], oldsz, newsz, np, copy_n);
            }
            ret_val = np;
'''
    new_heap = r'''        } else if (strcmp(fn, "HeapReAlloc") == 0) {
            // V30: no host malloc in the reallocation path.
            uint32_t oldp = (uint32_t)args[2];
            uint32_t oldsz = lookup_alloc_size(oldp);
            uint32_t newsz = (uint32_t)args[3];
            uint32_t np = 0;
            uint32_t copy_n = 0;

            if (oldp && oldsz && newsz > 0 && newsz <= oldsz) {
                /* Shrink / same size: Windows may keep the block. */
                mxx_update_alloc_size(oldp, newsz);
                np = oldp;
                s_mxx_v30_realloc_inplace++;
            } else if (oldp && oldsz && newsz > oldsz &&
                       (uint64_t)oldp + oldsz == (uint64_t)s_heap_ptr &&
                       (uint64_t)oldp + newsz < 0x7E000000ULL) {
                /* Most recent bump allocation can grow in place. */
                uint32_t new_end = oldp + newsz;
                uint32_t need_end = (new_end + 0xFFFu) & ~0xFFFu;
                if (need_end >= new_end &&
                    mxx_heap_ensure_mapped(engine, need_end)) {
                    uint8_t z[256] = {0};
                    uint32_t pos = oldp + oldsz;
                    uint32_t left = newsz - oldsz;
                    while (left) {
                        uint32_t n = left > sizeof(z)
                            ? (uint32_t)sizeof(z) : left;
                        if (!wg_blink_write_mem(engine->blink, pos, z, n)) {
                            np = 0;
                            break;
                        }
                        pos += n;
                        left -= n;
                    }
                    if (left == 0) {
                        s_heap_ptr = new_end;
                        mxx_update_alloc_size(oldp, newsz);
                        np = oldp;
                        s_mxx_v30_realloc_inplace++;
                    }
                }
            }

            if (!np) {
                np = wg_guest_alloc(engine, newsz);
                copy_n = oldsz < newsz ? oldsz : newsz;
                if (np && oldp && copy_n) {
                    uint8_t tmp[4096];
                    uint32_t done = 0;
                    while (done < copy_n) {
                        uint32_t n = copy_n - done;
                        if (n > sizeof(tmp)) n = (uint32_t)sizeof(tmp);
                        if (!wg_blink_read_mem(engine->blink,
                                               oldp + done, tmp, n) ||
                            !wg_blink_write_mem(engine->blink,
                                                np + done, tmp, n)) {
                            np = 0;
                            break;
                        }
                        done += n;
                    }
                    if (np) s_mxx_v30_realloc_copies++;
                }
            }

            static uint64_t v30_realloc_calls = 0;
            v30_realloc_calls++;
            if ((v30_realloc_calls & 0xFFFULL) == 0) {
                WG_LOGI(TAG,
                        "HEAP V30 REALLOC: calls=%llu inplace=%llu copies=%llu "
                        "heap=0x%X mapped_end=0x%X",
                        (unsigned long long)v30_realloc_calls,
                        (unsigned long long)s_mxx_v30_realloc_inplace,
                        (unsigned long long)s_mxx_v30_realloc_copies,
                        s_heap_ptr, s_heap_mapped_end);
            }
            ret_val = np;
'''
    if old_heap not in s:
        raise SystemExit("ERROR: V30 V16 HeapReAlloc block changed")
    s = s.replace(old_heap, new_heap, 1)

    old_realloc = r'''        } else if (strcmp(fn, "realloc") == 0) {
            // Preserve only bytes that actually belonged to the old block.
            uint32_t oldsz = lookup_alloc_size((uint32_t)args[0]);
            uint32_t newsz = (uint32_t)args[1];
            uint32_t np = wg_guest_alloc(engine, newsz);
            uint32_t copy_n = oldsz < newsz ? oldsz : newsz;
            if (np && args[0] && copy_n) {
                uint8_t *tmp = malloc(copy_n);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[0], tmp, copy_n);
                    wg_blink_write_mem(engine->blink, np, tmp, copy_n);
                    free(tmp);
                }
            }
            ret_val = np;
'''
    new_realloc = r'''        } else if (strcmp(fn, "realloc") == 0) {
            uint32_t oldp = (uint32_t)args[0];
            uint32_t oldsz = lookup_alloc_size(oldp);
            uint32_t newsz = (uint32_t)args[1];
            uint32_t np = 0;

            if (oldp && oldsz && newsz > 0 && newsz <= oldsz) {
                mxx_update_alloc_size(oldp, newsz);
                np = oldp;
                s_mxx_v30_realloc_inplace++;
            } else if (oldp && oldsz && newsz > oldsz &&
                       (uint64_t)oldp + oldsz == (uint64_t)s_heap_ptr &&
                       (uint64_t)oldp + newsz < 0x7E000000ULL) {
                uint32_t new_end = oldp + newsz;
                uint32_t need_end = (new_end + 0xFFFu) & ~0xFFFu;
                if (need_end >= new_end &&
                    mxx_heap_ensure_mapped(engine, need_end)) {
                    uint8_t z[256] = {0};
                    uint32_t pos = oldp + oldsz;
                    uint32_t left = newsz - oldsz;
                    while (left) {
                        uint32_t n = left > sizeof(z)
                            ? (uint32_t)sizeof(z) : left;
                        if (!wg_blink_write_mem(engine->blink, pos, z, n)) {
                            np = 0;
                            break;
                        }
                        pos += n;
                        left -= n;
                    }
                    if (left == 0) {
                        s_heap_ptr = new_end;
                        mxx_update_alloc_size(oldp, newsz);
                        np = oldp;
                        s_mxx_v30_realloc_inplace++;
                    }
                }
            }

            if (!np) {
                np = wg_guest_alloc(engine, newsz);
                uint32_t copy_n = oldsz < newsz ? oldsz : newsz;
                if (np && oldp && copy_n) {
                    uint8_t tmp[4096];
                    uint32_t done = 0;
                    while (done < copy_n) {
                        uint32_t n = copy_n - done;
                        if (n > sizeof(tmp)) n = (uint32_t)sizeof(tmp);
                        if (!wg_blink_read_mem(engine->blink,
                                               oldp + done, tmp, n) ||
                            !wg_blink_write_mem(engine->blink,
                                                np + done, tmp, n)) {
                            np = 0;
                            break;
                        }
                        done += n;
                    }
                    if (np) s_mxx_v30_realloc_copies++;
                }
            }
            ret_val = np;
'''
    if old_realloc not in s:
        raise SystemExit("ERROR: V30 V16 realloc block changed")
    s = s.replace(old_realloc, new_realloc, 1)

    # Precommit after the Blink VM and PE mappings have been established, but
    # before guest execution starts.
    #
    # V30.2: use the STRUCTURE of load_pe_blink(), not a log-message string.
    # The HK boot patch has changed the checkpoint text multiple times, so
    # anchoring against any exact checkpoint sentence is too fragile.
    #
    # We find the final `return true;` in load_pe_blink() and insert the heap
    # precommit immediately before it. That is the stable semantic point:
    # mappings/imports/stack/TEB are complete, guest execution has not begun.
    precommit_code = r"""    /* V30: move guest-heap backing allocation out of Unity/Mono lock loops. */
    if (!mxx_heap_precommit(engine)) {
        WG_LOGW(TAG,
                "HEAP V30 PREALLOC FAILED; continuing with 1MiB chunk growth");
    }

"""

    if "HEAP V30 PREALLOC FAILED; continuing with 1MiB chunk growth" not in s:
        fn_start, fn_end = find_function(s, "load_pe_blink")
        fn = s[fn_start:fn_end]

        final_ret = fn.rfind("    return true;")
        if final_ret < 0:
            raise SystemExit(
                "ERROR: V30.2 could not find final return true in load_pe_blink"
            )

        insert_at = fn_start + final_ret
        s = s[:insert_at] + precommit_code + s[insert_at:]
        print("V30.2: precommit inserted before final load_pe_blink return")
    else:
        print("V30.2: heap precommit call already inserted")

    # Reset V30 counters for each program.
    reset_anchor = '''    s_mxx_heap_alloc_calls = 0;
    s_alloc_count = 0;
'''
    reset_new = '''    s_mxx_heap_alloc_calls = 0;
    s_mxx_v30_map_chunks = 0;
    s_mxx_v30_realloc_inplace = 0;
    s_mxx_v30_realloc_copies = 0;
    s_alloc_count = 0;
'''
    if reset_anchor not in s:
        raise SystemExit("ERROR: V30 V22 reset anchor changed")
    s = s.replace(reset_anchor, reset_new, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V30: heap precommit + no-host-malloc realloc path installed")
else:
    print("V30: heap preallocation patch already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "HEAP V30 PREALLOC:",
    "HEAP V30 REALLOC:",
    "#define MXXHUB_HEAP_PRECOMMIT_BYTES (16u * 1024u * 1024u)",
    "static uint8_t s_mxx_heap_zero_1m",
    "mxx_heap_ensure_mapped",
    "uint8_t tmp[4096]",
    "mxx_update_alloc_size",
):
    if token not in final:
        raise SystemExit("ERROR: V30 verification failed: " + token)

# Critical regression checks: the guest allocator / realloc paths must not
# dynamically allocate a host copy/mapping buffer anymore.
a, b = find_function(final, "wg_guest_alloc")
guest_alloc = final[a:b]
if "calloc(" in guest_alloc or "malloc(" in guest_alloc:
    raise SystemExit("ERROR: host dynamic allocation survived in wg_guest_alloc")

heap_pos = final.find('strcmp(fn, "HeapReAlloc") == 0')
heap_end = final.find('} else if (strcmp(fn, "HeapFree")', heap_pos)
if heap_pos >= 0 and heap_end > heap_pos:
    heap_block = final[heap_pos:heap_end]
    if "malloc(" in heap_block or "calloc(" in heap_block:
        raise SystemExit("ERROR: host dynamic allocation survived in HeapReAlloc")

print("MXXHUB_HEAP_PREALLOC_NO_HOST_REALLOC_V30_OK")
print("MXXHUB_V30_1_PRECOMMIT_ANCHOR_FIX_OK")
print("MXXHUB_V30_2_FUNCTION_END_ANCHOR_FIX_OK")
