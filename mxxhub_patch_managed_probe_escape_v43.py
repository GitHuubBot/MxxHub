#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_managed_probe_escape_v43.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
engine_p = wg / 'Sources/Core/wg_engine.c'
if not engine_p.is_file():
    raise SystemExit(f'ERROR: missing {engine_p}')

s = engine_p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V43_MANAGED_PROBE_ESCAPE_FIX'

# V40 intended to detect Windows paths containing "\\Managed\\", but its
# generated C literal was over-escaped in the first implementation. In C:
#   "\\\\Managed\\\\" matches TWO backslashes on each side.
#   "\\Managed\\"     matches the ONE backslash present in a normal path.
# The bad matcher let V31 turn mscorlib.dll.dll back into mscorlib.dll and
# load the managed assembly as a native PE.
bad = 'strcasestr(load_name, "\\\\\\\\Managed\\\\\\\\") != NULL'
good = 'strcasestr(load_name, "\\\\Managed\\\\") != NULL'

if bad in s:
    s = s.replace(bad, good, 1)

if good not in s:
    raise SystemExit('ERROR: V43 could not find corrected Managed path matcher')

if MARKER not in s:
    anchor = '                bool v40_managed_native_probe =\n'
    if anchor not in s:
        raise SystemExit('ERROR: V43 V40 managed-probe anchor changed')
    s = s.replace(
        anchor,
        '                /* ' + MARKER + ' */\n' + anchor,
        1,
    )

# Give the device log an unambiguous V43 breadcrumb.
s = s.replace(
    'GPA V40 MANAGED-PROBE PRESERVE: \'%s\' -> expected LoadLibrary miss',
    'GPA V43 MANAGED-PROBE PRESERVE: \'%s\' -> expected LoadLibrary miss',
    1,
)

engine_p.write_text(s, encoding='utf-8')

final = engine_p.read_text(encoding='utf-8')
if bad in final:
    raise SystemExit('ERROR: V43 bad double-backslash matcher still present')
for token in (
    MARKER,
    'strcasestr(load_name, "\\\\Managed\\\\") != NULL',
    'GPA V43 MANAGED-PROBE PRESERVE:',
    'v40_managed_native_probe',
):
    if token not in final:
        raise SystemExit('ERROR: V43 verification failed: ' + token)

print('V43: corrected Managed\\*.dll.dll detector to match normal single-backslash Windows paths')
print('V43: Mono managed AOT companion probes will remain real LoadLibrary misses')
print('MXXHUB_WINDOWS_V43_MANAGED_PROBE_ESCAPE_FIX_OK')
