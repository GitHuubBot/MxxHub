#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_MONO_FILE_MAPPING_FIX_V16"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_mono_filemap_v16.py <WineGlass-root>")

wg = Path(sys.argv[1]).resolve()
engine_p = wg / "Sources/Core/wg_engine.c"
if not engine_p.is_file():
    raise SystemExit(f"ERROR: missing {engine_p}")

e = engine_p.read_text(encoding="utf-8")

# V16 is aimed at the exact new Hollow Knight / Mono failure:
#
#   CreateFileW(...Managed\\mscorlib.dll) -> valid handle
#   GetFileInformationByHandle(...)       -> fake TRUE, no output
#   CreateFileMappingW(...)               -> 0
#   ...fallback probing...
#   crash inside UnityPlayer at 0x626677F5
#
# Mono memory-maps assemblies. WineGlass registered these APIs but had no
# engine implementation, so CreateFileMappingW always returned NULL.

if MARKER not in e:
    # ------------------------------------------------------------------
    # V16A — add a small in-memory file-mapping object table.
    # We snapshot the real file at CreateFileMapping time so the original
    # file HANDLE may be closed immediately, exactly like Windows permits.
    # ------------------------------------------------------------------
    state_anchor = "static uint32_t s_heap_ptr = WG_GUEST_HEAP_BASE;\n"
    if state_anchor not in e:
        raise SystemExit("ERROR: V16 heap-state anchor changed")

    state = r'''static uint32_t s_heap_ptr = WG_GUEST_HEAP_BASE;

/* MXXHUB_MONO_FILE_MAPPING_FIX_V16
 * Minimal read-only section/file-mapping support for Mono assembly loading.
 */
#define MXX_FILEMAP_BASE 0xA000u
#define MXX_MAX_FILEMAPS 32
typedef struct {
    bool in_use;
    uint32_t handle;
    uint8_t *data;
    uint32_t size;
} MxxFileMap;
static MxxFileMap s_mxx_filemaps[MXX_MAX_FILEMAPS];

static MxxFileMap *mxx_filemap_find(uint32_t h) {
    for (int i = 0; i < MXX_MAX_FILEMAPS; ++i)
        if (s_mxx_filemaps[i].in_use && s_mxx_filemaps[i].handle == h)
            return &s_mxx_filemaps[i];
    return NULL;
}

static uint32_t mxx_filemap_create(uint8_t *data, uint32_t size) {
    for (int i = 0; i < MXX_MAX_FILEMAPS; ++i) {
        if (!s_mxx_filemaps[i].in_use) {
            s_mxx_filemaps[i].in_use = true;
            s_mxx_filemaps[i].handle = MXX_FILEMAP_BASE + (uint32_t)i;
            s_mxx_filemaps[i].data = data;
            s_mxx_filemaps[i].size = size;
            return s_mxx_filemaps[i].handle;
        }
    }
    return 0;
}

static bool mxx_filemap_close(uint32_t h) {
    MxxFileMap *fm = mxx_filemap_find(h);
    if (!fm) return false;
    free(fm->data);
    memset(fm, 0, sizeof(*fm));
    return true;
}
'''
    e = e.replace(state_anchor, state, 1)
    print("V16: added read-only file-mapping object table")

    # ------------------------------------------------------------------
    # V16B — HeapReAlloc/realloc integrity.
    #
    # The old implementation copied NEW_SIZE bytes from the OLD allocation.
    # Growing 0x30 -> 0x60 therefore copied 0x30 bytes beyond the old block,
    # importing neighboring heap state into the new object.  This is a direct
    # memory-corruption bug and is especially dangerous during Mono startup.
    # ------------------------------------------------------------------
    old_heap_realloc = r'''        } else if (strcmp(fn, "HeapReAlloc") == 0) {
            // HeapReAlloc(hHeap, dwFlags, lpMem=args[2], dwBytes=args[3])
            uint32_t np = wg_guest_alloc(engine, args[3]);
            if (np && args[2] && args[3]) {
                uint8_t *tmp = malloc(args[3]);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[2], tmp, args[3]);
                    wg_blink_write_mem(engine->blink, np, tmp, args[3]);
                    free(tmp);
                }
            }
            ret_val = np;
'''
    new_heap_realloc = r'''        } else if (strcmp(fn, "HeapReAlloc") == 0) {
            // HeapReAlloc(hHeap, dwFlags, lpMem=args[2], dwBytes=args[3])
            uint32_t oldsz = lookup_alloc_size((uint32_t)args[2]);
            uint32_t newsz = (uint32_t)args[3];
            uint32_t np = wg_guest_alloc(engine, newsz);
            uint32_t copy_n = oldsz < newsz ? oldsz : newsz;
            if (np && args[2] && copy_n) {
                uint8_t *tmp = malloc(copy_n);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[2], tmp, copy_n);
                    wg_blink_write_mem(engine->blink, np, tmp, copy_n);
                    free(tmp);
                }
            }
            static int v16_realloc_logs = 0;
            if (v16_realloc_logs++ < 20) {
                WG_LOGI(TAG,
                        "HeapReAlloc V16(old=0x%llX oldsz=%u newsz=%u) "
                        "-> 0x%X copied=%u",
                        (unsigned long long)args[2], oldsz, newsz, np, copy_n);
            }
            ret_val = np;
'''
    if old_heap_realloc not in e:
        raise SystemExit("ERROR: V16 HeapReAlloc body changed")
    e = e.replace(old_heap_realloc, new_heap_realloc, 1)

    old_realloc = r'''        } else if (strcmp(fn, "realloc") == 0) {
            // Bump allocator can't grow in place; allocate fresh and copy. We
            // don't know the old size, so copy a bounded amount (new size).
            uint32_t np = wg_guest_alloc(engine, args[1]);
            if (np && args[0] && args[1]) {
                uint8_t *tmp = malloc(args[1]);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[0], tmp, args[1]);
                    wg_blink_write_mem(engine->blink, np, tmp, args[1]);
                    free(tmp);
                }
            }
            ret_val = np;
'''
    new_realloc = r'''        } else if (strcmp(fn, "realloc") == 0) {
            // Preserve only bytes that actually belonged to the old block.
            uint32_t oldsz = lookup_alloc_size((uint32_t)args[0]);
            uint32_t newsz = (uint32_t)args[1];
            uint32_t np = wg_guest_alloc(engine, newsz);
            uint32_t copy_n = oldsz < newsz ? oldsz : newsz;
            if (np && args[0] && copy_n) {
                uint8_t *tmp = malloc(copy_n);
                if (tmp) {
                    wg_blink_read_mem(engine->blink, args[0], tmp, copy_n);
                    wg_blink_write_mem(engine->blink, np, tmp, copy_n);
                    free(tmp);
                }
            }
            ret_val = np;
'''
    if old_realloc not in e:
        raise SystemExit("ERROR: V16 realloc body changed")
    e = e.replace(old_realloc, new_realloc, 1)
    print("V16: fixed HeapReAlloc/realloc over-copy corruption")

    # ------------------------------------------------------------------
    # V16C — real GetFileInformationByHandle.
    # Mono uses this before mapping mscorlib.dll.  The old R1S stub returned
    # TRUE without filling BY_HANDLE_FILE_INFORMATION.
    # ------------------------------------------------------------------
    file_anchor = '''        } else if (strcmp(fn, "GetFileSize") == 0) {
'''
    if file_anchor not in e:
        raise SystemExit("ERROR: V16 GetFileSize dispatch anchor changed")

    file_branches = r'''        } else if (strcmp(fn, "GetFileInformationByHandle") == 0) {
            uint32_t h = (uint32_t)args[0];
            uint64_t out = args[1];
            uint32_t sz = wg_files_get_size(h);
            if (out && sz != 0xFFFFFFFFu) {
                /* BY_HANDLE_FILE_INFORMATION = 52 bytes */
                uint8_t fi[52] = {0};
                uint32_t attrs = 0x80; /* FILE_ATTRIBUTE_NORMAL */
                uint32_t vol = 1;
                uint32_t hi = 0;
                uint32_t links = 1;
                uint32_t index_hi = 0;
                uint32_t index_lo = h;
                memcpy(fi + 0,  &attrs,    4);
                memcpy(fi + 28, &vol,      4);
                memcpy(fi + 32, &hi,       4);
                memcpy(fi + 36, &sz,       4);
                memcpy(fi + 40, &links,    4);
                memcpy(fi + 44, &index_hi, 4);
                memcpy(fi + 48, &index_lo, 4);
                if (wg_blink_write_mem(engine->blink, out, fi, sizeof(fi))) {
                    ret_val = 1;
                    s_last_error = 0;
                } else {
                    ret_val = 0;
                    s_last_error = 998; /* ERROR_NOACCESS */
                }
            } else {
                ret_val = 0;
                s_last_error = 6; /* ERROR_INVALID_HANDLE */
            }
            WG_LOGI(TAG,
                    "GetFileInformationByHandle V16(h=0x%X out=0x%llX) "
                    "-> %llu size=%u err=0x%X",
                    h, (unsigned long long)out,
                    (unsigned long long)ret_val, sz, s_last_error);

        } else if (strcmp(fn, "CreateFileMappingW") == 0) {
            uint32_t h = (uint32_t)args[0];
            uint32_t protect = (uint32_t)args[2];
            uint64_t requested =
                ((uint64_t)(uint32_t)args[3] << 32) |
                (uint64_t)(uint32_t)args[4];

            uint32_t file_sz = wg_files_get_size(h);
            uint64_t map_sz64 = requested ? requested : (uint64_t)file_sz;

            if (file_sz == 0xFFFFFFFFu || map_sz64 == 0 ||
                map_sz64 > 256ULL * 1024ULL * 1024ULL) {
                ret_val = 0;
                s_last_error = (file_sz == 0xFFFFFFFFu) ? 6 : 8;
            } else {
                uint32_t map_sz = (uint32_t)map_sz64;
                uint8_t *snapshot = calloc(1, map_sz);
                if (!snapshot) {
                    ret_val = 0;
                    s_last_error = 8; /* ERROR_NOT_ENOUGH_MEMORY */
                } else {
                    uint32_t oldpos = wg_files_set_pointer(h, 0, 1);
                    wg_files_set_pointer(h, 0, 0);
                    uint32_t to_read = file_sz < map_sz ? file_sz : map_sz;
                    uint32_t got = 0;
                    bool ok = (to_read == 0) ||
                        wg_files_read(h, snapshot, to_read, &got);
                    if (oldpos != 0xFFFFFFFFu)
                        wg_files_set_pointer(h, (int32_t)oldpos, 0);

                    if (!ok || got != to_read) {
                        free(snapshot);
                        ret_val = 0;
                        s_last_error = 5; /* ERROR_ACCESS_DENIED / I/O failure */
                    } else {
                        uint32_t mh = mxx_filemap_create(snapshot, map_sz);
                        if (!mh) {
                            free(snapshot);
                            ret_val = 0;
                            s_last_error = 4; /* ERROR_TOO_MANY_OPEN_FILES */
                        } else {
                            ret_val = mh;
                            s_last_error = 0;
                        }
                    }
                }
            }

            WG_LOGI(TAG,
                    "CreateFileMappingW V16(file=0x%X protect=0x%X "
                    "requested=%llu fileSize=%u) -> hMap=0x%llX err=0x%X",
                    h, protect, (unsigned long long)requested, file_sz,
                    (unsigned long long)ret_val, s_last_error);

        } else if (strcmp(fn, "MapViewOfFile") == 0) {
            uint32_t mh = (uint32_t)args[0];
            uint32_t access = (uint32_t)args[1];
            uint64_t off =
                ((uint64_t)(uint32_t)args[2] << 32) |
                (uint64_t)(uint32_t)args[3];
            uint64_t want = args[4];

            MxxFileMap *fm = mxx_filemap_find(mh);
            if (!fm || off >= fm->size) {
                ret_val = 0;
                s_last_error = fm ? 87 : 6;
            } else {
                uint64_t available = (uint64_t)fm->size - off;
                uint64_t view_sz64 = want ? want : available;
                if (view_sz64 > available)
                    view_sz64 = available;

                if (view_sz64 == 0 || view_sz64 > 0x7FFFFFFFULL) {
                    ret_val = 0;
                    s_last_error = 8;
                } else {
                    uint32_t view_sz = (uint32_t)view_sz64;
                    uint32_t guest = wg_guest_alloc(engine, view_sz);
                    if (!guest ||
                        !wg_blink_write_mem(engine->blink, guest,
                                            fm->data + (uint32_t)off,
                                            view_sz)) {
                        ret_val = 0;
                        s_last_error = 8;
                    } else {
                        ret_val = guest;
                        s_last_error = 0;
                    }
                }
            }

            WG_LOGI(TAG,
                    "MapViewOfFile V16(hMap=0x%X access=0x%X off=%llu "
                    "want=%llu) -> 0x%llX err=0x%X",
                    mh, access, (unsigned long long)off,
                    (unsigned long long)want,
                    (unsigned long long)ret_val, s_last_error);

        } else if (strcmp(fn, "UnmapViewOfFile") == 0) {
            /* Guest heap is a bump allocator, so the view remains mapped but
               becomes unreachable. Windows callers only require TRUE here. */
            WG_LOGI(TAG, "UnmapViewOfFile V16(base=0x%llX) -> TRUE",
                    (unsigned long long)args[0]);
            ret_val = 1;
            s_last_error = 0;

'''
    e = e.replace(file_anchor, file_branches + file_anchor, 1)
    print("V16: implemented GetFileInformationByHandle + file mapping APIs")

    # Mapping HANDLEs must be closable independently of the source file.
    old_close = r'''        } else if (strcmp(fn, "CloseHandle") == 0) {
            wg_files_close(args[0]);
            ret_val = 1;
'''
    new_close = r'''        } else if (strcmp(fn, "CloseHandle") == 0) {
            uint32_t ch = (uint32_t)args[0];
            if (mxx_filemap_close(ch)) {
                WG_LOGI(TAG, "CloseHandle V16 mapping h=0x%X", ch);
                ret_val = 1;
            } else {
                wg_files_close(ch);
                ret_val = 1;
            }
'''
    if old_close not in e:
        raise SystemExit("ERROR: V16 CloseHandle body changed")
    e = e.replace(old_close, new_close, 1)

    engine_p.write_text(e, encoding="utf-8")
else:
    print("V16: Mono file mapping patch already present")

ev = engine_p.read_text(encoding="utf-8")
for token in (
    MARKER,
    "CreateFileMappingW V16",
    "MapViewOfFile V16",
    "UnmapViewOfFile V16",
    "GetFileInformationByHandle V16",
    "HeapReAlloc V16",
    "mxx_filemap_find",
):
    if token not in ev:
        raise SystemExit("ERROR: V16 verification failed: " + token)

print("MXXHUB_MONO_FILE_MAPPING_FIX_V16_OK")
