#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_critsec_backoff_v38.py <WineGlass-root>")
wg = Path(sys.argv[1]).resolve()
p = wg / "Sources/Core/wg_engine.c"
if not p.is_file():
    raise SystemExit(f"ERROR: missing {p}")
s = p.read_text(encoding="utf-8")
MARKER = "MXXHUB_CRITSEC_ADAPTIVE_BACKOFF_V38"

if MARKER not in s:
    old = '''            if ((v25_same_cs_count & 255ULL) == 0 || switched) {
                WG_LOGI(TAG,
                        "CRITSEC V25 FAIR YIELD: cs=0x%X same=%llu total=%llu switched=%d",
                        cs,
                        (unsigned long long)v25_same_cs_count,
                        (unsigned long long)v25_leave_count,
                        (int)switched);
            }

            if (switched) return true;'''
    new = '''            /* MXXHUB_CRITSEC_ADAPTIVE_BACKOFF_V38
             * Keep V25's cooperative yield, but do not log every successful
             * switch. Mono's bad JIT/unwind state produced thousands of those
             * lines and amplified CPU/log pressure. If a same-lock poll still
             * lasts thousands of iterations, use a tiny host backoff too.
             */
            if ((v25_same_cs_count & 2047ULL) == 0) {
                WG_LOGI(TAG,
                        "CRITSEC V38 BACKOFF: cs=0x%X same=%llu total=%llu switched=%d",
                        cs,
                        (unsigned long long)v25_same_cs_count,
                        (unsigned long long)v25_leave_count,
                        (int)switched);
            }
            if (v25_same_cs_count >= 4096 &&
                (v25_same_cs_count & 255ULL) == 0) {
                usleep(250);
            }

            if (switched) return true;'''
    if old not in s:
        raise SystemExit("ERROR: V38 critical-section V25 block changed")
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("V38: critical-section fair-yield logging/backoff hardened")
else:
    print("V38: critical-section adaptive backoff already present")

final = p.read_text(encoding="utf-8")
for token in (MARKER, "CRITSEC V38 BACKOFF:", "usleep(250)"):
    if token not in final:
        raise SystemExit("ERROR: V38 critsec verification failed: " + token)
print("MXXHUB_CRITSEC_ADAPTIVE_BACKOFF_V38_OK")
