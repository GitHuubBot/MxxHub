#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_srw_v28.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_SRW_FAIR_YIELD_V28"

if MARKER not in s:
    # ------------------------------------------------------------------
    # V27 device proof:
    # - page-table pool fix WORKED: the log reaches tables=2145 (> old 512 cap)
    # - no AllocatePageTable failure appears
    # - retained 2000-line tail is ~985 AcquireSRWLockExclusive +
    #   ~984 ReleaseSRWLockExclusive calls on the exact same lock 0x62861410
    # - only tid=1 appears in that tail
    #
    # WineGlass guest scheduling is cooperative. These SRW calls are currently
    # emulated stubs and don't naturally block/yield, so one polling worker can
    # monopolize the VM exactly like the V25 critical-section loop did.
    # ------------------------------------------------------------------

    # 1) Stop SRW spam from consuming the entire diagnostic tail.
    quiet_anchor = '''            "EnterCriticalSection", "LeaveCriticalSection",
            "TryEnterCriticalSection", // V25: cooperative-lock poll noise
            "HeapAlloc", "HeapFree", "HeapSize",
'''
    quiet_new = '''            "EnterCriticalSection", "LeaveCriticalSection",
            "TryEnterCriticalSection", // V25: cooperative-lock poll noise
            "AcquireSRWLockExclusive", "ReleaseSRWLockExclusive",
            "AcquireSRWLockShared", "ReleaseSRWLockShared",
            "TryAcquireSRWLockExclusive", "TryAcquireSRWLockShared",
            "HeapAlloc", "HeapFree", "HeapSize",
'''
    if quiet_anchor not in s:
        raise SystemExit("ERROR: V28 quiet-list anchor changed")
    s = s.replace(quiet_anchor, quiet_new, 1)

    # 2) Keep crash API ring useful too. Otherwise a later real crash reports
    #    only hundreds of SRW calls and hides the calls that led into the loop.
    ring_anchor = '''    if (name[0] == 'T' && name[3] == 'E') return; // TryEnterCriticalSection
    if (name[0] == 'G' && name[3] == 'L') return; // GetLastError
'''
    ring_new = '''    if (name[0] == 'T' && name[3] == 'E') return; // TryEnterCriticalSection
    if ((name[0] == 'A' && strstr(name, "AcquireSRWLock") != NULL) ||
        (name[0] == 'R' && strstr(name, "ReleaseSRWLock") != NULL) ||
        (name[0] == 'T' && strstr(name, "TryAcquireSRWLock") != NULL))
        return; // V28: SRW poll-loop noise
    if (name[0] == 'G' && name[3] == 'L') return; // GetLastError
'''
    if ring_anchor not in s:
        raise SystemExit("ERROR: V28 call-ring anchor changed")
    s = s.replace(ring_anchor, ring_new, 1)

    # 3) Yield after a release, never while the guest logically owns the lock.
    #    V25 already established the safe insertion point after thunk return
    #    registers have been restored.
    epilogue_anchor = '''    if (entry && strcmp(entry->func_name, "LeaveCriticalSection") == 0) {
        static uint64_t v25_leave_count = 0;
        static uint32_t v25_last_cs = 0;
        static uint64_t v25_same_cs_count = 0;

        v25_leave_count++;
        uint32_t cs = args[0];
        if (cs == v25_last_cs) {
            v25_same_cs_count++;
        } else {
            v25_last_cs = cs;
            v25_same_cs_count = 1;
        }

        if ((v25_same_cs_count & 31ULL) == 0) {
            bool switched = wg_sched_yield(
                engine->scheduler, engine->blink, WG_THREAD_READY);

            if ((v25_same_cs_count & 255ULL) == 0 || switched) {
                WG_LOGI(TAG,
                        "CRITSEC V25 FAIR YIELD: cs=0x%X same=%llu total=%llu switched=%d",
                        cs,
                        (unsigned long long)v25_same_cs_count,
                        (unsigned long long)v25_leave_count,
                        (int)switched);
            }

            if (switched) return true;
        }
    }

    return true;
}
'''
    replacement = r'''    if (entry && strcmp(entry->func_name, "LeaveCriticalSection") == 0) {
        static uint64_t v25_leave_count = 0;
        static uint32_t v25_last_cs = 0;
        static uint64_t v25_same_cs_count = 0;

        v25_leave_count++;
        uint32_t cs = args[0];
        if (cs == v25_last_cs) {
            v25_same_cs_count++;
        } else {
            v25_last_cs = cs;
            v25_same_cs_count = 1;
        }

        if ((v25_same_cs_count & 31ULL) == 0) {
            bool switched = wg_sched_yield(
                engine->scheduler, engine->blink, WG_THREAD_READY);

            if ((v25_same_cs_count & 255ULL) == 0 || switched) {
                WG_LOGI(TAG,
                        "CRITSEC V25 FAIR YIELD: cs=0x%X same=%llu total=%llu switched=%d",
                        cs,
                        (unsigned long long)v25_same_cs_count,
                        (unsigned long long)v25_leave_count,
                        (int)switched);
            }

            if (switched) return true;
        }
    }

    /* MXXHUB_SRW_FAIR_YIELD_V28
     *
     * Same cooperative-scheduler starvation fix as V25, but for Slim Reader/
     * Writer locks. Unity/Mono currently hits:
     *
     *   AcquireSRWLockExclusive(0x62861410)
     *   ReleaseSRWLockExclusive(0x62861410)
     *   ...repeat...
     *
     * Yield only after Release so no other guest thread is selected while the
     * current thread is logically inside this SRW-protected region.
     */
    if (entry &&
        (strcmp(entry->func_name, "ReleaseSRWLockExclusive") == 0 ||
         strcmp(entry->func_name, "ReleaseSRWLockShared") == 0)) {
        static uint64_t v28_release_count = 0;
        static uint32_t v28_last_srw = 0;
        static uint64_t v28_same_srw_count = 0;

        v28_release_count++;
        uint32_t srw = args[0];
        if (srw == v28_last_srw) {
            v28_same_srw_count++;
        } else {
            v28_last_srw = srw;
            v28_same_srw_count = 1;
        }

        /* The V27 loop was extremely hot, so give other READY threads a chance
         * every 16 releases of the same lock. This is still cheap for ordinary
         * initialization code.
         */
        if ((v28_same_srw_count & 15ULL) == 0) {
            bool switched = wg_sched_yield(
                engine->scheduler, engine->blink, WG_THREAD_READY);

            if ((v28_same_srw_count & 255ULL) == 0 || switched) {
                WG_LOGI(TAG,
                        "SRW V28 FAIR YIELD: lock=0x%X same=%llu total=%llu switched=%d",
                        srw,
                        (unsigned long long)v28_same_srw_count,
                        (unsigned long long)v28_release_count,
                        (int)switched);
            }

            if (switched) return true;
        }
    }

    return true;
}
'''
    if epilogue_anchor not in s:
        raise SystemExit("ERROR: V28 V25 epilogue anchor changed")
    s = s.replace(epilogue_anchor, replacement, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V28: SRW polling now yields fairly to other READY guest threads")
else:
    print("V28: SRW fair-yield patch already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "SRW V28 FAIR YIELD:",
    '"AcquireSRWLockExclusive", "ReleaseSRWLockExclusive"',
    'strcmp(entry->func_name, "ReleaseSRWLockExclusive") == 0',
    'strstr(name, "AcquireSRWLock")',
):
    if token not in final:
        raise SystemExit("ERROR: V28 verification failed: " + token)

print("MXXHUB_SRW_FAIR_YIELD_V28_OK")
