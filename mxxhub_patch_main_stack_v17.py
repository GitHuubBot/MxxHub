#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_MAIN_STACK_GROWTH_FIX_V17"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_main_stack_v17.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
impl_p = wg / "Vendor/blink/wg_blink_impl.c"

for p in (engine_p, impl_p):
    if not p.is_file():
        raise SystemExit(f"ERROR: missing {p}")

e = engine_p.read_text(encoding="utf-8")
b = impl_p.read_text(encoding="utf-8")

if MARKER not in e:
    old_limit = "uint64_t stack_limit = 0x7FEF0000ULL;"
    new_limit = "uint64_t stack_limit = 0x7EFF0000ULL; /* MXXHUB_MAIN_STACK_GROWTH_FIX_V17 */"
    if old_limit not in e:
        raise SystemExit("ERROR: V17 TEB StackLimit anchor changed")
    e = e.replace(old_limit, new_limit, 1)

    old_sched = """        mt->stack_base = 0x7FEF0000u;
        mt->stack_size = 0x00100000u;
"""
    new_sched = """        mt->stack_base = 0x7EFF0000u;
        mt->stack_size = 0x01000000u;
        WG_LOGI(TAG,
                "MAIN STACK V17: 16 MiB reserved 0x7EFF0000-0x7FFF0000");
"""
    if old_sched not in e:
        raise SystemExit("ERROR: V17 main scheduler stack anchor changed")
    e = e.replace(old_sched, new_sched, 1)

    old_fallback = """            uint64_t lo = 0x7FEF0000ULL;
            uint64_t hi = 0x7FFF0000ULL;
"""
    new_fallback = """            uint64_t lo = 0x7EFF0000ULL;
            uint64_t hi = 0x7FFF0000ULL;
"""
    if old_fallback not in e:
        raise SystemExit("ERROR: V17 GetCurrentThreadStackLimits fallback anchor changed")
    e = e.replace(old_fallback, new_fallback, 1)

    tls_old = """        } else if (strcmp(fn, "TlsSetValue") == 0) {
            int ti = (engine->scheduler && engine->scheduler->current >= 0)
                     ? engine->scheduler->current : 0;
            if (args[0] < 1088) s_tls_slots[ti][args[0]] = args[1];
            ret_val = 1;
"""
    tls_new = """        } else if (strcmp(fn, "TlsSetValue") == 0) {
            int ti = (engine->scheduler && engine->scheduler->current >= 0)
                     ? engine->scheduler->current : 0;
            if (args[0] < 1088) s_tls_slots[ti][args[0]] = (uint32_t)args[1];

            static unsigned v17_tls_calls = 0;
            v17_tls_calls++;
            if ((v17_tls_calls & 0x7F) == 0 &&
                engine->pe_image && engine->pe_image->is_64bit) {
                uint64_t rsp_now = wg_blink_get_reg(engine->blink, 4);
                uint64_t used = rsp_now < 0x7FFF0000ULL
                    ? 0x7FFF0000ULL - rsp_now : 0;
                long long remain = (long long)(
                    ((int64_t)rsp_now - (int64_t)0x7EFF0000ULL) / 1024LL);
                WG_LOGI(TAG,
                        "STACK V17 waterline: RSP=0x%llX used=%llu KiB "
                        "remaining=%lld KiB tlsCalls=%u",
                        (unsigned long long)rsp_now,
                        (unsigned long long)(used / 1024ULL),
                        remain,
                        v17_tls_calls);
            }
            ret_val = 1;
"""
    if tls_old not in e:
        raise SystemExit("ERROR: V17 TlsSetValue anchor changed")
    e = e.replace(tls_old, tls_new, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V17: engine TEB/scheduler/StackLimits now use 16 MiB main stack")
else:
    print("V17: engine main-stack patch already present")

if MARKER not in b:
    old_blink = "unsigned long long stack_size = 0x100000ULL;"
    new_blink = """unsigned long long stack_size = 0x1000000ULL;
    /* MXXHUB_MAIN_STACK_GROWTH_FIX_V17:
       16 MiB reserve for Unity/Mono main-thread startup. */"""
    if old_blink not in b:
        raise SystemExit("ERROR: V17 Blink stack_size anchor changed")
    b = b.replace(old_blink, new_blink, 1)
    impl_p.write_text(b, encoding="utf-8")
    print("V17: Blink physical main-stack reservation increased to 16 MiB")
else:
    print("V17: Blink stack reservation already patched")

ev = engine_p.read_text(encoding="utf-8")
bv = impl_p.read_text(encoding="utf-8")

checks = (
    (MARKER, ev),
    ("MAIN STACK V17: 16 MiB reserved", ev),
    ("mt->stack_base = 0x7EFF0000u", ev),
    ("mt->stack_size = 0x01000000u", ev),
    ("uint64_t stack_limit = 0x7EFF0000ULL", ev),
    ("STACK V17 waterline", ev),
    ("unsigned long long stack_size = 0x1000000ULL", bv),
    (MARKER, bv),
)
for token, body in checks:
    if token not in body:
        raise SystemExit("ERROR: V17 verification failed: " + token)

print("MXXHUB_MAIN_STACK_GROWTH_FIX_V17_OK")
