#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_SEMAPHORE_WAITEX_FIX_V14"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_wait_v14.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

e = engine_p.read_text(encoding="utf-8")

# V14 requires the V10/V11 semaphore model.
for token in (
    "MXX_SEM_BASE",
    "mxx_sem_valid",
    "mxx_sem_signalled",
    "mxx_sem_consume",
    "ReleaseSemaphore V11",
):
    if token not in e:
        raise SystemExit("ERROR: V14 requires semaphore runtime token: " + token)

if MARKER not in e:
    # ------------------------------------------------------------------
    # The original V10 patch taught WaitForSingleObject about semaphores,
    # but WineGlass has a SECOND, separate WaitForSingleObjectEx branch.
    # That branch remained event/thread-only.
    #
    # Device proof:
    #   CreateSemaphoreW V11(initial=1) -> 0x602
    #   WaitForSingleObjectEx(0x602, INFINITE) signalled=0
    #   ReleaseSemaphore(0x602, 1) -> count=2
    #
    # A semaphore born with count=1 MUST be signalled, and a successful wait
    # MUST consume one permit. The old branch did neither.
    # ------------------------------------------------------------------
    old_detect = '''            bool signalled = false;
            if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                signalled = s_event_signalled[h - WG_EVENT_BASE];
            WGThread *wt_ex = wg_sched_find(engine->scheduler, h);
'''
    new_detect = '''            bool signalled = false;
            if (h >= WG_EVENT_BASE && h < WG_EVENT_BASE + WG_MAX_EVENTS)
                signalled = s_event_signalled[h - WG_EVENT_BASE];
            else if (mxx_sem_valid(h))
                signalled = mxx_sem_signalled(h); /* MXXHUB_SEMAPHORE_WAITEX_FIX_V14 */
            WGThread *wt_ex = wg_sched_find(engine->scheduler, h);
'''
    if old_detect not in e:
        raise SystemExit("ERROR: V14 WaitForSingleObjectEx detection anchor changed")
    e = e.replace(old_detect, new_detect, 1)

    old_log = '''            WG_LOGI(TAG, "WaitForSingleObjectEx(h=0x%X, timeout=0x%X) signalled=%d",
                    h, timeout, (int)signalled);
'''
    new_log = '''            int sem_count_v14 = mxx_sem_valid(h)
                ? s_mxx_sem_count[h - MXX_SEM_BASE] : -1;
            WG_LOGI(TAG,
                    "WaitForSingleObjectEx V14(h=0x%X timeout=0x%X "
                    "signalled=%d semCount=%d)",
                    h, timeout, (int)signalled, sem_count_v14);
'''
    if old_log not in e:
        raise SystemExit("ERROR: V14 WaitForSingleObjectEx log anchor changed")
    e = e.replace(old_log, new_log, 1)

    old_consume = '''            if (signalled) {
                wg_event_consume(h);
                ret_val = 0;
'''
    new_consume = '''            if (signalled) {
                wg_event_consume(h);
                mxx_sem_consume(h);
                if (mxx_sem_valid(h)) {
                    WG_LOGI(TAG,
                            "WaitForSingleObjectEx V14 consumed semaphore "
                            "h=0x%X -> count=%d",
                            h, s_mxx_sem_count[h - MXX_SEM_BASE]);
                }
                ret_val = 0;
'''
    if old_consume not in e:
        raise SystemExit("ERROR: V14 WaitForSingleObjectEx consume anchor changed")
    e = e.replace(old_consume, new_consume, 1)

    # The upstream fallback incorrectly returned WAIT_OBJECT_0 for an
    # unsignalled INFINITE wait when there was no other runnable guest thread.
    # That tells Windows code a wait succeeded when it did not. Keep this as a
    # diagnostic timeout instead of a false semaphore acquisition.
    old_fallback = '''                bool sw = wg_sched_yield(engine->scheduler, engine->blink, blk);
                if (sw) return true;
                ret_val = (timeout == 0xFFFFFFFFu) ? 0 : 258;
'''
    new_fallback = '''                bool sw = wg_sched_yield(engine->scheduler, engine->blink, blk);
                if (sw) return true;
                ret_val = 258; /* V14: never fake WAIT_OBJECT_0 when unsignalled */
                if (timeout == 0xFFFFFFFFu) {
                    WG_LOGW(TAG,
                            "WaitForSingleObjectEx V14: INFINITE wait h=0x%X "
                            "had no runnable peer; returning WAIT_TIMEOUT "
                            "instead of false success",
                            h);
                }
'''
    if old_fallback not in e:
        raise SystemExit("ERROR: V14 WaitForSingleObjectEx fallback anchor changed")
    e = e.replace(old_fallback, new_fallback, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V14: WaitForSingleObjectEx now understands counting semaphores")
    print("V14: successful semaphore waits consume one permit")
    print("V14: unsignalled INFINITE fallback no longer reports false success")
else:
    print("V14: semaphore WaitForSingleObjectEx patch already present")

ev = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "WaitForSingleObjectEx V14(h=",
    "WaitForSingleObjectEx V14 consumed semaphore",
    "mxx_sem_consume(h)",
    "never fake WAIT_OBJECT_0 when unsignalled",
):
    if token not in ev:
        raise SystemExit("ERROR: V14 verification failed: " + token)

print("MXXHUB_SEMAPHORE_WAITEX_FIX_V14_OK")
