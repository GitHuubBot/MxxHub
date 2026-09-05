#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_FORCED_JOB_SYSTEM_V11"
BANNER = "MXXHUB RUNTIME V11 ACTIVE"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v11.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
mapper_p = wg / "Sources/Win32/wg_dll_mapper.c"

for p in (engine_p, mapper_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

e = engine_p.read_text(encoding="utf-8")
m = mapper_p.read_text(encoding="utf-8")

required_v10_engine = (
    "GetLogicalProcessorInformationEx V10",
    "MXX_SEM_BASE",
    "mxx_sem_consume",
    'strcasestr(ascii, "psapi")',
)
for token in required_v10_engine:
    if token not in e:
        raise SystemExit(
            "ERROR: V11 requires V10 engine patch but token is missing: " + token
        )

required_v10_mapper = (
    "GetLogicalProcessorInformationEx",
    "CreateSemaphoreExW",
    'RS ("PSAPI.dll", EnumProcessModules',
)
for token in required_v10_mapper:
    if token not in m:
        raise SystemExit(
            "ERROR: V11 requires V10 mapper patch but token is missing: " + token
        )

if BANNER not in e:
    anchor = "    e->scheduler = wg_sched_create();\n"
    if anchor not in e:
        raise SystemExit("ERROR: wg_engine_create scheduler anchor changed before V11")

    banner_code = '''    e->scheduler = wg_sched_create();
    /* MXXHUB_FORCED_JOB_SYSTEM_V11 */
    WG_LOGI(TAG, "MXXHUB RUNTIME V11 ACTIVE — job-system shim compiled in");
'''
    e = e.replace(anchor, banner_code, 1)

e = e.replace(
    "GetLogicalProcessorInformationEx V10(rel=%u)",
    "GetLogicalProcessorInformationEx V11(rel=%u)"
)
e = e.replace(
    "%s V10(initial=%d max=%d) -> h=0x%X err=0x%X",
    "%s V11(initial=%d max=%d) -> h=0x%X err=0x%X"
)
e = e.replace(
    "ReleaseSemaphore V10(h=0x%X release=%d) -> %llu ",
    "ReleaseSemaphore V11(h=0x%X release=%d) -> %llu "
)

if MARKER not in e:
    dispatch_anchor = '''        } else if (strcmp(fn, "GetLogicalProcessorInformationEx") == 0) {
'''
    if dispatch_anchor not in e:
        raise SystemExit(
            "ERROR: V11 cannot find GetLogicalProcessorInformationEx dispatch"
        )
    dispatch_replacement = '''        } else if (strcmp(fn, "GetLogicalProcessorInformationEx") == 0) {
            /* MXXHUB_FORCED_JOB_SYSTEM_V11 */
'''
    e = e.replace(dispatch_anchor, dispatch_replacement, 1)

engine_p.write_text(e, encoding="utf-8")

ev = engine_p.read_text(encoding="utf-8")
mv = mapper_p.read_text(encoding="utf-8")

for token in (
    MARKER,
    BANNER,
    "GetLogicalProcessorInformationEx V11",
    "V11(initial=%d max=%d)",
    "ReleaseSemaphore V11",
    "MXX_SEM_BASE",
    'strcasestr(ascii, "psapi")',
):
    if token not in ev:
        raise SystemExit("ERROR: V11 engine verification failed: " + token)

for token in (
    "GetLogicalProcessorInformationEx",
    "CreateSemaphoreExW",
    'RS ("PSAPI.dll", EnumProcessModules',
):
    if token not in mv:
        raise SystemExit("ERROR: V11 mapper verification failed: " + token)

print("V11: V10.1 job-system shim verified in generated WineGlass source")
print("V11: unique runtime fingerprint embedded")
print("MXXHUB_FORCED_JOB_SYSTEM_V11_OK")
