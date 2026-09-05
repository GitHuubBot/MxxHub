#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_UNITY_SETFILEPOINTEREX_FIX_V9"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_unity_v9.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
files_c = wg / "Sources/Win32/wg_win32_files.c"
files_h = wg / "Sources/Win32/wg_win32_files.h"

for p in (engine_p, files_c, files_h):
    if not p.is_file():
        raise SystemExit(f"ERROR: expected WineGlass source missing: {p}")

# ---------------------------------------------------------------------------
# V9A — add a real 64-bit file seek helper.
# ---------------------------------------------------------------------------
h = files_h.read_text(encoding="utf-8")
if "wg_files_set_pointer64" not in h:
    anchor = "uint32_t wg_files_set_pointer(uint32_t handle, int32_t distance, uint32_t method);\n"
    if anchor not in h:
        raise SystemExit("ERROR: wg_win32_files.h set-pointer prototype changed")
    h = h.replace(
        anchor,
        anchor +
        "int64_t  wg_files_set_pointer64(uint32_t handle, int64_t distance,\n"
        "                                uint32_t method, bool *ok);\n",
        1,
    )
    files_h.write_text(h, encoding="utf-8")
    print("V9: exported 64-bit file seek helper")

c = files_c.read_text(encoding="utf-8")
if MARKER not in c:
    old = r'''uint32_t wg_files_set_pointer(uint32_t handle, int32_t distance, uint32_t method) {
    WGFileEntry *f = find_file(handle);
    if (!f) return 0xFFFFFFFF;
    int whence;
    switch (method) {
        case 0: whence = SEEK_SET; break;
        case 1: whence = SEEK_CUR; break;
        case 2: whence = SEEK_END; break;
        default: whence = SEEK_SET; break;
    }
    fseek(f->fp, distance, whence);
    return (uint32_t)ftell(f->fp);
}'''
    if old not in c:
        raise SystemExit("ERROR: wg_files_set_pointer body changed before V9")

    new = old + r'''

/* MXXHUB_UNITY_SETFILEPOINTEREX_FIX_V9
 * Win64 Unity uses SetFilePointerEx() while reading boot.config. The old
 * engine had no SetFilePointerEx branch, so the generic thunk returned FALSE.
 * Keep the existing 32-bit helper for legacy callers and add a true 64-bit
 * seek for LARGE_INTEGER offsets / returned positions.
 */
int64_t wg_files_set_pointer64(uint32_t handle, int64_t distance,
                               uint32_t method, bool *ok) {
    WGFileEntry *f = find_file(handle);
    if (ok) *ok = false;
    if (!f || !f->fp) return -1;

    int whence;
    switch (method) {
        case 0: whence = SEEK_SET; break; /* FILE_BEGIN */
        case 1: whence = SEEK_CUR; break; /* FILE_CURRENT */
        case 2: whence = SEEK_END; break; /* FILE_END */
        default:
            errno = EINVAL;
            return -1;
    }

    if (fseeko(f->fp, (off_t)distance, whence) != 0) {
        return -1;
    }

    off_t pos = ftello(f->fp);
    if (pos < 0) return -1;

    if (ok) *ok = true;
    return (int64_t)pos;
}'''
    c = c.replace(old, new, 1)
    files_c.write_text(c, encoding="utf-8")
    print("V9: added true 64-bit SetFilePointerEx backend")
else:
    print("V9: 64-bit file seek backend already present")

# ---------------------------------------------------------------------------
# V9B — implement the actual Win64 SetFilePointerEx API.
# ---------------------------------------------------------------------------
e = engine_p.read_text(encoding="utf-8")

if MARKER not in e:
    anchor = '''        } else if (strcmp(fn, "SetFilePointer") == 0) {
'''
    if anchor not in e:
        raise SystemExit("ERROR: SetFilePointer dispatch anchor changed before V9")

    branch = r'''        } else if (strcmp(fn, "SetFilePointerEx") == 0) {
            /* MXXHUB_UNITY_SETFILEPOINTEREX_FIX_V9
             * BOOL SetFilePointerEx(HANDLE, LARGE_INTEGER distance,
             *                       PLARGE_INTEGER newPos, DWORD method)
             *
             * Hollow Knight is Win64, so RCX/RDX/R8/R9 map directly to the
             * four logical parameters and args[1] already contains the full
             * signed 64-bit LARGE_INTEGER value.
             */
            uint32_t handle = (uint32_t)args[0];
            int64_t distance = (int64_t)args[1];
            uint64_t newpos_ptr = args[2];
            uint32_t method = (uint32_t)args[3];
            bool seek_ok = false;

            int64_t newpos = wg_files_set_pointer64(
                handle, distance, method, &seek_ok);

            if (seek_ok) {
                if (newpos_ptr) {
                    uint64_t outpos = (uint64_t)newpos;
                    if (!wg_blink_write_mem(engine->blink, newpos_ptr,
                                            &outpos, sizeof(outpos))) {
                        WG_LOGE(TAG,
                                "SetFilePointerEx V9: failed writing newPos "
                                "to guest 0x%llX",
                                (unsigned long long)newpos_ptr);
                        ret_val = 0;
                        s_last_error = 998; /* ERROR_NOACCESS */
                    } else {
                        ret_val = 1;
                        s_last_error = 0;
                    }
                } else {
                    ret_val = 1;
                    s_last_error = 0;
                }
            } else {
                ret_val = 0;
                s_last_error = 6; /* ERROR_INVALID_HANDLE / seek failure */
            }

            WG_LOGI(TAG,
                    "SetFilePointerEx V9(h=0x%X dist=%lld method=%u "
                    "newPosPtr=0x%llX) -> %llu pos=%lld err=0x%X",
                    handle, (long long)distance, method,
                    (unsigned long long)newpos_ptr,
                    (unsigned long long)ret_val,
                    (long long)newpos, s_last_error);

'''
    e = e.replace(anchor, branch + anchor, 1)

    # Add boot.config-specific ReadFile visibility. ReadFile is normally a quiet
    # API, so make the actual file consumption visible without flooding logs.
    read_anchor = '''            if (nbytes <= 64 || nread < nbytes) {
                WG_LOGI(TAG, "ReadFile(h=0x%X, pos=%u, n=%u) -> nread=%u first4=0x%08X",
                        handle, pos_before, nbytes, nread, first4);
            }
'''
    if read_anchor not in e:
        raise SystemExit("ERROR: ReadFile diagnostic block changed before V9")

    read_new = read_anchor + r'''            if (handle >= 0x100 && handle < 0x200 &&
                nread > 0 && nread <= 4096) {
                char preview[161];
                uint32_t pn = nread < 160 ? nread : 160;
                memset(preview, 0, sizeof(preview));
                memcpy(preview, tmpbuf ? tmpbuf : (uint8_t *)"", pn);
                for (uint32_t pi = 0; pi < pn; pi++) {
                    unsigned char ch = (unsigned char)preview[pi];
                    if (ch == '\r' || ch == '\n' || ch == '\t') preview[pi] = ' ';
                    else if (ch < 0x20 || ch > 0x7E) preview[pi] = '.';
                }
                WG_LOGI(TAG, "ReadFile V9 preview h=0x%X: '%s'", handle, preview);
            }
'''
    # The upstream block frees tmpbuf before this point, so the above would be
    # unsafe if inserted literally after free. Instead insert preview before free.
    # Replace with a safe in-scope version at the successful read site.
    preview_anchor = '''                    if (nread >= 4) memcpy(&first4, tmpbuf, 4);
                    ret_val = 1;
'''
    preview_insert = r'''                    if (nread >= 4) memcpy(&first4, tmpbuf, 4);
                    if (handle >= 0x100 && handle < 0x200 &&
                        nread > 0 && nread <= 4096) {
                        char preview[161];
                        uint32_t pn = nread < 160 ? nread : 160;
                        memset(preview, 0, sizeof(preview));
                        memcpy(preview, tmpbuf, pn);
                        for (uint32_t pi = 0; pi < pn; pi++) {
                            unsigned char ch = (unsigned char)preview[pi];
                            if (ch == '\r' || ch == '\n' || ch == '\t')
                                preview[pi] = ' ';
                            else if (ch < 0x20 || ch > 0x7E)
                                preview[pi] = '.';
                        }
                        WG_LOGI(TAG, "ReadFile V9 preview h=0x%X: '%s'",
                                handle, preview);
                    }
                    ret_val = 1;
'''
    if preview_anchor not in e:
        raise SystemExit("ERROR: ReadFile success block changed before V9")
    e = e.replace(preview_anchor, preview_insert, 1)

    engine_p.write_text(e, encoding="utf-8")
    print("V9: implemented Win64 SetFilePointerEx + file-read preview")
else:
    print("V9: engine SetFilePointerEx patch already present")

# Capability-based verification.
hv = files_h.read_text(encoding="utf-8")
cv = files_c.read_text(encoding="utf-8")
ev = engine_p.read_text(encoding="utf-8")

for token in (
    "wg_files_set_pointer64",
    "int64_t",
):
    if token not in hv:
        raise SystemExit("ERROR: V9 header verification failed: " + token)

for token in (
    MARKER,
    "wg_files_set_pointer64",
    "fseeko",
    "ftello",
):
    if token not in cv:
        raise SystemExit("ERROR: V9 file backend verification failed: " + token)

for token in (
    MARKER,
    'strcmp(fn, "SetFilePointerEx")',
    "SetFilePointerEx V9",
    "wg_files_set_pointer64",
    "ReadFile V9 preview",
):
    if token not in ev:
        raise SystemExit("ERROR: V9 engine verification failed: " + token)

print("MXXHUB_UNITY_SETFILEPOINTEREX_FIX_V9_OK")
