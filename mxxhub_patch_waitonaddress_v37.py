#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_waitonaddress_v37.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_WINDOWS_V37_WAITONADDRESS_REAL_PARK"

if MARKER not in s:
    # Keep a tiny per-scheduler-slot address-wait registry. The guest scheduler is
    # cooperative, so a parked WaitOnAddress thread can be woken by changing its
    # state from WAITING to READY when WakeByAddress* runs on another guest thread.
    globals_anchor = "static uint32_t s_last_error = 0;\n"
    globals_block = r'''static uint32_t s_last_error = 0;
/* MXXHUB_WINDOWS_V37_WAITONADDRESS_REAL_PARK
 * Address waits are process-local and keyed by scheduler slot.  We only truly
 * park long/infinite waits; short finite waits keep the old cooperative poll
 * behavior because WineGlass has no host timer queue for guest wait deadlines.
 */
static bool     s_mxx_waitaddr_active[WG_MAX_THREADS] = {0};
static uint64_t s_mxx_waitaddr_addr[WG_MAX_THREADS] = {0};
static uint8_t  s_mxx_waitaddr_size[WG_MAX_THREADS] = {0};

static bool mxx_waitaddr_equal(WGBlinkInstance *blink,
                               uint64_t addr, uint64_t compare, uint64_t size) {
    if (!blink || !addr || !compare ||
        !(size == 1 || size == 2 || size == 4 || size == 8)) return false;
    uint8_t a[8] = {0};
    uint8_t b[8] = {0};
    if (!wg_blink_read_mem(blink, addr, a, (size_t)size)) return false;
    if (!wg_blink_read_mem(blink, compare, b, (size_t)size)) return false;
    return memcmp(a, b, (size_t)size) == 0;
}
'''
    if globals_anchor not in s:
        raise SystemExit("ERROR: V37 globals anchor changed")
    s = s.replace(globals_anchor, globals_block, 1)

    api_anchor = '''        } else if (strcmp(fn, "GetSystemMetrics") == 0) {
'''
    api_block = r'''        } else if (strcmp(fn, "WaitOnAddress") == 0) {
            uint64_t addr = args[0];
            uint64_t compare = args[1];
            uint64_t size = args[2];
            uint32_t timeout = (uint32_t)args[3];
            int wait_slot = engine->scheduler ? engine->scheduler->current : -1;

            if (!addr || !compare || !(size == 1 || size == 2 || size == 4 || size == 8)) {
                s_last_error = 87; /* ERROR_INVALID_PARAMETER */
                ret_val = 0;
            } else if (!mxx_waitaddr_equal(engine->blink, addr, compare, size)) {
                if (wait_slot >= 0 && wait_slot < WG_MAX_THREADS)
                    s_mxx_waitaddr_active[wait_slot] = false;
                ret_val = 1; /* observed value already changed */
            } else if (timeout == 0) {
                if (wait_slot >= 0 && wait_slot < WG_MAX_THREADS)
                    s_mxx_waitaddr_active[wait_slot] = false;
                s_last_error = 1460; /* ERROR_TIMEOUT */
                ret_val = 0;
            } else {
                /* Unity uses 0xFFFFFFFE here. Treat huge waits like INFINITE so
                 * the waiter is genuinely removed from the READY set instead of
                 * spinning at the thunk thousands of times.  A wake makes the
                 * saved thunk re-enter, re-check the memory, and then return.
                 */
                bool long_wait = (timeout == 0xFFFFFFFFu || timeout >= 60000u);
                if (long_wait && wait_slot >= 0 && wait_slot < WG_MAX_THREADS) {
                    uint32_t wait_tid = wg_sched_current_tid(engine->scheduler);
                    s_mxx_waitaddr_active[wait_slot] = true;
                    s_mxx_waitaddr_addr[wait_slot] = addr;
                    s_mxx_waitaddr_size[wait_slot] = (uint8_t)size;
                    WGThread *cur = wg_sched_current(engine->scheduler);
                    if (cur) {
                        cur->wait_handle = 0;
                        cur->wait_timeout = timeout;
                    }
                    bool switched = wg_sched_yield(
                        engine->scheduler, engine->blink, WG_THREAD_WAITING);
                    WG_LOGI(TAG,
                            "WAITADDR V37 PARK: slot=%d tid=0x%X addr=0x%llX size=%llu timeout=0x%X switched=%d state=%d",
                            wait_slot,
                            (unsigned)wait_tid,
                            (unsigned long long)addr,
                            (unsigned long long)size,
                            timeout, (int)switched,
                            (int)engine->scheduler->threads[wait_slot].state);
                    if (switched) return true;

                    /* No READY peer exists. Windows allows spurious wakeups, so
                     * do not deadlock the only runnable guest. */
                    s_mxx_waitaddr_active[wait_slot] = false;
                    ret_val = 1;
                } else {
                    /* Short finite wait: cooperative poll with timeout result if
                     * we are alone. Re-entering the thunk after a switch keeps
                     * other workers moving without a hot single-thread loop. */
                    bool switched = wg_sched_yield(
                        engine->scheduler, engine->blink, WG_THREAD_READY);
                    if (switched) return true;
                    s_last_error = 1460;
                    ret_val = 0;
                }
            }

        } else if (strcmp(fn, "WakeByAddressSingle") == 0 ||
                   strcmp(fn, "WakeByAddressAll") == 0) {
            uint64_t wake_addr = args[0];
            bool wake_all = strcmp(fn, "WakeByAddressAll") == 0;
            int woke = 0;
            if (engine->scheduler && wake_addr) {
                for (int wi = 0; wi < WG_MAX_THREADS; wi++) {
                    WGThread *wt = &engine->scheduler->threads[wi];
                    if (!s_mxx_waitaddr_active[wi] ||
                        s_mxx_waitaddr_addr[wi] != wake_addr ||
                        wt->state != WG_THREAD_WAITING) continue;
                    wt->state = WG_THREAD_READY;
                    wt->wait_timeout = 0;
                    s_mxx_waitaddr_active[wi] = false;
                    woke++;
                    WG_LOGI(TAG,
                            "WAITADDR V37 WAKE: %s slot=%d tid=0x%X addr=0x%llX size=%u -> READY",
                            wake_all ? "ALL" : "ONE", wi, wt->id,
                            (unsigned long long)wake_addr,
                            (unsigned)s_mxx_waitaddr_size[wi]);
                    if (!wake_all) break;
                }
            }
            if (woke == 0) {
                WG_LOGD(TAG, "WAITADDR V37 WAKE: %s addr=0x%llX no parked waiter",
                        wake_all ? "ALL" : "ONE", (unsigned long long)wake_addr);
            }
            ret_val = 0; /* VOID */

        } else if (strcmp(fn, "GetSystemMetrics") == 0) {
'''
    if api_anchor not in s:
        raise SystemExit("ERROR: V37 WaitOnAddress API insertion anchor changed")
    s = s.replace(api_anchor, api_block, 1)

    # Stable quiet-list insertion: anchor only on the SRW pair. V29/V30 may add
    # HeapReAlloc or other items between SRW and HeapAlloc.
    quiet_token = '            "TryAcquireSRWLockExclusive", "TryAcquireSRWLockShared",\n'
    quiet_add = quiet_token + '            "WaitOnAddress", "WakeByAddressSingle", "WakeByAddressAll",\n'
    if '"WaitOnAddress", "WakeByAddressSingle", "WakeByAddressAll"' not in s:
        if quiet_token not in s:
            raise SystemExit("ERROR: V37 quiet-list SRW token changed")
        s = s.replace(quiet_token, quiet_add, 1)

    # Stable call-ring insertion directly after V28's own return token. Later
    # patches can safely add their filters before/after this block.
    ring_token = '        return; // V28: SRW poll-loop noise\n'
    ring_add = ring_token + r'''    if (strcmp(name, "WaitOnAddress") == 0 ||
        strcmp(name, "WakeByAddressSingle") == 0 ||
        strcmp(name, "WakeByAddressAll") == 0)
        return; // V37: address-wait synchronization noise
'''
    if 'return; // V37: address-wait synchronization noise' not in s:
        if ring_token not in s:
            raise SystemExit("ERROR: V37 call-ring V28 token changed")
        s = s.replace(ring_token, ring_add, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("V37: WaitOnAddress now uses real WAITING parking for long/near-infinite waits")
    print("V37: WakeByAddressSingle/All wake matching parked guest threads")
    print("V37: V29/V30-safe quiet-list and call-ring anchors applied")
else:
    print("V37: WaitOnAddress real-parking patch already present")

final = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    'strcmp(fn, "WaitOnAddress") == 0',
    'strcmp(fn, "WakeByAddressSingle") == 0',
    'WAITADDR V37 PARK:',
    'WAITADDR V37 WAKE:',
    'WG_THREAD_WAITING',
    'timeout >= 60000u',
    'return; // V37: address-wait synchronization noise',
):
    if token not in final:
        raise SystemExit("ERROR: V37 verification failed: " + token)

print("MXXHUB_WINDOWS_V37_WAITONADDRESS_REAL_PARK_OK")
