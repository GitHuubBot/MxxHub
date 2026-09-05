#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_dxgi_bootstrap_v45.py <WineGlass-root>')
wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file(): raise SystemExit(f'ERROR: missing {p}')
s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V45_DXGI_FACTORY_ADAPTER_BOOTSTRAP'

if MARKER not in s:
    global_anchor = '// Check if RIP is in the thunk range and handle the Win32 API call.\n'
    if global_anchor not in s:
        raise SystemExit('ERROR: V45 global anchor changed')
    globals_block = r'''/* MXXHUB_WINDOWS_V45_DXGI_FACTORY_ADAPTER_BOOTSTRAP
 * Minimal guest-side DXGI COM bootstrap. This is intentionally NOT a fake
 * D3D11 renderer: it supplies the factory + adapter enumeration contract that
 * Unity expects before it asks D3D11CreateDevice for an actual device.
 */
static uint64_t s_v45_dxgi_factory = 0;
static uint64_t s_v45_dxgi_adapter = 0;
static uint32_t s_v45_factory_refs = 1;
static uint32_t s_v45_adapter_refs = 1;

'''
    s = s.replace(global_anchor, globals_block + global_anchor, 1)

    dispatch_anchor = '        /* MXXHUB_WINDOWS_V44_UNITY_D3D11_GRAPHICS_ROUTE */\n'
    if dispatch_anchor not in s:
        raise SystemExit('ERROR: V45 dispatch anchor changed')
    dispatch = r'''        /* V45 DXGI COM thunk handlers. These functions are registered lazily
         * when CreateDXGIFactory* is first called; map_thunks_to_blink already
         * filled the complete 128 KiB thunk area with HLT, so late mapper
         * entries are safe inside that reserved region. */
        if (strcmp(fn, "MxxDXGI_QueryInterface") == 0) {
            uint64_t self = args[0];
            if (args[2]) wg_blink_write_mem(engine->blink, args[2], &self, 8);
            ret_val = 0; /* S_OK */
        } else if (strcmp(fn, "MxxDXGIFactory_AddRef") == 0) {
            ret_val = ++s_v45_factory_refs;
        } else if (strcmp(fn, "MxxDXGIFactory_Release") == 0) {
            if (s_v45_factory_refs > 1) s_v45_factory_refs--;
            ret_val = s_v45_factory_refs;
        } else if (strcmp(fn, "MxxDXGIAdapter_AddRef") == 0) {
            ret_val = ++s_v45_adapter_refs;
        } else if (strcmp(fn, "MxxDXGIAdapter_Release") == 0) {
            if (s_v45_adapter_refs > 1) s_v45_adapter_refs--;
            ret_val = s_v45_adapter_refs;
        } else if (strcmp(fn, "MxxDXGIFactory_EnumAdapters") == 0 ||
                   strcmp(fn, "MxxDXGIFactory_EnumAdapters1") == 0) {
            uint32_t index = (uint32_t)args[1];
            if (index == 0 && args[2] && s_v45_dxgi_adapter) {
                uint64_t a = s_v45_dxgi_adapter;
                wg_blink_write_mem(engine->blink, args[2], &a, 8);
                s_v45_adapter_refs++;
                WG_LOGI(TAG, "GFX V45 DXGI ADAPTER: %s index=0 -> 0x%llX",
                        fn, (unsigned long long)a);
                ret_val = 0; /* S_OK */
            } else {
                if (args[2]) { uint64_t z = 0; wg_blink_write_mem(engine->blink, args[2], &z, 8); }
                ret_val = 0x887A0002u; /* DXGI_ERROR_NOT_FOUND */
            }
        } else if (strcmp(fn, "MxxDXGIFactory_MakeWindowAssociation") == 0) {
            ret_val = 0; /* S_OK */
        } else if (strcmp(fn, "MxxDXGIFactory_GetWindowAssociation") == 0) {
            if (args[1]) { uint64_t z = 0; wg_blink_write_mem(engine->blink, args[1], &z, 8); }
            ret_val = 0;
        } else if (strcmp(fn, "MxxDXGIFactory_IsCurrent") == 0) {
            ret_val = 1; /* TRUE */
        } else if (strcmp(fn, "MxxDXGIAdapter_EnumOutputs") == 0) {
            if (args[2]) { uint64_t z = 0; wg_blink_write_mem(engine->blink, args[2], &z, 8); }
            ret_val = 0x887A0002u; /* no physical Win32 output object yet */
        } else if (strcmp(fn, "MxxDXGIAdapter_GetDesc") == 0 ||
                   strcmp(fn, "MxxDXGIAdapter_GetDesc1") == 0 ||
                   strcmp(fn, "MxxDXGIAdapter_GetDesc2") == 0) {
            if (!args[1]) {
                ret_val = 0x80070057u; /* E_INVALIDARG */
            } else {
                uint8_t desc[320]; memset(desc, 0, sizeof(desc));
                const char *name = "MxxHub Metal Adapter";
                for (int i = 0; name[i] && i < 127; i++) {
                    uint16_t ch = (uint8_t)name[i];
                    memcpy(desc + i * 2, &ch, 2);
                }
                uint32_t vendor = 0x106B; /* Apple */
                uint32_t device = 1;
                memcpy(desc + 256, &vendor, 4);
                memcpy(desc + 260, &device, 4);
                uint64_t dedicated = 512ULL * 1024ULL * 1024ULL;
                uint64_t shared = 1024ULL * 1024ULL * 1024ULL;
                memcpy(desc + 272, &dedicated, 8);
                memcpy(desc + 288, &shared, 8);
                uint32_t luid_lo = 0x4D585848u; /* 'MXXH' */
                uint32_t luid_hi = 1;
                memcpy(desc + 296, &luid_lo, 4); memcpy(desc + 300, &luid_hi, 4);
                wg_blink_write_mem(engine->blink, args[1], desc, sizeof(desc));
                WG_LOGI(TAG, "GFX V45 DXGI DESC: %s -> MxxHub Metal Adapter vendor=0x106B",
                        fn);
                ret_val = 0;
            }
        } else if (strcmp(fn, "MxxDXGI_NotImpl") == 0) {
            static uint32_t n = 0;
            if (n++ < 16) WG_LOGW(TAG, "GFX V45 DXGI METHOD: unimplemented COM slot called; returning E_NOTIMPL");
            ret_val = 0x80004001u; /* E_NOTIMPL */
        } else '''
    s = s.replace(dispatch_anchor, dispatch + dispatch_anchor, 1)

    old_factory = r'''        } else if (strcmp(fn, "CreateDXGIFactory") == 0 || strcmp(fn, "CreateDXGIFactory2") == 0) {
            uint64_t outp = strcmp(fn, "CreateDXGIFactory2") == 0 ? args[2] : args[1];
            uint64_t zero64 = 0; uint32_t zero32 = 0;
            if (outp) wg_blink_write_mem(engine->blink, outp, is_32bit ? (void *)&zero32 : (void *)&zero64, is_32bit ? 4 : 8);
            WG_LOGW(TAG, "GFX V44 DXGI FACTORY: %s reached before a graphics bridge exists", fn);
            ret_val = 0x887A0004u; /* DXGI_ERROR_UNSUPPORTED */
'''
    if old_factory not in s:
        raise SystemExit('ERROR: V45 V44 factory block anchor changed')
    new_factory = r'''        } else if (strcmp(fn, "CreateDXGIFactory") == 0 || strcmp(fn, "CreateDXGIFactory2") == 0) {
            uint64_t outp = strcmp(fn, "CreateDXGIFactory2") == 0 ? args[2] : args[1];
            if (!outp) {
                ret_val = 0x80070057u; /* E_INVALIDARG */
            } else {
                if (!s_v45_dxgi_factory) {
                    uint64_t q  = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGI_QueryInterface");
                    uint64_t fa = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_AddRef");
                    uint64_t fr = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_Release");
                    uint64_t aa = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_AddRef");
                    uint64_t ar = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_Release");
                    uint64_t ea = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_EnumAdapters");
                    uint64_t ea1= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_EnumAdapters1");
                    uint64_t mwa= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_MakeWindowAssociation");
                    uint64_t gwa= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_GetWindowAssociation");
                    uint64_t cur= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_IsCurrent");
                    uint64_t eo = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_EnumOutputs");
                    uint64_t gd = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetDesc");
                    uint64_t gd1= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetDesc1");
                    uint64_t gd2= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetDesc2");
                    uint64_t ni = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGI_NotImpl");
                    uint64_t fvt = wg_guest_alloc(engine, 64 * 8);
                    uint64_t avt = wg_guest_alloc(engine, 64 * 8);
                    s_v45_dxgi_factory = wg_guest_alloc(engine, 16);
                    s_v45_dxgi_adapter = wg_guest_alloc(engine, 16);
                    uint64_t fv[64], av[64];
                    for (int i=0;i<64;i++) { fv[i]=ni; av[i]=ni; }
                    fv[0]=q; fv[1]=fa; fv[2]=fr;
                    fv[7]=ea; fv[8]=mwa; fv[9]=gwa; fv[12]=ea1; fv[13]=cur;
                    av[0]=q; av[1]=aa; av[2]=ar; av[7]=eo; av[8]=gd; av[10]=gd1; av[11]=gd2;
                    wg_blink_write_mem(engine->blink, fvt, fv, sizeof(fv));
                    wg_blink_write_mem(engine->blink, avt, av, sizeof(av));
                    wg_blink_write_mem(engine->blink, s_v45_dxgi_factory, &fvt, 8);
                    wg_blink_write_mem(engine->blink, s_v45_dxgi_adapter, &avt, 8);
                    WG_LOGI(TAG, "GFX V45 DXGI BOOTSTRAP: factory=0x%llX adapter=0x%llX fvt=0x%llX avt=0x%llX",
                            (unsigned long long)s_v45_dxgi_factory,
                            (unsigned long long)s_v45_dxgi_adapter,
                            (unsigned long long)fvt, (unsigned long long)avt);
                }
                wg_blink_write_mem(engine->blink, outp, &s_v45_dxgi_factory, 8);
                s_v45_factory_refs++;
                WG_LOGI(TAG, "GFX V45 DXGI FACTORY: %s -> S_OK factory=0x%llX",
                        fn, (unsigned long long)s_v45_dxgi_factory);
                ret_val = 0; /* S_OK */
            }
'''
    s = s.replace(old_factory, new_factory, 1)
    p.write_text(s, encoding='utf-8')
    print('V45: DXGI factory + adapter COM bootstrap installed')
else:
    print('V45: DXGI bootstrap already present')

f = p.read_text(encoding='utf-8')
for token in (MARKER, 'GFX V45 DXGI BOOTSTRAP:', 'GFX V45 DXGI FACTORY:', 'GFX V45 DXGI ADAPTER:', 'MxxDXGIFactory_EnumAdapters1', 'MxxHub Metal Adapter'):
    if token not in f: raise SystemExit('ERROR: V45 verification failed: ' + token)
print('MXXHUB_WINDOWS_V45_DXGI_FACTORY_ADAPTER_BOOTSTRAP_OK')
