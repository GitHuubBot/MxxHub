#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_fileattributesex_v34.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

s = engine_p.read_text(encoding="utf-8")
MARKER = "MXXHUB_WINDOWS_V34_FILEATTRIBUTES_EX"

if MARKER not in s:
    anchor = '''        } else if (strcmp(fn, "GetFileAttributesW") == 0) {
            uint16_t wpath[260] = {0};
'''

    replacement = r'''        } else if (strcmp(fn, "GetFileAttributesExW") == 0) {
            /*
             * MXXHUB_WINDOWS_V34_FILEATTRIBUTES_EX
             *
             * V33 reaches GetFileAttributesExW immediately before Unity shows
             * "Failed to initialize player". WineGlass had no implementation
             * for this API, so Unity always received FALSE here.
             */
            uint64_t path_ptr = args[0];
            uint32_t info_level = (uint32_t)args[1];
            uint64_t out_ptr = args[2];

            uint16_t wpath[520] = {0};
            char apath[520] = {0};

            if (path_ptr) {
                wg_blink_read_mem(engine->blink, path_ptr,
                                  wpath, sizeof(wpath) - 2);
                for (int i = 0; i < 519 && wpath[i]; i++)
                    apath[i] = wpath[i] < 128 ? (char)wpath[i] : '_';
            }

            const char *real = wg_files_map_path(
                path_ptr, engine->blink, apath, sizeof(apath));

            struct stat st;
            bool exists = real && stat(real, &st) == 0;

            if (exists && out_ptr && info_level == 0) {
                uint8_t data[36];
                memset(data, 0, sizeof(data));

                uint32_t attrs = S_ISDIR(st.st_mode) ? 0x10u : 0x80u;
                uint64_t filetime =
                    (uint64_t)st.st_mtime * 10000000ULL +
                    116444736000000000ULL;
                uint64_t fsize = S_ISDIR(st.st_mode)
                    ? 0ULL : (uint64_t)st.st_size;
                uint32_t size_hi = (uint32_t)(fsize >> 32);
                uint32_t size_lo = (uint32_t)fsize;

                memcpy(data + 0,  &attrs,    4);
                memcpy(data + 4,  &filetime, 8);
                memcpy(data + 12, &filetime, 8);
                memcpy(data + 20, &filetime, 8);
                memcpy(data + 28, &size_hi,  4);
                memcpy(data + 32, &size_lo,  4);

                wg_blink_write_mem(engine->blink, out_ptr,
                                   data, sizeof(data));

                s_last_error = 0;
                ret_val = 1;

                WG_LOGI(TAG,
                        "FILEATTR V34 HIT: ptr=0x%llX path='%s' "
                        "attrs=0x%X size=%llu -> TRUE",
                        (unsigned long long)path_ptr,
                        apath,
                        attrs,
                        (unsigned long long)fsize);
            } else {
                s_last_error = exists ? 87 : 2;
                ret_val = 0;

                WG_LOGI(TAG,
                        "FILEATTR V34 MISS: ptr=0x%llX path='%s' "
                        "level=%u out=0x%llX real='%s' err=%u -> FALSE",
                        (unsigned long long)path_ptr,
                        apath,
                        info_level,
                        (unsigned long long)out_ptr,
                        real ? real : "(null)",
                        s_last_error);
            }

        } else if (strcmp(fn, "GetFileAttributesExA") == 0) {
            uint64_t path_ptr = args[0];
            uint32_t info_level = (uint32_t)args[1];
            uint64_t out_ptr = args[2];

            char apath[520] = {0};
            if (path_ptr)
                wg_blink_read_mem(engine->blink, path_ptr,
                                  apath, sizeof(apath) - 1);

            const char *real = wg_files_map_path(
                path_ptr, engine->blink, apath, sizeof(apath));

            struct stat st;
            bool exists = real && stat(real, &st) == 0;

            if (exists && out_ptr && info_level == 0) {
                uint8_t data[36];
                memset(data, 0, sizeof(data));

                uint32_t attrs = S_ISDIR(st.st_mode) ? 0x10u : 0x80u;
                uint64_t filetime =
                    (uint64_t)st.st_mtime * 10000000ULL +
                    116444736000000000ULL;
                uint64_t fsize = S_ISDIR(st.st_mode)
                    ? 0ULL : (uint64_t)st.st_size;
                uint32_t size_hi = (uint32_t)(fsize >> 32);
                uint32_t size_lo = (uint32_t)fsize;

                memcpy(data + 0,  &attrs,    4);
                memcpy(data + 4,  &filetime, 8);
                memcpy(data + 12, &filetime, 8);
                memcpy(data + 20, &filetime, 8);
                memcpy(data + 28, &size_hi,  4);
                memcpy(data + 32, &size_lo,  4);

                wg_blink_write_mem(engine->blink, out_ptr,
                                   data, sizeof(data));

                s_last_error = 0;
                ret_val = 1;

                WG_LOGI(TAG,
                        "FILEATTR V34 HIT A: path='%s' attrs=0x%X "
                        "size=%llu -> TRUE",
                        apath, attrs, (unsigned long long)fsize);
            } else {
                s_last_error = exists ? 87 : 2;
                ret_val = 0;
                WG_LOGI(TAG,
                        "FILEATTR V34 MISS A: path='%s' level=%u err=%u",
                        apath, info_level, s_last_error);
            }

        } else if (strcmp(fn, "GetFileAttributesW") == 0) {
            uint16_t wpath[260] = {0};
'''

    if anchor not in s:
        raise SystemExit("ERROR: V34 GetFileAttributesW insertion anchor changed")
    s = s.replace(anchor, replacement, 1)

    engine_p.write_text(s, encoding="utf-8")
    print("Windows V34: GetFileAttributesExA/W implemented")
else:
    print("Windows V34: patch already present")

final = engine_p.read_text(encoding="utf-8")
required = (
    MARKER,
    'strcmp(fn, "GetFileAttributesExW") == 0',
    'strcmp(fn, "GetFileAttributesExA") == 0',
    "FILEATTR V34 HIT:",
    "FILEATTR V34 MISS:",
    "uint8_t data[36];",
    "wg_blink_write_mem(engine->blink, out_ptr",
)

for token in required:
    if token not in final:
        raise SystemExit("ERROR: V34 verification failed: " + token)

print("MXXHUB_WINDOWS_V34_FILEATTRIBUTES_EX_FIX_OK")
