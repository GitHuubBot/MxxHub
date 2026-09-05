#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unwind_tables_v38.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
p = wg / "Sources/Core/wg_engine.c"
if not p.is_file():
    raise SystemExit(f"ERROR: missing {p}")

s = p.read_text(encoding="utf-8")
MARKER = "MXXHUB_WINDOWS_V38_GROWABLE_FUNCTION_TABLES"

if MARKER not in s:
    globals_anchor = "static uint32_t s_last_error = 0;\n"
    globals_block = '''static uint32_t s_last_error = 0;

/* MXXHUB_WINDOWS_V38_GROWABLE_FUNCTION_TABLES
 * Win64 Mono dynamically resolves the Windows 8+ growable function-table API
 * before publishing JIT code. The generic WineGlass auto-stub returned
 * STATUS_SUCCESS (0) from RtlAddGrowableFunctionTable but never wrote the
 * required opaque DynamicTable output handle. Mono then called Grow/Delete
 * with handle 0, violating the Windows API contract and destabilizing its JIT
 * metadata/unwind bookkeeping.
 *
 * iOS cannot register Windows x64 unwind metadata with the host kernel, but
 * the guest still needs coherent Windows-visible API semantics. Keep a tiny
 * opaque-handle registry so Add -> Grow -> Delete is internally consistent.
 */
#define WG_V38_MAX_UNWIND_TABLES 64
#define WG_V38_UNWIND_HANDLE_BASE 0xE3800000ULL
typedef struct {
    bool in_use;
    uint64_t handle;
    uint64_t function_table;
    uint64_t range_base;
    uint64_t range_end;
    uint32_t entry_count;
    uint32_t max_entry_count;
} WGV38UnwindTable;
static WGV38UnwindTable s_v38_unwind[WG_V38_MAX_UNWIND_TABLES];
static uint64_t s_v38_unwind_generation = 1;

static WGV38UnwindTable *v38_find_unwind(uint64_t handle) {
    if (!handle) return NULL;
    for (int i = 0; i < WG_V38_MAX_UNWIND_TABLES; i++) {
        if (s_v38_unwind[i].in_use && s_v38_unwind[i].handle == handle)
            return &s_v38_unwind[i];
    }
    return NULL;
}
'''
    if globals_anchor not in s:
        raise SystemExit("ERROR: V38 globals anchor changed")
    s = s.replace(globals_anchor, globals_block, 1)

    # V38.1 anchor hardening: do not assume nsDialogs is immediately after fn.
    # Earlier patches can insert bookkeeping between these two stable points.
    fn_anchor = 'const char *fn = entry->func_name;'
    if s.count(fn_anchor) != 1:
        raise SystemExit(f"ERROR: V38.1 func-name anchor count changed: {s.count(fn_anchor)}")

    dispatch_anchor = '        if (entry->dll_name && strcasecmp(entry->dll_name, "nsDialogs.dll") == 0) {'
    dispatch_new = '''        if (strcmp(fn, "RtlAddGrowableFunctionTable") == 0) {
            // DWORD RtlAddGrowableFunctionTable(PVOID *DynamicTable,
            //   PRUNTIME_FUNCTION FunctionTable, DWORD EntryCount,
            //   DWORD MaximumEntryCount, ULONG_PTR RangeBase, ULONG_PTR RangeEnd)
            uint64_t out_ptr = args[0];
            int slot = -1;
            for (int i = 0; i < WG_V38_MAX_UNWIND_TABLES; i++) {
                if (!s_v38_unwind[i].in_use) { slot = i; break; }
            }
            if (!out_ptr || slot < 0) {
                ret_val = slot < 0 ? 0xC000009AULL : 0xC000000DULL;
                WG_LOGW(TAG, "UNWIND V38 ADD-GROW FAILED: out=0x%llX slot=%d status=0x%llX",
                        (unsigned long long)out_ptr, slot,
                        (unsigned long long)ret_val);
            } else {
                WGV38UnwindTable *t = &s_v38_unwind[slot];
                memset(t, 0, sizeof(*t));
                t->in_use = true;
                t->handle = WG_V38_UNWIND_HANDLE_BASE +
                            ((s_v38_unwind_generation++ & 0xFFFFFULL) << 4) +
                            (uint64_t)slot;
                t->function_table = args[1];
                t->entry_count = (uint32_t)args[2];
                t->max_entry_count = (uint32_t)args[3];
                t->range_base = args[4];
                t->range_end = args[5];

                bool wrote = false;
                if (is_32bit) {
                    uint32_t h32 = (uint32_t)t->handle;
                    wrote = wg_blink_write_mem(engine->blink, out_ptr, &h32, 4);
                } else {
                    uint64_t h64 = t->handle;
                    wrote = wg_blink_write_mem(engine->blink, out_ptr, &h64, 8);
                }
                if (!wrote) {
                    memset(t, 0, sizeof(*t));
                    ret_val = 0xC0000005ULL; // STATUS_ACCESS_VIOLATION
                } else {
                    ret_val = 0; // STATUS_SUCCESS
                    WG_LOGI(TAG,
                            "UNWIND V38 ADD-GROW: out=0x%llX handle=0x%llX table=0x%llX entries=%u max=%u range=0x%llX-0x%llX",
                            (unsigned long long)out_ptr,
                            (unsigned long long)t->handle,
                            (unsigned long long)t->function_table,
                            t->entry_count, t->max_entry_count,
                            (unsigned long long)t->range_base,
                            (unsigned long long)t->range_end);
                }
            }
        } else if (strcmp(fn, "RtlGrowFunctionTable") == 0) {
            WGV38UnwindTable *t = v38_find_unwind(args[0]);
            if (t) {
                t->entry_count = (uint32_t)args[1];
                if (t->entry_count == 1 || (t->entry_count & 63u) == 0) {
                    WG_LOGD(TAG, "UNWIND V38 GROW: handle=0x%llX entries=%u",
                            (unsigned long long)t->handle, t->entry_count);
                }
            } else {
                WG_LOGW(TAG, "UNWIND V38 GROW unknown handle=0x%llX entries=%u",
                        (unsigned long long)args[0], (uint32_t)args[1]);
            }
            ret_val = 0; // VOID function; RAX is ignored
        } else if (strcmp(fn, "RtlDeleteGrowableFunctionTable") == 0) {
            WGV38UnwindTable *t = v38_find_unwind(args[0]);
            if (t) {
                WG_LOGD(TAG, "UNWIND V38 DELETE-GROW: handle=0x%llX entries=%u",
                        (unsigned long long)t->handle, t->entry_count);
                memset(t, 0, sizeof(*t));
            } else if (args[0]) {
                WG_LOGW(TAG, "UNWIND V38 DELETE-GROW unknown handle=0x%llX",
                        (unsigned long long)args[0]);
            }
            ret_val = 0; // VOID function; RAX is ignored
        } else if (strcmp(fn, "RtlAddFunctionTable") == 0) {
            ret_val = 1; // BOOLEAN TRUE
        } else if (strcmp(fn, "RtlDeleteFunctionTable") == 0) {
            ret_val = 1; // BOOLEAN TRUE
        } else if (entry->dll_name && strcasecmp(entry->dll_name, "nsDialogs.dll") == 0) {'''
    if s.count(dispatch_anchor) != 1:
        raise SystemExit(f"ERROR: V38.1 nsDialogs dispatch anchor count changed: {s.count(dispatch_anchor)}")
    s = s.replace(dispatch_anchor, dispatch_new, 1)
    p.write_text(s, encoding="utf-8")
    print("V38: Win64 growable function-table output handles implemented")
else:
    print("V38: growable function-table patch already present")

final = p.read_text(encoding="utf-8")
for token in (
    MARKER,
    'strcmp(fn, "RtlAddGrowableFunctionTable") == 0',
    'strcmp(fn, "RtlGrowFunctionTable") == 0',
    'strcmp(fn, "RtlDeleteGrowableFunctionTable") == 0',
    'UNWIND V38 ADD-GROW:',
    'wg_blink_write_mem(engine->blink, out_ptr, &h64, 8)',
):
    if token not in final:
        raise SystemExit("ERROR: V38 verification failed: " + token)

print("MXXHUB_WINDOWS_V38_GROWABLE_FUNCTION_TABLES_OK")
print("MXXHUB_V38_1_DISPATCH_ANCHOR_HOTFIX_OK")
