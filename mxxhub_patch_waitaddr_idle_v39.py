#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_waitaddr_idle_v39.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
engine_p = wg / 'Sources/Core/wg_engine.c'
if not engine_p.is_file():
    raise SystemExit(f'ERROR: missing {engine_p}')

s = engine_p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V39_WAITADDR_IDLE_SCHEDULER'

if MARKER not in s:
    # V37 already owns the address-wait registry. Extend it with a host-side
    # spurious-wake flag and a round-robin cursor. This is needed because the
    # pinned WineGlass scheduler restores the current thread to RUNNING when
    # wg_sched_yield(...WAITING) cannot find another READY guest thread.
    globals_old = '''static bool     s_mxx_waitaddr_active[WG_MAX_THREADS] = {0};\nstatic uint64_t s_mxx_waitaddr_addr[WG_MAX_THREADS] = {0};\nstatic uint8_t  s_mxx_waitaddr_size[WG_MAX_THREADS] = {0};\n'''
    globals_new = '''static bool     s_mxx_waitaddr_active[WG_MAX_THREADS] = {0};\nstatic uint64_t s_mxx_waitaddr_addr[WG_MAX_THREADS] = {0};\nstatic uint8_t  s_mxx_waitaddr_size[WG_MAX_THREADS] = {0};\n/* MXXHUB_WINDOWS_V39_WAITADDR_IDLE_SCHEDULER\n * V37 could still hot-loop the final runnable waiter because WineGlass restores\n * a blocked current thread when no READY peer exists. V39 parks that final\n * waiter, pauses the engine, and lets the host tick wake one waiter at a\n * controlled rate. Windows WaitOnAddress permits spurious wakeups, so this is\n * safe and prevents an all-waiting guest from burning a full iOS core.\n */\nstatic bool     s_mxx_waitaddr_spurious[WG_MAX_THREADS] = {0};\nstatic uint32_t s_mxx_waitaddr_idle_cursor = 0;\nstatic uint64_t s_mxx_waitaddr_idle_polls = 0;\n'''
    if globals_old not in s:
        raise SystemExit('ERROR: V39 globals anchor changed (V37 registry missing?)')
    s = s.replace(globals_old, globals_new, 1)

    # When a V39 host-side wake selects a waiter, complete the API call as a
    # controlled spurious wake instead of immediately parking the same thunk.
    dispatch_old = '''            int wait_slot = engine->scheduler ? engine->scheduler->current : -1;\n\n            if (!addr || !compare || !(size == 1 || size == 2 || size == 4 || size == 8)) {\n'''
    dispatch_new = '''            int wait_slot = engine->scheduler ? engine->scheduler->current : -1;\n\n            if (wait_slot >= 0 && wait_slot < WG_MAX_THREADS &&\n                s_mxx_waitaddr_spurious[wait_slot]) {\n                s_mxx_waitaddr_spurious[wait_slot] = false;\n                s_mxx_waitaddr_active[wait_slot] = false;\n                s_last_error = 0;\n                ret_val = 1; /* V39 controlled spurious wake */\n                WG_LOGD(TAG, "WAITADDR V39 RESUME: slot=%d tid=0x%X -> spurious success",\n                        wait_slot, wg_sched_current_tid(engine->scheduler));\n            } else if (!addr || !compare || !(size == 1 || size == 2 || size == 4 || size == 8)) {\n'''
    if dispatch_old not in s:
        raise SystemExit('ERROR: V39 WaitOnAddress dispatch anchor changed')
    s = s.replace(dispatch_old, dispatch_new, 1)

    # This is the exact V37 bug seen on-device: switched=0 followed by state=1
    # (RUNNING), then the same WAITADDR PARK line repeats until iOS kills the app.
    fallback_old = '''                    if (switched) return true;\n\n                    /* No READY peer exists. Windows allows spurious wakeups, so\n                     * do not deadlock the only runnable guest. */\n                    s_mxx_waitaddr_active[wait_slot] = false;\n                    ret_val = 1;\n'''
    fallback_new = '''                    if (switched) return true;\n\n                    /* V39: no READY guest exists. wg_sched_yield() restored this\n                     * waiter to RUNNING, which made V37 loop at the thunk forever.\n                     * Re-park it explicitly and hand control back to the host.\n                     * The engine thread calls wg_engine_tick() while PAUSED; the\n                     * V39 host-idle block below periodically wakes one address\n                     * waiter as a legal spurious wake so Mono/Unity can re-check\n                     * its queues without a watchdog-grade spin loop. */\n                    WGThread *last_waiter = &engine->scheduler->threads[wait_slot];\n                    last_waiter->state = WG_THREAD_WAITING;\n                    engine->scheduler->current = -1;\n                    engine->state = WG_ENGINE_PAUSED;\n                    WG_LOGI(TAG,\n                            "WAITADDR V39 IDLE-PARK: slot=%d tid=0x%X addr=0x%llX timeout=0x%X -> engine PAUSED",\n                            wait_slot, (unsigned)wait_tid,\n                            (unsigned long long)addr, timeout);\n                    return true;\n'''
    if fallback_old not in s:
        raise SystemExit('ERROR: V39 no-READY fallback anchor changed')
    s = s.replace(fallback_old, fallback_new, 1)

    wake_old = '''                    wt->state = WG_THREAD_READY;\n                    wt->wait_timeout = 0;\n                    s_mxx_waitaddr_active[wi] = false;\n                    woke++;\n'''
    wake_new = '''                    wt->state = WG_THREAD_READY;\n                    wt->wait_timeout = 0;\n                    s_mxx_waitaddr_active[wi] = false;\n                    s_mxx_waitaddr_spurious[wi] = false;\n                    woke++;\n'''
    if wake_old not in s:
        raise SystemExit('ERROR: V39 wake anchor changed')
    s = s.replace(wake_old, wake_new, 1)

    # Host-idle scheduler: only activates for the V39 all-waiting state
    # (PAUSED + current == -1). Dialog pauses keep a valid current thread and are
    # untouched. Every ~32ms at the current 8ms paused-loop sleep we mark one
    # waiter READY. Existing PAUSED tick logic then switches to it and resumes.
    tick_old = '''void wg_engine_tick(WGEngine *engine) {\n    if (!engine) return;\n    // Allow ticking when PAUSED if worker threads need to run\n'''
    tick_new = '''void wg_engine_tick(WGEngine *engine) {\n    if (!engine) return;\n\n    /* V39 host-idle WaitOnAddress scheduler. */\n    if (engine->state == WG_ENGINE_PAUSED && engine->scheduler &&\n        engine->scheduler->current < 0) {\n        s_mxx_waitaddr_idle_polls++;\n        if ((s_mxx_waitaddr_idle_polls & 3ULL) == 0) {\n            for (int wn = 0; wn < WG_MAX_THREADS; wn++) {\n                int wi = (int)((s_mxx_waitaddr_idle_cursor + (uint32_t)wn) % WG_MAX_THREADS);\n                WGThread *wt = &engine->scheduler->threads[wi];\n                if (!s_mxx_waitaddr_active[wi] || wt->state != WG_THREAD_WAITING)\n                    continue;\n                s_mxx_waitaddr_spurious[wi] = true;\n                wt->state = WG_THREAD_READY;\n                s_mxx_waitaddr_idle_cursor = (uint32_t)(wi + 1) % WG_MAX_THREADS;\n                if (s_mxx_waitaddr_idle_polls <= 32 ||\n                    (s_mxx_waitaddr_idle_polls & 255ULL) == 0) {\n                    WG_LOGI(TAG,\n                            "WAITADDR V39 HOST-WAKE: slot=%d tid=0x%X addr=0x%llX poll=%llu -> READY",\n                            wi, wt->id,\n                            (unsigned long long)s_mxx_waitaddr_addr[wi],\n                            (unsigned long long)s_mxx_waitaddr_idle_polls);\n                }\n                break;\n            }\n        }\n    }\n\n    // Allow ticking when PAUSED if worker threads need to run\n'''
    if tick_old not in s:
        raise SystemExit('ERROR: V39 wg_engine_tick anchor changed')
    s = s.replace(tick_old, tick_new, 1)

    # The newest device log is dominated by timing/TLS/env probes. These calls
    # are not diagnostic milestones and their per-call logging materially raises
    # CPU and memory pressure during Mono startup. Keep their semantics; only
    # suppress the generic one-line API trace.
    quiet_old = '            "WaitOnAddress", "WakeByAddressSingle", "WakeByAddressAll",\n'
    quiet_new = quiet_old + '            "QueryPerformanceFrequency", "TlsSetValue", "GetEnvironmentVariableW",\n'
    if '"QueryPerformanceFrequency", "TlsSetValue", "GetEnvironmentVariableW"' not in s:
        if quiet_old not in s:
            raise SystemExit('ERROR: V39 quiet-list anchor changed')
        s = s.replace(quiet_old, quiet_new, 1)

    # Also keep the post-crash call ring useful instead of filling it with timing
    # probes during Mono JIT publication.
    ring_old = '''        strcmp(name, "WakeByAddressAll") == 0)\n        return; // V37: address-wait synchronization noise\n'''
    ring_new = ring_old + '''    if (strcmp(name, "QueryPerformanceFrequency") == 0 ||\n        strcmp(name, "TlsSetValue") == 0 ||\n        strcmp(name, "GetEnvironmentVariableW") == 0)\n        return; // V39: high-rate Mono startup noise\n'''
    if 'return; // V39: high-rate Mono startup noise' not in s:
        if ring_old not in s:
            raise SystemExit('ERROR: V39 call-ring anchor changed')
        s = s.replace(ring_old, ring_new, 1)

    engine_p.write_text(s, encoding='utf-8')
    print('V39: final WaitOnAddress waiter now parks instead of being restored RUNNING')
    print('V39: all-waiting guest pauses engine and receives controlled host-tick spurious wakes')
    print('V39: high-rate Mono timing/TLS/env trace noise suppressed')
else:
    print('V39: all-waiting WaitOnAddress scheduler patch already present')

final = engine_p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'WAITADDR V39 IDLE-PARK:',
    'WAITADDR V39 HOST-WAKE:',
    'WAITADDR V39 RESUME:',
    'engine->scheduler->current = -1;',
    'engine->state = WG_ENGINE_PAUSED;',
    's_mxx_waitaddr_spurious',
    'return; // V39: high-rate Mono startup noise',
):
    if token not in final:
        raise SystemExit('ERROR: V39 verification failed: ' + token)

print('MXXHUB_WINDOWS_V39_WAITADDR_IDLE_SCHEDULER_OK')
