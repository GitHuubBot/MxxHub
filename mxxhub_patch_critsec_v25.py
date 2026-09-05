#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_critsec_v25.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_CRITSEC_FAIR_YIELD_V25"

if MARKER not in s:
    quiet_anchor = '''            "WaitForSingleObject", "WaitForSingleObjectEx", // poll-loop noise
            "QueryPerformanceCounter", "GetSystemTimePreciseAsFileTime",
'''
    quiet_new = '''            "WaitForSingleObject", "WaitForSingleObjectEx", // poll-loop noise
            "EnterCriticalSection", "LeaveCriticalSection",
            "TryEnterCriticalSection", // V25: cooperative-lock poll noise
            "QueryPerformanceCounter", "GetSystemTimePreciseAsFileTime",
'''
    if quiet_anchor not in s:
        raise SystemExit("ERROR: V25 quiet-function anchor changed")
    s = s.replace(quiet_anchor, quiet_new, 1)

    epilogue = '''    wg_call_ring_push(entry ? entry->func_name : "?", ret_val);
    return true;
}
'''
    replacement = r'''    wg_call_ring_push(entry ? entry->func_name : "?", ret_val);

    /* MXXHUB_CRITSEC_FAIR_YIELD_V25
     * WineGlass uses a cooperative guest scheduler. Mono/Unity can poll a
     * shared condition by entering/leaving the same CRITICAL_SECTION in a
     * tight loop. Since these emulated calls do not naturally block, that
     * polling thread can starve the worker that must update the condition.
     *
     * The thunk return registers above already point past the API call, so
     * yielding here is safe: when resumed, this thread continues AFTER
     * LeaveCriticalSection instead of repeating it.
     */
    if (entry && strcmp(entry->func_name, "LeaveCriticalSection") == 0) {
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
    if epilogue not in s:
        raise SystemExit("ERROR: V25 thunk epilogue anchor changed")
    s = s.replace(epilogue, replacement, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V25: critical-section polling now gives other guest threads CPU time")
else:
    print("V25: critical-section fair-yield patch already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "CRITSEC V25 FAIR YIELD:",
    '"EnterCriticalSection", "LeaveCriticalSection"',
    'strcmp(entry->func_name, "LeaveCriticalSection") == 0',
):
    if token not in final:
        raise SystemExit("ERROR: V25 verification failed: " + token)

print("MXXHUB_CRITSEC_FAIR_YIELD_V25_OK")
