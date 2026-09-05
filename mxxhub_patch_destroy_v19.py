#!/usr/bin/env python3
from pathlib import Path
import re
import sys

MARKER = "MXXHUB_HOST_DESTROY_CRASH_FIX_V19"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_destroy_v19.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
bridge_p = wg / "Sources/Core/wg_blink_bridge.c"
if not bridge_p.is_file():
    raise SystemExit(f"ERROR: missing {bridge_p}")

src = bridge_p.read_text(encoding="utf-8")

def find_function(text: str, name: str):
    m = re.search(
        rf'(?m)^[ \t]*(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t\*]*\b'
        rf'{re.escape(name)}[ \t]*\([^;]*?\)[ \t]*\{{',
        text,
        re.S,
    )
    if not m:
        raise SystemExit(f"ERROR: could not locate C function {name}()")
    brace = text.find("{", m.start(), m.end())
    depth = 0
    i = brace
    in_s = in_c = in_line = in_block = False
    esc = False
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "*" and n == "/":
                in_block = False
                i += 1
        elif in_s:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_s = False
        elif in_c:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == "'":
                in_c = False
        else:
            if c == "/" and n == "/":
                in_line = True
                i += 1
            elif c == "/" and n == "*":
                in_block = True
                i += 1
            elif c == '"':
                in_s = True
            elif c == "'":
                in_c = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        i += 1
    raise SystemExit(f"ERROR: unterminated C function {name}()")

a, b = find_function(src, "wg_blink_destroy")

replacement = r"""void wg_blink_destroy(WGBlinkInstance *inst) {
    /* MXXHUB_HOST_DESTROY_CRASH_FIX_V19
     *
     * iOS stability rule: DO NOT call WGBlinkVM_Destroy here.
     *
     * The guest VM owns mappings created through MxxHub's iOS-specific Blink
     * nonlinear/page-table path. Generic Blink teardown reaches
     * FreeSystem -> RemoveVirtual -> GetPageAddress and asserts on device.
     *
     * For now we intentionally release only the WineGlass wrapper. iOS will
     * reclaim the VM's process memory when MxxHub exits. This trades a bounded
     * per-session VM leak for eliminating a deterministic host SIGSEGV.
     */
    if (!inst) return;
    WG_LOGI(TAG,
            "BLINK DESTROY V19 WRAPPER-ONLY: skipping WGBlinkVM_Destroy "
            "to avoid iOS FreeSystem crash");
    free(inst);
}"""

if MARKER not in src:
    src = src[:a] + replacement + src[b:]
    bridge_p.write_text(src, encoding="utf-8")
    print("V19: replaced Blink teardown with iOS wrapper-only destroy")
else:
    print("V19: host destroy crash fix already present")

final = bridge_p.read_text(encoding="utf-8")
fa, fb = find_function(final, "wg_blink_destroy")
body = final[fa:fb]

for token in (
    MARKER,
    "BLINK DESTROY V19 WRAPPER-ONLY",
    "free(inst);",
):
    if token not in body:
        raise SystemExit("ERROR: V19 verification failed: " + token)

stripped = re.sub(r'/\*.*?\*/|//[^\n]*', '', body, flags=re.S)
if re.search(r'\bWGBlinkVM_Destroy\s*\(', stripped):
    raise SystemExit("ERROR: V19 found executable WGBlinkVM_Destroy call")

print("MXXHUB_HOST_DESTROY_CRASH_FIX_V19_OK")
