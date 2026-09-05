#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_heap_v22.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_PACKED_GUEST_HEAP_FIX_V22"

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
    state_anchor = "static uint32_t s_heap_ptr = WG_GUEST_HEAP_BASE;\n"
    if state_anchor not in s:
        raise SystemExit("ERROR: V22 heap state anchor changed")

    state = r'''static uint32_t s_heap_ptr = WG_GUEST_HEAP_BASE;

/* MXXHUB_PACKED_GUEST_HEAP_FIX_V22
 * Byte-granular bump position plus page-granular mapped end.
 */
static uint32_t s_heap_mapped_end = WG_GUEST_HEAP_BASE;
static uint64_t s_mxx_heap_requested = 0;
static uint64_t s_mxx_heap_naive_pages = 0;
static uint64_t s_mxx_heap_alloc_calls = 0;
'''
    s = s.replace(state_anchor, state, 1)

    s = s.replace("#define WG_MAX_ALLOCS 8192", "#define WG_MAX_ALLOCS 131072", 1)

    a, b = find_function(s, "wg_guest_alloc")
    replacement = r'''static uint32_t wg_guest_alloc(WGEngine *engine, uint32_t size) {
    /* MXXHUB_PACKED_GUEST_HEAP_FIX_V22 */
    if (!engine || !engine->blink) return 0;
    if (size == 0) size = 1;
    if ((size & 0x80000000u) || size > 512u * 1024u * 1024u) return 0;

    uint64_t addr64 = ((uint64_t)s_heap_ptr + 15ULL) & ~15ULL;
    uint64_t end64 = addr64 + (uint64_t)size;
    if (end64 > 0x7E000000ULL || end64 <= addr64) return 0;

    uint32_t addr = (uint32_t)addr64;
    uint32_t end = (uint32_t)end64;
    uint32_t need_mapped_end = (end + 0xFFFu) & ~0xFFFu;
    if (need_mapped_end < end) return 0;

    if (need_mapped_end > s_heap_mapped_end) {
        uint32_t map_from = s_heap_mapped_end;
        uint32_t map_size = need_mapped_end - map_from;
        uint8_t *zeros = (uint8_t *)calloc(1, map_size);
        if (!zeros) return 0;
        bool ok = wg_blink_load_code(engine->blink, map_from, zeros, map_size, 0);
        free(zeros);
        if (!ok) return 0;
        s_heap_mapped_end = need_mapped_end;
    }

    uint8_t zero_chunk[256] = {0};
    uint32_t left = size;
    uint32_t pos = addr;
    while (left) {
        uint32_t n = left > sizeof(zero_chunk) ? (uint32_t)sizeof(zero_chunk) : left;
        if (!wg_blink_write_mem(engine->blink, pos, zero_chunk, n)) return 0;
        pos += n;
        left -= n;
    }

    s_heap_ptr = end;
    track_alloc(addr, size);

    s_mxx_heap_alloc_calls++;
    s_mxx_heap_requested += size;
    s_mxx_heap_naive_pages += ((uint64_t)size + 0xFFFULL) >> 12;

    if ((s_mxx_heap_alloc_calls & 0xFFFULL) == 0) {
        uint64_t packed_pages =
            ((uint64_t)s_heap_mapped_end - WG_GUEST_HEAP_BASE) >> 12;
        WG_LOGI(TAG,
                "HEAP V22 PACKED: calls=%llu requested=%lluB "
                "packed_pages=%llu old_page_per_alloc_est=%llu",
                (unsigned long long)s_mxx_heap_alloc_calls,
                (unsigned long long)s_mxx_heap_requested,
                (unsigned long long)packed_pages,
                (unsigned long long)s_mxx_heap_naive_pages);
    }

    return addr;
}'''
    s = s[:a] + replacement + s[b:]

    old_argv = r'''            // Allocate guest memory: argv[0] pointer (4 bytes) + string data
            uint32_t base = s_heap_ptr;
            uint32_t str_off = base + 4; // argv[0] string right after pointer
            uint32_t str_bytes = (len + 1) * 2;
            uint32_t total = 4 + str_bytes;
            total = (total + 0xFFF) & ~0xFFFu;
            uint8_t *buf = calloc(1, total);
            if (buf) {
                // argv[0] = pointer to the string
                uint32_t str_addr = str_off;
                memcpy(buf, &str_addr, 4);
                memcpy(buf + 4, cmdw, str_bytes);
                wg_blink_load_code(engine->blink, base, buf, total, 0);
                free(buf);
                s_heap_ptr += total;
                // Write argc = 1
                if (args[1]) {
                    uint32_t one = 1;
                    wg_blink_write_mem(engine->blink, args[1], &one, 4);
                }
                ret_val = base;
            }
'''
    new_argv = r'''            // V22: packed guest allocation for argv[0] pointer + string.
            uint32_t str_bytes = (len + 1) * 2;
            uint32_t total = 4 + str_bytes;
            uint32_t base = wg_guest_alloc(engine, total);
            if (base) {
                uint32_t str_off = base + 4;
                uint32_t str_addr = str_off;
                wg_blink_write_mem(engine->blink, base, &str_addr, 4);
                wg_blink_write_mem(engine->blink, str_off, cmdw, str_bytes);
                if (args[1]) {
                    uint32_t one = 1;
                    wg_blink_write_mem(engine->blink, args[1], &one, 4);
                }
                ret_val = base;
            }
'''
    if old_argv in s:
        s = s.replace(old_argv, new_argv, 1)

    old_global = r'''            } else {
                size = (size + 0xFFF) & ~0xFFF;
                uint32_t addr = s_heap_ptr;
                uint8_t *zeros = calloc(1, size);
                if (zeros) {
                    wg_blink_load_code(engine->blink, addr, zeros, size, 0);
                    free(zeros);
                    s_heap_ptr += size;
                    s_heap_ptr = (s_heap_ptr + 0xFFF) & ~0xFFF;
                    ret_val = addr;
                }
            }
'''
    new_global = r'''            } else {
                ret_val = wg_guest_alloc(engine, size ? size : 1);
            }
'''
    if old_global in s:
        s = s.replace(old_global, new_global, 1)

    reset_anchor = "    s_heap_ptr = WG_GUEST_HEAP_BASE;\n"
    idx = s.rfind(reset_anchor)
    if idx < 0:
        raise SystemExit("ERROR: V22 run reset anchor changed")
    reset = r'''    s_heap_ptr = WG_GUEST_HEAP_BASE;
    s_heap_mapped_end = WG_GUEST_HEAP_BASE;
    s_mxx_heap_requested = 0;
    s_mxx_heap_naive_pages = 0;
    s_mxx_heap_alloc_calls = 0;
    s_alloc_count = 0;
'''
    s = s[:idx] + s[idx:].replace(reset_anchor, reset, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V22: packed guest heap installed")
else:
    print("V22: packed guest heap already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "HEAP V22 PACKED:",
    "static uint32_t s_heap_mapped_end",
    "#define WG_MAX_ALLOCS 131072",
    "s_heap_mapped_end = WG_GUEST_HEAP_BASE;",
):
    if token not in final:
        raise SystemExit("ERROR: V22 verification failed: " + token)

if "uint32_t alloc = (size + 0xFFF) & ~0xFFFu;" in final:
    raise SystemExit("ERROR: old page-per-allocation wg_guest_alloc survived V22")

print("MXXHUB_PACKED_GUEST_HEAP_FIX_V22_OK")
