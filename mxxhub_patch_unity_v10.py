#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_UNITY_JOB_SYSTEM_FIX_V10"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v10.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
mapper_p = wg / "Sources/Win32/wg_dll_mapper.c"

for p in (engine_p, mapper_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

# ---------------------------------------------------------------------------
# 1) Register the Win64 APIs Unity is calling immediately before it drops into
#    its crash / diagnostic path. Without registrations they are auto-stubbed
#    with a zero return.
# ---------------------------------------------------------------------------
m = mapper_p.read_text(encoding="utf-8")

if MARKER not in m:
    import re

    regs = r'''    /* MXXHUB_UNITY_JOB_SYSTEM_FIX_V10
     * Unity 6000 job-system / topology startup.
     */
    RS ("KERNEL32.dll", GetLogicalProcessorInformationEx, 3);
    RS ("KERNEL32.dll", GetActiveProcessorCount, 1);
    RS ("KERNEL32.dll", GetActiveProcessorGroupCount, 0);
    RS ("KERNEL32.dll", GetMaximumProcessorCount, 1);
    RS ("KERNEL32.dll", GetMaximumProcessorGroupCount, 0);
    RS ("KERNEL32.dll", CreateSemaphoreExW, 6);
    RS ("KERNEL32.dll", CreateSemaphoreExA, 6);
    RS ("KERNEL32.dll", Module32First, 2);
    RS ("KERNEL32.dll", Module32Next, 2);
    RS ("KERNEL32.dll", Module32FirstW, 2);
    RS ("KERNEL32.dll", Module32NextW, 2);
    RS ("KERNEL32.dll", Thread32First, 2);
    RS ("KERNEL32.dll", Thread32Next, 2);
    RS ("KERNEL32.dll", GetProcessId, 1);

'''

    # V10.1: structural insertion. The pinned mapper mixes RS("...") and
    # RS ("...") formatting, so exact whitespace matching is invalid.
    native_re = re.compile(
        r'(?m)^(?P<line>\s*RS\s*\(\s*"KERNEL32\.dll"\s*,\s*'
        r'GetNativeSystemInfo\s*,\s*1\s*\)\s*;\s*\n)'
    )
    match = native_re.search(m)
    if not match:
        raise SystemExit(
            "ERROR: V10.1 could not locate GetNativeSystemInfo registration structurally"
        )
    m = m[:match.end()] + regs + m[match.end():]
    print("V10.1: inserted job-system registrations after GetNativeSystemInfo")

    psapi_regs = r'''    /* PSAPI is loaded dynamically by Unity's diagnostic layer. */
    RS ("PSAPI.dll", EnumProcessModules, 4);
    RS ("PSAPI.dll", EnumProcessModulesEx, 5);
    RS ("PSAPI.dll", GetModuleBaseNameA, 4);
    RS ("PSAPI.dll", GetModuleBaseNameW, 4);
    RS ("PSAPI.dll", GetModuleFileNameExA, 4);
    RS ("PSAPI.dll", GetModuleFileNameExW, 4);
    RS ("PSAPI.dll", GetModuleInformation, 4);
    RS ("PSAPI.dll", GetProcessMemoryInfo, 3);

'''

    rtl_re = re.compile(
        r'(?m)^(?P<line>\s*R1S\s*\(\s*"ADVAPI32\.dll"\s*,\s*'
        r'RtlGenRandom\s*,\s*2\s*\)\s*;\s*\n)'
    )
    match = rtl_re.search(m)
    if not match:
        # Fallback: place PSAPI registrations just before the vcruntime/ucrt
        # section rather than failing because a harmless nearby anchor moved.
        crt = m.find("    // === vcruntime / ucrt ===")
        if crt < 0:
            raise SystemExit(
                "ERROR: V10.1 could not locate RtlGenRandom or CRT section for PSAPI insertion"
            )
        m = m[:crt] + psapi_regs + m[crt:]
        print("V10.1: inserted PSAPI registrations before CRT section")
    else:
        m = m[:match.end()] + psapi_regs + m[match.end():]
        print("V10.1: inserted PSAPI registrations after RtlGenRandom")

    mapper_p.write_text(m, encoding="utf-8")
    print("V10.1: registered Unity processor/semaphore + PSAPI APIs")
else:
    print("V10.1: mapper registrations already present")

# ---------------------------------------------------------------------------
# 2) Implement processor-group discovery and semaphore objects.
# ---------------------------------------------------------------------------
e = engine_p.read_text(encoding="utf-8")

if MARKER not in e:
    state_anchor = '''static uint32_t s_event_next = 0;
'''
    if state_anchor not in e:
        raise SystemExit("ERROR: event state anchor changed before V10")

    sem_state = r'''static uint32_t s_event_next = 0;

/* MXXHUB_UNITY_JOB_SYSTEM_FIX_V10
 * A tiny counting-semaphore model for Unity's job system. Event handles live
 * at 0x200..0x2FF, so keep semaphores in a separate range.
 */
#define MXX_SEM_BASE 0x600u
#define MXX_MAX_SEMS 128
static int32_t s_mxx_sem_count[MXX_MAX_SEMS];
static int32_t s_mxx_sem_max[MXX_MAX_SEMS];
static uint32_t s_mxx_sem_next = 0;

static bool mxx_sem_valid(uint32_t h) {
    return h >= MXX_SEM_BASE &&
           h < MXX_SEM_BASE + s_mxx_sem_next &&
           (h - MXX_SEM_BASE) < MXX_MAX_SEMS;
}

static bool mxx_sem_signalled(uint32_t h) {
    return mxx_sem_valid(h) &&
           s_mxx_sem_count[h - MXX_SEM_BASE] > 0;
}

static void mxx_sem_consume(uint32_t h) {
    if (mxx_sem_valid(h)) {
        uint32_t i = h - MXX_SEM_BASE;
        if (s_mxx_sem_count[i] > 0) s_mxx_sem_count[i]--;
    }
}
'''
    e = e.replace(state_anchor, sem_state, 1)

    # Processor topology comes before GetCurrentThread in the current engine
    # dispatch, which is a stable anchor in the pinned source and HK patch.
    topo_anchor = '''        } else if (strcmp(fn, "GetCurrentThread") == 0) {
'''
    if topo_anchor not in e:
        raise SystemExit("ERROR: GetCurrentThread dispatch anchor changed before V10")

    topo_branch = r'''        } else if (strcmp(fn, "GetLogicalProcessorInformationEx") == 0) {
            /*
             * Unity asks RelationGroup (4) with a NULL buffer first. Return a
             * one-group / one-logical-processor SYSTEM_LOGICAL_PROCESSOR...
             * record on the second call. 80 bytes is the x64 RelationGroup
             * layout (header + GROUP_RELATIONSHIP + one PROCESSOR_GROUP_INFO).
             */
            uint32_t relation = (uint32_t)args[0];
            uint64_t out_ptr = args[1];
            uint64_t len_ptr = args[2];
            uint32_t supplied = 0;
            const uint32_t required = 80;

            if (len_ptr)
                wg_blink_read_mem(engine->blink, len_ptr,
                                  &supplied, sizeof(supplied));

            if (!out_ptr || supplied < required) {
                if (len_ptr)
                    wg_blink_write_mem(engine->blink, len_ptr,
                                       &required, sizeof(required));
                s_last_error = 122; /* ERROR_INSUFFICIENT_BUFFER */
                ret_val = 0;
                WG_LOGI(TAG,
                        "GetLogicalProcessorInformationEx V10(rel=%u) "
                        "size-query supplied=%u -> need=%u",
                        relation, supplied, required);
            } else {
                uint8_t info[80];
                memset(info, 0, sizeof(info));

                uint32_t rel_group = 4;
                uint32_t sz = required;
                uint16_t one16 = 1;
                uint64_t mask = 1;

                memcpy(info + 0, &rel_group, 4); /* Relationship */
                memcpy(info + 4, &sz, 4);        /* Size */
                memcpy(info + 8, &one16, 2);     /* MaximumGroupCount */
                memcpy(info + 10, &one16, 2);    /* ActiveGroupCount */

                /* PROCESSOR_GROUP_INFO starts at offset 32. */
                info[32] = 1;                    /* MaximumProcessorCount */
                info[33] = 1;                    /* ActiveProcessorCount */
                memcpy(info + 72, &mask, 8);     /* ActiveProcessorMask */

                wg_blink_write_mem(engine->blink, out_ptr,
                                   info, sizeof(info));
                if (len_ptr)
                    wg_blink_write_mem(engine->blink, len_ptr,
                                       &required, sizeof(required));
                s_last_error = 0;
                ret_val = 1;
                WG_LOGI(TAG,
                        "GetLogicalProcessorInformationEx V10(rel=%u) "
                        "-> 1 group / 1 logical CPU",
                        relation);
            }
        } else if (strcmp(fn, "GetActiveProcessorCount") == 0 ||
                   strcmp(fn, "GetMaximumProcessorCount") == 0) {
            ret_val = 1;
            s_last_error = 0;
        } else if (strcmp(fn, "GetActiveProcessorGroupCount") == 0 ||
                   strcmp(fn, "GetMaximumProcessorGroupCount") == 0) {
            ret_val = 1;
            s_last_error = 0;
        } else if (strcmp(fn, "GetCurrentProcessId") == 0) {
            /* PID 0 is not a valid normal Windows process ID. */
            ret_val = 1;
            s_last_error = 0;
        } else if (strcmp(fn, "GetProcessId") == 0) {
            ret_val = args[0] ? 1 : 0;
            s_last_error = ret_val ? 0 : 6;
'''
    e = e.replace(topo_anchor, topo_branch + topo_anchor, 1)

    sem_anchor = '''        } else if (strcmp(fn, "CreateEventA") == 0 ||
                   strcmp(fn, "CreateEventW") == 0) {
'''
    if sem_anchor not in e:
        raise SystemExit("ERROR: CreateEvent dispatch anchor changed before V10")

    sem_branch = r'''        } else if (strcmp(fn, "CreateSemaphoreA") == 0 ||
                   strcmp(fn, "CreateSemaphoreW") == 0 ||
                   strcmp(fn, "CreateSemaphoreExA") == 0 ||
                   strcmp(fn, "CreateSemaphoreExW") == 0) {
            int32_t initial = (int32_t)args[1];
            int32_t maximum = (int32_t)args[2];
            uint32_t h = 0;

            if (maximum <= 0 || initial < 0 || initial > maximum) {
                s_last_error = 87; /* ERROR_INVALID_PARAMETER */
            } else if (s_mxx_sem_next >= MXX_MAX_SEMS) {
                s_last_error = 8;  /* ERROR_NOT_ENOUGH_MEMORY */
            } else {
                uint32_t idx = s_mxx_sem_next++;
                s_mxx_sem_count[idx] = initial;
                s_mxx_sem_max[idx] = maximum;
                h = MXX_SEM_BASE + idx;
                s_last_error = 0;
            }

            WG_LOGI(TAG,
                    "%s V10(initial=%d max=%d) -> h=0x%X err=0x%X",
                    fn, initial, maximum, h, s_last_error);
            ret_val = h;
'''
    e = e.replace(sem_anchor, sem_branch + sem_anchor, 1)

    release_anchor = '''        } else if (strcmp(fn, "SetEvent") == 0) {
'''
    if release_anchor not in e:
        raise SystemExit("ERROR: SetEvent dispatch anchor changed before V10")

    release_branch = r'''        } else if (strcmp(fn, "ReleaseSemaphore") == 0) {
            uint32_t h = (uint32_t)args[0];
            int32_t release = (int32_t)args[1];
            uint64_t prev_ptr = args[2];

            if (!mxx_sem_valid(h) || release <= 0) {
                ret_val = 0;
                s_last_error = 6;
            } else {
                uint32_t idx = h - MXX_SEM_BASE;
                int32_t prev = s_mxx_sem_count[idx];
                int64_t next = (int64_t)prev + release;

                if (next > s_mxx_sem_max[idx]) {
                    ret_val = 0;
                    s_last_error = 298; /* ERROR_TOO_MANY_POSTS */
                } else {
                    s_mxx_sem_count[idx] = (int32_t)next;
                    if (prev_ptr)
                        wg_blink_write_mem(engine->blink, prev_ptr,
                                           &prev, sizeof(prev));
                    wg_sched_wake(engine->scheduler, h);
                    ret_val = 1;
                    s_last_error = 0;
                }
            }

            WG_LOGI(TAG,
                    "ReleaseSemaphore V10(h=0x%X release=%d) -> %llu "
                    "count=%d err=0x%X",
                    h, release, (unsigned long long)ret_val,
                    mxx_sem_valid(h)
                        ? s_mxx_sem_count[h - MXX_SEM_BASE] : -1,
                    s_last_error);
'''
    e = e.replace(release_anchor, release_branch + release_anchor, 1)

    # Teach the existing waits about semaphore handles.
    old_single = '''            if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                signalled = s_event_signalled[h - WG_EVENT_BASE];
'''
    new_single = '''            if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                signalled = s_event_signalled[h - WG_EVENT_BASE];
            else if (mxx_sem_valid(h))
                signalled = mxx_sem_signalled(h);
'''
    if old_single not in e:
        raise SystemExit("ERROR: WaitForSingleObject event check changed before V10")
    e = e.replace(old_single, new_single, 1)

    old_single_consume = '''                wg_event_consume(h); // auto-reset events clear after a satisfied wait
                ret_val = 0; // WAIT_OBJECT_0
'''
    new_single_consume = '''                wg_event_consume(h); // auto-reset events clear after a satisfied wait
                mxx_sem_consume(h);   // counting semaphore consumes one permit
                ret_val = 0; // WAIT_OBJECT_0
'''
    if old_single_consume not in e:
        raise SystemExit("ERROR: WaitForSingleObject consume block changed before V10")
    e = e.replace(old_single_consume, new_single_consume, 1)

    old_multi = '''                if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                    sig = s_event_signalled[h - WG_EVENT_BASE];
'''
    new_multi = '''                if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                    sig = s_event_signalled[h - WG_EVENT_BASE];
                else if (mxx_sem_valid(h))
                    sig = mxx_sem_signalled(h);
'''
    if old_multi not in e:
        raise SystemExit("ERROR: WaitForMultipleObjects event check changed before V10")
    e = e.replace(old_multi, new_multi, 1)

    old_multi_consume_all = '''                        for (uint32_t idx = 0; idx < ncount; idx++) wg_event_consume(handles[idx]);
'''
    new_multi_consume_all = '''                        for (uint32_t idx = 0; idx < ncount; idx++) {
                            wg_event_consume(handles[idx]);
                            mxx_sem_consume(handles[idx]);
                        }
'''
    if old_multi_consume_all not in e:
        raise SystemExit("ERROR: WaitForMultipleObjects consume-all block changed before V10")
    e = e.replace(old_multi_consume_all, new_multi_consume_all, 1)

    old_multi_consume_one = '''                        wg_event_consume(handles[first_signalled]);
'''
    new_multi_consume_one = '''                        wg_event_consume(handles[first_signalled]);
                        mxx_sem_consume(handles[first_signalled]);
'''
    if old_multi_consume_one not in e:
        raise SystemExit("ERROR: WaitForMultipleObjects consume-one block changed before V10")
    e = e.replace(old_multi_consume_one, new_multi_consume_one, 1)

    # V8 deliberately treats many Windows DLLs as emulated modules. PSAPI is
    # another normal Windows system DLL and the V9 log shows Unity loading it
    # only after entering its diagnostics path.
    psapi_old = '''                            strcasestr(ascii, "dbghelp") ||
                            strcasestr(ascii, "d3d") ||
'''
    psapi_new = '''                            strcasestr(ascii, "dbghelp") ||
                            strcasestr(ascii, "psapi") ||
                            strcasestr(ascii, "d3d") ||
'''
    if psapi_old not in e:
        raise SystemExit("ERROR: V8 system-DLL list changed before V10")
    e = e.replace(psapi_old, psapi_new, 1)

    # Benign PSAPI implementations. These are mainly there so if Unity still
    # enters its reporter we keep collecting useful diagnostics instead of
    # failing immediately on psapi.dll.
    psapi_dispatch_anchor = '''        } else if (strcmp(fn, "GetCurrentThread") == 0) {
'''
    psapi_branch = r'''        } else if (strcmp(fn, "EnumProcessModules") == 0 ||
                   strcmp(fn, "EnumProcessModulesEx") == 0) {
            uint64_t modules_ptr = args[1];
            uint32_t cb = (uint32_t)args[2];
            uint64_t needed_ptr = args[3];
            uint32_t needed = 8;
            uint64_t main_module = engine->pe_image
                ? engine->pe_image->image_base : 0x140000000ULL;

            if (needed_ptr)
                wg_blink_write_mem(engine->blink, needed_ptr,
                                   &needed, sizeof(needed));
            if (modules_ptr && cb >= 8)
                wg_blink_write_mem(engine->blink, modules_ptr,
                                   &main_module, sizeof(main_module));
            ret_val = 1;
            s_last_error = 0;
        } else if (strcmp(fn, "GetProcessMemoryInfo") == 0) {
            /* PROCESS_MEMORY_COUNTERS: set cb + a modest working set. */
            if (args[1] && args[2] >= 72) {
                uint8_t pmc[72];
                memset(pmc, 0, sizeof(pmc));
                uint32_t cbv = 72;
                uint64_t ws = 128ULL * 1024ULL * 1024ULL;
                memcpy(pmc + 0, &cbv, 4);
                memcpy(pmc + 16, &ws, 8); /* PeakWorkingSetSize */
                memcpy(pmc + 24, &ws, 8); /* WorkingSetSize */
                wg_blink_write_mem(engine->blink, args[1],
                                   pmc, sizeof(pmc));
            }
            ret_val = 1;
            s_last_error = 0;
'''
    # Insert this branch only once, before the first GetCurrentThread branch.
    if psapi_dispatch_anchor not in e:
        raise SystemExit("ERROR: PSAPI dispatch anchor changed before V10")
    e = e.replace(psapi_dispatch_anchor, psapi_branch + psapi_dispatch_anchor, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V10: implemented processor topology + counting semaphores + PSAPI fallback")
else:
    print("V10: engine patch already present")

# ---------------------------------------------------------------------------
# Capability-based verification.
# ---------------------------------------------------------------------------
mv = mapper_p.read_text(encoding="utf-8")
ev = engine_p.read_text(encoding="utf-8")

for token in (
    MARKER,
    "GetLogicalProcessorInformationEx",
    "CreateSemaphoreExW",
    "EnumProcessModules",
    '"PSAPI.dll"',
):
    if token not in mv:
        raise SystemExit("ERROR: V10.1 mapper verification failed: " + token)

print("V10.1: mapper verification passed")

for token in (
    MARKER,
    "GetLogicalProcessorInformationEx V10",
    "CreateSemaphoreExW",
    "ReleaseSemaphore V10",
    "MXX_SEM_BASE",
    "mxx_sem_consume",
    'strcasestr(ascii, "psapi")',
    'strcmp(fn, "EnumProcessModules")',
):
    if token not in ev:
        raise SystemExit("ERROR: V10 engine verification failed: " + token)

print("MXXHUB_UNITY_JOB_SYSTEM_FIX_V10_1_OK")
print("MXXHUB_UNITY_JOB_SYSTEM_FIX_V10_OK")
