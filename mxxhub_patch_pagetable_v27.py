#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: mxxhub_patch_pagetable_v27.py <WineGlass-root> <Blink-root>"
    )

wg = Path(sys.argv[1]).resolve()
blink = Path(sys.argv[2]).resolve()

bridge_p = wg / "Sources/Core/wg_blink_bridge.c"
if not bridge_p.is_file():
    raise SystemExit(f"ERROR: missing {bridge_p}")
if not blink.is_dir():
    raise SystemExit(f"ERROR: missing Blink root {blink}")

MARKER = "MXXHUB_PAGETABLE_POOL_EXPANSION_V27"

# ---------------------------------------------------------------------------
# 1. Expand Blink's nonlinear backing RAM from 16 MiB to 32 MiB.
#
# The V26 device log hit the custom iOS page-table pool's hard limit:
#   page tables: 512/512
#   AllocatePageTable failed, errno=12
#
# The page tables are physical offsets inside System.real. Expanding kRealSize
# gives the iOS page-table allocator room to grow without changing guest VA
# semantics or allocating thousands of host objects.
# ---------------------------------------------------------------------------
definition_hits = []

for p in sorted((blink / "blink").rglob("*")):
    if not p.is_file() or p.suffix not in {".c", ".h", ".inc"}:
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if "kRealSize" not in text:
        continue

    patterns = [
        re.compile(r'(?m)^(\s*#\s*define\s+kRealSize\s+)([^\r\n/]+)(.*)$'),
        re.compile(
            r'(?m)^(\s*(?:static\s+)?(?:const\s+)?'
            r'(?:unsigned\s+)?(?:long\s+long|long|size_t|u64|int)\s+'
            r'kRealSize\s*=\s*)([^;,\n]+)([;,])'
        ),
        re.compile(r'(?m)^(\s*kRealSize\s*=\s*)([^,\n]+)(,)'),
    ]

    for rx in patterns:
        m = rx.search(text)
        if m:
            definition_hits.append((p, text, rx))
            break

if len(definition_hits) != 1:
    raise SystemExit(
        "ERROR: expected exactly one kRealSize definition, found "
        f"{len(definition_hits)}: {[str(x[0]) for x in definition_hits]}"
    )

kreal_p, kreal_text, kreal_rx = definition_hits[0]
m = kreal_rx.search(kreal_text)
groups = m.groups()

if len(groups) == 3:
    replacement = groups[0] + "(32u * 1024u * 1024u)" + groups[2]
else:
    raise SystemExit("ERROR: unexpected kRealSize regex groups")

kreal_text = kreal_text[:m.start()] + replacement + kreal_text[m.end():]
kreal_p.write_text(kreal_text, encoding="utf-8")

# ---------------------------------------------------------------------------
# 2. Expand the custom iOS page-table pool from 2 MiB / 512 tables to
#    24 MiB / 6144 tables.
#
# Keep the bottom 8 MiB of System.real outside the pool for BIOS/legacy
# physical-memory uses. 6144 tables is enough for the observed ~4 GiB commit
# (about 2050 leaf page tables plus higher levels) with substantial headroom.
# ---------------------------------------------------------------------------
pt_hits = []
for p in sorted((blink / "blink").rglob("*.c")):
    text = p.read_text(encoding="utf-8")
    if "MXXHUB_IOS_PAGETABLE_POOL" in text:
        pt_hits.append((p, text))

if len(pt_hits) != 1:
    raise SystemExit(
        "ERROR: expected exactly one MXXHUB_IOS_PAGETABLE_POOL file, found "
        f"{len(pt_hits)}: {[str(x[0]) for x in pt_hits]}"
    )

pt_p, pt = pt_hits[0]
old_pool = "#define MXX_IOS_PT_POOL_BYTES (2u * 1024u * 1024u)"
new_pool = "#define MXX_IOS_PT_POOL_BYTES (24u * 1024u * 1024u)"
if new_pool not in pt:
    if old_pool not in pt:
        raise SystemExit("ERROR: V27 page-table pool size anchor changed")
    pt = pt.replace(old_pool, new_pool, 1)

pt = pt.replace(
    "correctness-first 4 KiB table pool carved from the top 2 MiB",
    "expanded 4 KiB table pool carved from the top 24 MiB",
)
pt_p.write_text(pt, encoding="utf-8")

# ---------------------------------------------------------------------------
# 3. Put an unmistakable runtime marker in the final Mach-O and device log.
# ---------------------------------------------------------------------------
bridge = bridge_p.read_text(encoding="utf-8")
old_log = (
    'WG_LOGI(TAG, "Blink x86-64 VM created (JIT: %s, real RAM: %llu bytes, '
    'native iOS mmap: %s, page tables: %ld/%ld)",'
)
new_log = (
    'WG_LOGI(TAG, "PAGE TABLE V27 EXPANDED: Blink x86-64 VM created '
    '(JIT: %s, real RAM: %llu bytes, native iOS mmap: %s, '
    'page tables: %ld/%ld)",'
)
if "PAGE TABLE V27 EXPANDED:" not in bridge:
    if old_log not in bridge:
        raise SystemExit("ERROR: V27 Blink success-log anchor changed")
    bridge = bridge.replace(old_log, new_log, 1)

# Add a compact diagnostic when VM creation is successful and capacity is lower
# than expected. This does not alter execution; it only catches stale builds.
success_anchor = (
    'return inst;\n'
)
# Do not inject here because multiple functions may return inst; the creation
# log itself is sufficient proof and already reports used/capacity dynamically.

bridge_p.write_text(bridge, encoding="utf-8")

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
kcheck = kreal_p.read_text(encoding="utf-8")
pcheck = pt_p.read_text(encoding="utf-8")
bcheck = bridge_p.read_text(encoding="utf-8")

if "32u * 1024u * 1024u" not in kcheck:
    raise SystemExit("ERROR: kRealSize was not expanded to 32 MiB")
if new_pool not in pcheck:
    raise SystemExit("ERROR: page-table pool was not expanded to 24 MiB")
if "MXXHUB_IOS_PAGETABLE_POOL" not in pcheck:
    raise SystemExit("ERROR: original iOS page-table allocator disappeared")
if "PAGE TABLE V27 EXPANDED:" not in bcheck:
    raise SystemExit("ERROR: V27 runtime marker missing")

print(f"V27 kRealSize file: {kreal_p}")
print(f"V27 page-table file: {pt_p}")
print("V27 real backing: 32 MiB")
print("V27 page-table pool: 24 MiB = 6144 x 4 KiB tables")
print("MXXHUB_PAGETABLE_POOL_EXPANSION_V27_OK")
