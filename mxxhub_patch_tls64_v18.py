#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_TLS64_WORKER_FIX_V18"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_tls64_v18.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

e = engine_p.read_text(encoding="utf-8")

# Device proof of the bug:
#   TlsSetValue(slot=1, value=0x10E0400630)
#   TlsGetValue(slot=1) -> 0xE0400630
#
# The x64 boot patch already uses uint64_t TLS storage. V17 accidentally
# cast the stored pointer back to uint32_t while adding diagnostics.

if "static uint64_t s_tls_slots[WG_MAX_THREADS][1088]" not in e:
    raise SystemExit("ERROR: TLS slot storage is not uint64_t before V18")

old = "if (args[0] < 1088) s_tls_slots[ti][args[0]] = (uint32_t)args[1];"
new = r'''if (args[0] < 1088) {
                s_tls_slots[ti][args[0]] = args[1]; /* MXXHUB_TLS64_WORKER_FIX_V18 */
                static unsigned v18_tls64_logs = 0;
                if ((args[1] >> 32) && v18_tls64_logs++ < 32) {
                    WG_LOGI(TAG,
                            "TLS64 V18 SET: tid=%d slot=%llu value=0x%llX "
                            "(high32=0x%llX preserved)",
                            ti,
                            (unsigned long long)args[0],
                            (unsigned long long)args[1],
                            (unsigned long long)(args[1] >> 32));
                }
            }'''

if MARKER not in e:
    if old not in e:
        raise SystemExit("ERROR: V18 TlsSetValue truncation anchor changed")
    e = e.replace(old, new, 1)
    engine_p.write_text(e, encoding="utf-8")
    print("V18: removed V17 uint32_t TLS pointer truncation")
else:
    print("V18: TLS64 worker fix already present")

ev = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "static uint64_t s_tls_slots[WG_MAX_THREADS][1088]",
    "s_tls_slots[ti][args[0]] = args[1]",
    "TLS64 V18 SET:",
):
    if token not in ev:
        raise SystemExit("ERROR: V18 verification failed: " + token)

if "s_tls_slots[ti][args[0]] = (uint32_t)args[1]" in ev:
    raise SystemExit("ERROR: V18 verification found surviving TLS truncation")

print("MXXHUB_TLS64_WORKER_FIX_V18_OK")
