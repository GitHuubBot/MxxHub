#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_unity_dialog_waitaddr_v41.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
engine_p = wg / 'Sources/Core/wg_engine.c'
if not engine_p.is_file():
    raise SystemExit(f'ERROR: missing {engine_p}')

s = engine_p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V41_X64_DIALOG_AND_WAITADDR_FILTER'

if MARKER not in s:
    # 1) WineGlass' DialogBoxParamW path is an NSIS/Steam-installer path. It
    # hardcodes a "Steam Setup" title and, for x64, falls through to PAUSED.
    # UnityPlayer.dll also calls DialogBoxParamW (resource 105) during startup.
    # For a Win64 game this must not be routed through the 32-bit NSIS modal UI.
    dialog_anchor = '} else if (strcmp(fn, "DialogBoxParamW") == 0) {\n'
    dialog_repl = '''} else if (strcmp(fn, "DialogBoxParamW") == 0 && !is_32bit) {\n            /* MXXHUB_WINDOWS_V41_X64_DIALOG_AND_WAITADDR_FILTER\n             * WineGlass' legacy dialog renderer is for 32-bit NSIS/Steam setup.\n             * UnityPlayer x64 also calls DialogBoxParamW (observed resource 105).\n             * Routing that call into the installer path creates a fake\n             * "Steam Setup" guest window and PAUSES the whole x64 engine.\n             * Treat the unsupported x64 modal as acknowledged instead: no fake\n             * window, no installer template parser, and crucially no engine pause.\n             */\n            uint32_t dlg_id_v41 = (uint32_t)args[1];\n            WG_LOGI(TAG,\n                    "DIALOG V41 X64 BYPASS: id=%u hinst=0x%llX dlgproc=0x%llX -> IDOK; no Steam/NSIS window",\n                    dlg_id_v41,\n                    (unsigned long long)args[0],\n                    (unsigned long long)args[3]);\n            ret_val = 1; /* IDOK */\n        } else if (strcmp(fn, "DialogBoxParamW") == 0) {\n'''
    if dialog_anchor not in s:
        raise SystemExit('ERROR: V41 DialogBoxParamW anchor changed')
    s = s.replace(dialog_anchor, dialog_repl, 1)

    # 2) Keep the timeout associated with each parked WaitOnAddress slot. V39
    # woke *every* INFINITE worker in round-robin order; the latest device log
    # shows the entire Unity worker pool being resumed/re-parked after 5/5.
    globals_anchor = 'static uint8_t  s_mxx_waitaddr_size[WG_MAX_THREADS] = {0};\n'
    globals_repl = globals_anchor + 'static uint32_t s_mxx_waitaddr_timeout[WG_MAX_THREADS] = {0}; /* V41 */\n'
    if globals_anchor not in s:
        raise SystemExit('ERROR: V41 waitaddr globals anchor changed')
    s = s.replace(globals_anchor, globals_repl, 1)

    park_anchor = '''                    s_mxx_waitaddr_addr[wait_slot] = addr;\n                    s_mxx_waitaddr_size[wait_slot] = (uint8_t)size;\n'''
    park_repl = park_anchor + '                    s_mxx_waitaddr_timeout[wait_slot] = timeout; /* V41 */\n'
    if park_anchor not in s:
        raise SystemExit('ERROR: V41 waitaddr park anchor changed')
    s = s.replace(park_anchor, park_repl, 1)

    resume_anchor = '''                s_mxx_waitaddr_spurious[wait_slot] = false;\n                s_mxx_waitaddr_active[wait_slot] = false;\n                s_last_error = 0;\n'''
    resume_repl = '''                s_mxx_waitaddr_spurious[wait_slot] = false;\n                s_mxx_waitaddr_active[wait_slot] = false;\n                s_mxx_waitaddr_timeout[wait_slot] = 0;\n                s_last_error = 0;\n'''
    if resume_anchor not in s:
        raise SystemExit('ERROR: V41 waitaddr resume anchor changed')
    s = s.replace(resume_anchor, resume_repl, 1)

    wake_anchor = '''                    s_mxx_waitaddr_active[wi] = false;\n                    s_mxx_waitaddr_spurious[wi] = false;\n                    woke++;\n'''
    wake_repl = '''                    s_mxx_waitaddr_active[wi] = false;\n                    s_mxx_waitaddr_spurious[wi] = false;\n                    s_mxx_waitaddr_timeout[wi] = 0;\n                    woke++;\n'''
    if wake_anchor not in s:
        raise SystemExit('ERROR: V41 real wake anchor changed')
    s = s.replace(wake_anchor, wake_repl, 1)

    # Never manufacture spurious wakeups for ordinary INFINITE worker waits.
    # Only the observed 0xFFFFFFFE coordinator-style wait is eligible for the
    # host nudge. This reduces a 19+ worker wake storm to at most the special
    # near-infinite coordinator waiters while real WakeByAddress still wins.
    tick_anchor = '''                if (!s_mxx_waitaddr_active[wi] || wt->state != WG_THREAD_WAITING)\n                    continue;\n                s_mxx_waitaddr_spurious[wi] = true;\n                wt->state = WG_THREAD_READY;\n'''
    tick_repl = '''                if (!s_mxx_waitaddr_active[wi] || wt->state != WG_THREAD_WAITING)\n                    continue;\n                if (s_mxx_waitaddr_timeout[wi] == 0xFFFFFFFFu)\n                    continue; /* V41: do not churn the ordinary infinite worker pool */\n                s_mxx_waitaddr_spurious[wi] = true;\n                wt->state = WG_THREAD_READY;\n'''
    if tick_anchor not in s:
        raise SystemExit('ERROR: V41 V39 host-wake anchor changed')
    s = s.replace(tick_anchor, tick_repl, 1)

    log_anchor = '"WAITADDR V39 HOST-WAKE: slot=%d tid=0x%X addr=0x%llX poll=%llu -> READY",\n'
    log_repl = '"WAITADDR V41 COORD-WAKE: slot=%d tid=0x%X addr=0x%llX poll=%llu -> READY",\n'
    if log_anchor not in s:
        raise SystemExit('ERROR: V41 host-wake log anchor changed')
    s = s.replace(log_anchor, log_repl, 1)

    engine_p.write_text(s, encoding='utf-8')
    print('V41: Win64 DialogBoxParamW bypasses legacy Steam/NSIS modal UI')
    print('V41: ordinary INFINITE WaitOnAddress workers remain parked; only near-infinite coordinators receive host nudges')
else:
    print('V41: patch already present')

final = engine_p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'DIALOG V41 X64 BYPASS:',
    'no Steam/NSIS window',
    's_mxx_waitaddr_timeout',
    's_mxx_waitaddr_timeout[wi] == 0xFFFFFFFFu',
    'WAITADDR V41 COORD-WAKE:',
):
    if token not in final:
        raise SystemExit('ERROR: V41 verification failed: ' + token)

print('MXXHUB_WINDOWS_V41_X64_DIALOG_AND_WAITADDR_FILTER_OK')
