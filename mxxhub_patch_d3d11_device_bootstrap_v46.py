#!/usr/bin/env python3
from pathlib import Path
import re, sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_d3d11_device_bootstrap_v46.py <WineGlass-root>')
wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file():
    raise SystemExit(f'ERROR: missing {p}')
s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V46_D3D11_DEVICE_CONTEXT_BOOTSTRAP'

if MARKER not in s:
    # V45 is the stable predecessor and gives us a deterministic nearby anchor.
    global_anchor = '/* MXXHUB_WINDOWS_V45_DXGI_FACTORY_ADAPTER_BOOTSTRAP\n'
    if global_anchor not in s:
        raise SystemExit('ERROR: V46 global anchor changed')
    globals_block = r'''/* MXXHUB_WINDOWS_V46_D3D11_DEVICE_CONTEXT_BOOTSTRAP
 * First real D3D11 guest COM layer for Unity bootstrap.
 *
 * This is still a bootstrap, not a complete renderer: D3D11CreateDevice now
 * returns internally coherent ID3D11Device + ID3D11DeviceContext objects with
 * correct Win64 outputs and capability queries. Resource/shader/swap-chain
 * creation remains an explicit next boundary until Metal backing is added.
 */
static uint64_t s_v46_d3d_device = 0;
static uint64_t s_v46_d3d_context = 0;
static uint64_t s_v46_dxgi_device = 0;
static uint32_t s_v46_d3d_device_refs = 1;
static uint32_t s_v46_d3d_context_refs = 1;
static uint32_t s_v46_dxgi_device_refs = 1;
static uint32_t s_v46_dxgi_max_latency = 3;
static uint32_t s_v46_d3d_creation_flags = 0;
static uint32_t s_v46_feature_level = 0xB000u; /* D3D_FEATURE_LEVEL_11_0 */

'''
    s = s.replace(global_anchor, globals_block + global_anchor, 1)

    dispatch_anchor = '        /* V45 DXGI COM thunk handlers.'
    if dispatch_anchor not in s:
        raise SystemExit('ERROR: V46 dispatch anchor changed')
    dispatch = r'''        /* V46 D3D11 device/context COM thunk handlers. */
        if (strcmp(fn, "MxxD3D11Device_QueryInterface") == 0) {
            uint64_t out = args[2];
            if (!out) {
                ret_val = 0x80070057u;
            } else {
                uint8_t g[16] = {0};
                bool d3d_base = false, dxgi_base = false;
                if (args[1] && wg_blink_read_mem(engine->blink, args[1], g, sizeof(g))) {
                    static const uint8_t iid_unknown[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                    static const uint8_t iid_device[16] = {0xDB,0x6D,0x6F,0xDB,0x77,0xAC,0x88,0x4E,0x82,0x53,0x81,0x9D,0xF9,0xBB,0xF1,0x40};
                    static const uint8_t iid_dxgi_device[16] = {0xFA,0x77,0xEC,0x54,0x77,0x13,0xE6,0x44,0x8C,0x32,0x88,0xFD,0x5F,0x44,0xC8,0x4C};
                    static const uint8_t iid_dxgi_device1[16] = {0x0F,0x97,0xDB,0x77,0x76,0x62,0xBA,0x48,0xBA,0x28,0x07,0x01,0x43,0xB4,0x39,0x2C};
                    d3d_base = memcmp(g, iid_unknown, 16) == 0 || memcmp(g, iid_device, 16) == 0;
                    dxgi_base = memcmp(g, iid_dxgi_device, 16) == 0 || memcmp(g, iid_dxgi_device1, 16) == 0;
                }
                if (d3d_base) {
                    wg_blink_write_mem(engine->blink, out, &s_v46_d3d_device, 8);
                    s_v46_d3d_device_refs++;
                    ret_val = 0;
                } else if (dxgi_base && s_v46_dxgi_device) {
                    wg_blink_write_mem(engine->blink, out, &s_v46_dxgi_device, 8);
                    s_v46_dxgi_device_refs++;
                    WG_LOGI(TAG, "GFX V46 DEVICE QI: ID3D11Device -> IDXGIDevice 0x%llX", (unsigned long long)s_v46_dxgi_device);
                    ret_val = 0;
                } else {
                    uint64_t z = 0; wg_blink_write_mem(engine->blink, out, &z, 8);
                    WG_LOGI(TAG, "GFX V46 DEVICE QI: unsupported IID -> E_NOINTERFACE");
                    ret_val = 0x80004002u;
                }
            }
        } else if (strcmp(fn, "MxxD3D11Device_AddRef") == 0) {
            ret_val = ++s_v46_d3d_device_refs;
        } else if (strcmp(fn, "MxxD3D11Device_Release") == 0) {
            if (s_v46_d3d_device_refs > 1) s_v46_d3d_device_refs--;
            ret_val = s_v46_d3d_device_refs;
        } else if (strcmp(fn, "MxxD3D11Device_CheckFormatSupport") == 0) {
            if (args[2]) {
                uint32_t support = 0x07FFFFFFu;
                wg_blink_write_mem(engine->blink, args[2], &support, 4);
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_CheckMSAA") == 0) {
            if (args[3]) {
                uint32_t quality = ((uint32_t)args[2] <= 4u && args[2] != 0) ? 1u : 0u;
                wg_blink_write_mem(engine->blink, args[3], &quality, 4);
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_CheckFeatureSupport") == 0) {
            if (args[2] && args[3]) {
                uint64_t n = args[3] > 256 ? 256 : args[3];
                uint8_t zero[256]; memset(zero, 0, sizeof(zero));
                wg_blink_write_mem(engine->blink, args[2], zero, (size_t)n);
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_GetFeatureLevel") == 0) {
            ret_val = s_v46_feature_level;
        } else if (strcmp(fn, "MxxD3D11Device_GetCreationFlags") == 0) {
            ret_val = s_v46_d3d_creation_flags;
        } else if (strcmp(fn, "MxxD3D11Device_GetRemovedReason") == 0) {
            ret_val = 0; /* S_OK */
        } else if (strcmp(fn, "MxxD3D11Device_GetImmediateContext") == 0) {
            if (args[1]) {
                wg_blink_write_mem(engine->blink, args[1], &s_v46_d3d_context, 8);
                s_v46_d3d_context_refs++;
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_SetExceptionMode") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_GetExceptionMode") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Device_CreateBuffer") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateTexture2D") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateShaderResourceView") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateRenderTargetView") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateDepthStencilView") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateInputLayout") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateVertexShader") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreatePixelShader") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateBlendState") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateDepthStencilState") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateRasterizerState") == 0 ||
                   strcmp(fn, "MxxD3D11Device_CreateSamplerState") == 0) {
            static uint32_t resource_boundary_logs = 0;
            if (resource_boundary_logs++ < 24)
                WG_LOGW(TAG, "GFX V46 METAL RESOURCE BOUNDARY: %s requires a real Metal-backed D3D11 resource", fn);
            ret_val = 0x80004001u; /* E_NOTIMPL, never false S_OK */
        } else if (strcmp(fn, "MxxD3D11Device_NotImpl") == 0) {
            static uint32_t ni = 0;
            if (ni++ < 16) WG_LOGW(TAG, "GFX V46 D3D11 DEVICE METHOD: unsupported slot -> E_NOTIMPL");
            ret_val = 0x80004001u;
        } else if (strcmp(fn, "MxxDXGIDevice_QueryInterface") == 0) {
            uint64_t out = args[2];
            if (!out) ret_val = 0x80070057u;
            else {
                uint8_t g[16] = {0}; bool known = false;
                if (args[1] && wg_blink_read_mem(engine->blink, args[1], g, sizeof(g))) {
                    static const uint8_t iid_unknown[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                    static const uint8_t iid_dxgi_device[16] = {0xFA,0x77,0xEC,0x54,0x77,0x13,0xE6,0x44,0x8C,0x32,0x88,0xFD,0x5F,0x44,0xC8,0x4C};
                    static const uint8_t iid_dxgi_device1[16] = {0x0F,0x97,0xDB,0x77,0x76,0x62,0xBA,0x48,0xBA,0x28,0x07,0x01,0x43,0xB4,0x39,0x2C};
                    known = memcmp(g, iid_unknown, 16) == 0 || memcmp(g, iid_dxgi_device, 16) == 0 || memcmp(g, iid_dxgi_device1, 16) == 0;
                }
                if (known) { wg_blink_write_mem(engine->blink, out, &s_v46_dxgi_device, 8); s_v46_dxgi_device_refs++; ret_val=0; }
                else { uint64_t z=0; wg_blink_write_mem(engine->blink,out,&z,8); ret_val=0x80004002u; }
            }
        } else if (strcmp(fn, "MxxDXGIDevice_AddRef") == 0) {
            ret_val = ++s_v46_dxgi_device_refs;
        } else if (strcmp(fn, "MxxDXGIDevice_Release") == 0) {
            if (s_v46_dxgi_device_refs > 1) s_v46_dxgi_device_refs--;
            ret_val = s_v46_dxgi_device_refs;
        } else if (strcmp(fn, "MxxDXGIDevice_SetPrivateData") == 0 || strcmp(fn, "MxxDXGIDevice_SetPrivateDataInterface") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxDXGIDevice_GetPrivateData") == 0) {
            ret_val = 0x887A0002u;
        } else if (strcmp(fn, "MxxDXGIDevice_GetParent") == 0 || strcmp(fn, "MxxDXGIDevice_GetAdapter") == 0) {
            uint64_t out = strcmp(fn, "MxxDXGIDevice_GetParent") == 0 ? args[2] : args[1];
            if (out && s_v45_dxgi_adapter) { wg_blink_write_mem(engine->blink, out, &s_v45_dxgi_adapter, 8); s_v45_adapter_refs++; ret_val=0; }
            else ret_val=0x80070057u;
        } else if (strcmp(fn, "MxxDXGIDevice_CreateSurface") == 0) {
            WG_LOGW(TAG, "GFX V46 METAL RESOURCE BOUNDARY: IDXGIDevice::CreateSurface requires Metal texture backing");
            ret_val = 0x80004001u;
        } else if (strcmp(fn, "MxxDXGIDevice_QueryResidency") == 0) {
            if (args[2] && args[3] && args[3] < 4096) {
                uint32_t resident = 1;
                for (uint64_t i=0;i<args[3];i++) wg_blink_write_mem(engine->blink, args[2]+i*4, &resident, 4);
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxDXGIDevice_SetGPUThreadPriority") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxDXGIDevice_GetGPUThreadPriority") == 0) {
            if (args[1]) { int32_t z=0; wg_blink_write_mem(engine->blink,args[1],&z,4); } ret_val=0;
        } else if (strcmp(fn, "MxxDXGIDevice_SetMaxLatency") == 0) {
            s_v46_dxgi_max_latency = (uint32_t)args[1]; ret_val=0;
        } else if (strcmp(fn, "MxxDXGIDevice_GetMaxLatency") == 0) {
            if (args[1]) wg_blink_write_mem(engine->blink,args[1],&s_v46_dxgi_max_latency,4);
            ret_val=0;
        } else if (strcmp(fn, "MxxDXGIDevice_NotImpl") == 0) {
            ret_val = 0x80004001u;
        } else if (strcmp(fn, "MxxD3D11Context_QueryInterface") == 0) {
            uint64_t out = args[2];
            if (!out) {
                ret_val = 0x80070057u;
            } else {
                uint8_t g[16] = {0};
                bool known = false;
                if (args[1] && wg_blink_read_mem(engine->blink, args[1], g, sizeof(g))) {
                    static const uint8_t iid_unknown[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                    static const uint8_t iid_context[16] = {0x6C,0xA9,0xBF,0xC0,0x89,0xE0,0xFB,0x44,0x8E,0xAF,0x26,0xF8,0x79,0x61,0x90,0xDA};
                    known = memcmp(g, iid_unknown, 16) == 0 || memcmp(g, iid_context, 16) == 0;
                }
                if (known) {
                    wg_blink_write_mem(engine->blink, out, &s_v46_d3d_context, 8);
                    s_v46_d3d_context_refs++;
                    ret_val = 0;
                } else {
                    uint64_t z=0; wg_blink_write_mem(engine->blink, out, &z, 8);
                    ret_val = 0x80004002u;
                }
            }
        } else if (strcmp(fn, "MxxD3D11Context_AddRef") == 0) {
            ret_val = ++s_v46_d3d_context_refs;
        } else if (strcmp(fn, "MxxD3D11Context_Release") == 0) {
            if (s_v46_d3d_context_refs > 1) s_v46_d3d_context_refs--;
            ret_val = s_v46_d3d_context_refs;
        } else if (strcmp(fn, "MxxD3D11Context_GetDevice") == 0) {
            if (args[1]) {
                wg_blink_write_mem(engine->blink, args[1], &s_v46_d3d_device, 8);
                s_v46_d3d_device_refs++;
            }
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Context_ClearState") == 0 ||
                   strcmp(fn, "MxxD3D11Context_Flush") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Context_GetType") == 0) {
            ret_val = 0; /* D3D11_DEVICE_CONTEXT_IMMEDIATE */
        } else if (strcmp(fn, "MxxD3D11Context_GetFlags") == 0) {
            ret_val = 0;
        } else if (strcmp(fn, "MxxD3D11Context_NotImpl") == 0) {
            static uint32_t cni = 0;
            if (cni++ < 24) WG_LOGW(TAG, "GFX V46 D3D11 CONTEXT METHOD: first unsupported render-state/draw slot reached");
            ret_val = 0x80004001u;
        } else if (strcmp(fn, "MxxDXGIFactory_CreateSwapChain") == 0) {
            if (args[3]) { uint64_t z=0; wg_blink_write_mem(engine->blink, args[3], &z, 8); }
            WG_LOGW(TAG, "GFX V46 METAL SWAPCHAIN BOUNDARY: IDXGIFactory::CreateSwapChain reached; CAMetalLayer backing is next");
            ret_val = 0x80004001u;
        } else if (strcmp(fn, "MxxDXGIAdapter_GetParent") == 0) {
            if (args[2] && s_v45_dxgi_factory) {
                wg_blink_write_mem(engine->blink, args[2], &s_v45_dxgi_factory, 8);
                s_v45_factory_refs++;
                ret_val = 0;
            } else ret_val = 0x80070057u;
        } else if (strcmp(fn, "MxxDXGIAdapter_CheckInterfaceSupport") == 0) {
            if (args[2]) { uint64_t ver = 0x000B000000000000ULL; wg_blink_write_mem(engine->blink, args[2], &ver, 8); }
            ret_val = 0;
        } else '''
    s = s.replace(dispatch_anchor, dispatch + dispatch_anchor, 1)

    # Replace V44's intentional failure with a coherent D3D11 base device/context.
    pat = re.compile(
        r'        if \(strcmp\(fn, "D3D11CreateDevice"\) == 0\) \{.*?'
        r'        \} else if \(strcmp\(fn, "D3D11On12CreateDevice"\) == 0\) \{',
        re.S)
    m = pat.search(s)
    if not m:
        raise SystemExit('ERROR: V46 D3D11CreateDevice block anchor changed')
    new_create = r'''        if (strcmp(fn, "D3D11CreateDevice") == 0) {
            static uint32_t s_v46_calls = 0;
            s_v46_calls++;
            /* Correct D3D11CreateDevice ABI indexes:
             * 0 adapter, 1 driver type, 2 software, 3 flags,
             * 4 feature-level array, 5 count, 6 SDK version,
             * 7 ppDevice, 8 pFeatureLevel, 9 ppImmediateContext. */
            s_v46_d3d_creation_flags = (uint32_t)args[3];
            uint32_t selected = 0xB000u;
            if (args[4] && args[5] && args[5] < 64) {
                selected = 0;
                for (uint64_t fi = 0; fi < args[5]; fi++) {
                    uint32_t fl = 0;
                    wg_blink_read_mem(engine->blink, args[4] + fi * 4, &fl, 4);
                    if (fl <= 0xB000u && fl >= 0x9100u) { selected = fl; break; }
                }
                if (!selected) selected = 0xA000u;
            }
            s_v46_feature_level = selected;

            if (!s_v46_d3d_device) {
                uint64_t dq  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_QueryInterface");
                uint64_t da  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_AddRef");
                uint64_t dr  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_Release");
                uint64_t dfs = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CheckFormatSupport");
                uint64_t dms = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CheckMSAA");
                uint64_t dft = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CheckFeatureSupport");
                uint64_t dfl = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetFeatureLevel");
                uint64_t dcf = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetCreationFlags");
                uint64_t drr = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetRemovedReason");
                uint64_t dic = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetImmediateContext");
                uint64_t dse = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_SetExceptionMode");
                uint64_t dge = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetExceptionMode");
                uint64_t dni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_NotImpl");
                uint64_t dbuf= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateBuffer");
                uint64_t dt2 = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateTexture2D");
                uint64_t dsrv= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateShaderResourceView");
                uint64_t drtv= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateRenderTargetView");
                uint64_t ddsv= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateDepthStencilView");
                uint64_t dil = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateInputLayout");
                uint64_t dvs = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateVertexShader");
                uint64_t dps = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreatePixelShader");
                uint64_t dbs = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateBlendState");
                uint64_t dds = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateDepthStencilState");
                uint64_t drs = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateRasterizerState");
                uint64_t dss = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_CreateSamplerState");
                uint64_t cq  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_QueryInterface");
                uint64_t ca  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_AddRef");
                uint64_t cr  = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_Release");
                uint64_t cgd = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_GetDevice");
                uint64_t ccs = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_ClearState");
                uint64_t cfl = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_Flush");
                uint64_t cty = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_GetType");
                uint64_t cfg = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_GetFlags");
                uint64_t cni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_NotImpl");
                uint64_t xq  = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_QueryInterface");
                uint64_t xa  = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_AddRef");
                uint64_t xr  = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_Release");
                uint64_t xsp = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_SetPrivateData");
                uint64_t xspi= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_SetPrivateDataInterface");
                uint64_t xgp = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_GetPrivateData");
                uint64_t xpar= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_GetParent");
                uint64_t xad = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_GetAdapter");
                uint64_t xcs = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_CreateSurface");
                uint64_t xqr = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_QueryResidency");
                uint64_t xst = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_SetGPUThreadPriority");
                uint64_t xgt = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_GetGPUThreadPriority");
                uint64_t xml = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_SetMaxLatency");
                uint64_t xgl = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_GetMaxLatency");
                uint64_t xni = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIDevice_NotImpl");
                uint64_t dvt = wg_guest_alloc(engine, 128 * 8);
                uint64_t cvt = wg_guest_alloc(engine, 128 * 8);
                uint64_t xvt = wg_guest_alloc(engine, 32 * 8);
                s_v46_d3d_device = wg_guest_alloc(engine, 16);
                s_v46_d3d_context = wg_guest_alloc(engine, 16);
                s_v46_dxgi_device = wg_guest_alloc(engine, 16);
                uint64_t dv[128], cv[128], xv[32];
                for (int i=0;i<128;i++) { dv[i]=dni; cv[i]=cni; }
                for (int i=0;i<32;i++) xv[i]=xni;
                dv[0]=dq; dv[1]=da; dv[2]=dr;
                dv[3]=dbuf; dv[5]=dt2; dv[7]=dsrv; dv[9]=drtv; dv[10]=ddsv;
                dv[11]=dil; dv[12]=dvs; dv[15]=dps; dv[20]=dbs; dv[21]=dds;
                dv[22]=drs; dv[23]=dss;
                dv[29]=dfs; dv[30]=dms; dv[33]=dft; dv[37]=dfl; dv[38]=dcf;
                dv[39]=drr; dv[40]=dic; dv[41]=dse; dv[42]=dge;
                cv[0]=cq; cv[1]=ca; cv[2]=cr; cv[3]=cgd;
                /* ID3D11DeviceContext base vtable tail: ClearState, Flush,
                 * GetType, GetContextFlags, FinishCommandList. */
                cv[110]=ccs; cv[111]=cfl; cv[112]=cty; cv[113]=cfg;
                xv[0]=xq; xv[1]=xa; xv[2]=xr; xv[3]=xsp; xv[4]=xspi; xv[5]=xgp; xv[6]=xpar;
                xv[7]=xad; xv[8]=xcs; xv[9]=xqr; xv[10]=xst; xv[11]=xgt; xv[12]=xml; xv[13]=xgl;
                wg_blink_write_mem(engine->blink, dvt, dv, sizeof(dv));
                wg_blink_write_mem(engine->blink, cvt, cv, sizeof(cv));
                wg_blink_write_mem(engine->blink, xvt, xv, sizeof(xv));
                wg_blink_write_mem(engine->blink, s_v46_d3d_device, &dvt, 8);
                wg_blink_write_mem(engine->blink, s_v46_d3d_context, &cvt, 8);
                wg_blink_write_mem(engine->blink, s_v46_dxgi_device, &xvt, 8);
                WG_LOGI(TAG, "GFX V46 D3D11 BOOTSTRAP: device=0x%llX context=0x%llX dxgiDevice=0x%llX dvt=0x%llX cvt=0x%llX",
                        (unsigned long long)s_v46_d3d_device,
                        (unsigned long long)s_v46_d3d_context,
                        (unsigned long long)s_v46_dxgi_device,
                        (unsigned long long)dvt, (unsigned long long)cvt);
            }
            if (args[7]) wg_blink_write_mem(engine->blink, args[7], &s_v46_d3d_device, 8);
            if (args[8]) wg_blink_write_mem(engine->blink, args[8], &s_v46_feature_level, 4);
            if (args[9]) wg_blink_write_mem(engine->blink, args[9], &s_v46_d3d_context, 8);
            s_v46_d3d_device_refs++;
            s_v46_d3d_context_refs++;
            WG_LOGI(TAG,
                    "GFX V46 D3D11 CREATE OK: call=%u adapter=0x%llX driver=%llu flags=0x%X feature=0x%X sdk=%llu ppDevice=0x%llX ppContext=0x%llX",
                    s_v46_calls, (unsigned long long)args[0],
                    (unsigned long long)args[1], s_v46_d3d_creation_flags,
                    s_v46_feature_level, (unsigned long long)args[6],
                    (unsigned long long)args[7], (unsigned long long)args[9]);
            ret_val = 0; /* S_OK */
        } else if (strcmp(fn, "D3D11On12CreateDevice") == 0) {'''
    s = s[:m.start()] + new_create + s[m.end():]

    # Upgrade V45's DXGI vtables with a few base methods Unity will need after
    # the D3D device exists, plus a named swap-chain boundary.
    resolver_anchor = '                    uint64_t gd2= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetDesc2");\n                    uint64_t ni = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGI_NotImpl");'
    resolver_new = '                    uint64_t gd2= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetDesc2");\n                    uint64_t gp = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_GetParent");\n                    uint64_t cis= wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIAdapter_CheckInterfaceSupport");\n                    uint64_t cs = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGIFactory_CreateSwapChain");\n                    uint64_t ni = wg_dll_mapper_resolve(engine->dll_mapper, "dxgi.dll", "MxxDXGI_NotImpl");'
    if resolver_anchor not in s:
        raise SystemExit('ERROR: V46 V45 resolver anchor changed')
    s = s.replace(resolver_anchor, resolver_new, 1)
    assign_anchor = '                    fv[7]=ea; fv[8]=mwa; fv[9]=gwa; fv[12]=ea1; fv[13]=cur;\n                    av[0]=q; av[1]=aa; av[2]=ar; av[7]=eo; av[8]=gd; av[10]=gd1; av[11]=gd2;'
    assign_new = '                    fv[7]=ea; fv[8]=mwa; fv[9]=gwa; fv[10]=cs; fv[12]=ea1; fv[13]=cur;\n                    av[0]=q; av[1]=aa; av[2]=ar; av[6]=gp; av[7]=eo; av[8]=gd; av[9]=cis; av[10]=gd1; av[11]=gd2;'
    if assign_anchor not in s:
        raise SystemExit('ERROR: V46 V45 vtable assignment anchor changed')
    s = s.replace(assign_anchor, assign_new, 1)

    p.write_text(s, encoding='utf-8')
    print('V46: corrected D3D11CreateDevice Win64 argument indexes (ppDevice=7, pFeatureLevel=8, ppContext=9)')
    print('V46: added coherent ID3D11Device + immediate ID3D11DeviceContext guest COM objects')
    print('V46: added base capability queries and explicit Metal resource/swap-chain boundaries')
else:
    print('V46: D3D11 device/context bootstrap already present')

f = p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'GFX V46 D3D11 BOOTSTRAP:',
    'GFX V46 D3D11 CREATE OK:',
    'GFX V46 METAL RESOURCE BOUNDARY:',
    'GFX V46 METAL SWAPCHAIN BOUNDARY:',
    '7 ppDevice, 8 pFeatureLevel, 9 ppImmediateContext',
    'dv[40]=dic',
    'xv[7]=xad',
    'ID3D11Device -> IDXGIDevice',
    'cv[110]=ccs',
    'fv[10]=cs',
    'av[6]=gp',
):
    if token not in f:
        raise SystemExit('ERROR: V46 verification failed: ' + token)
if 'GFX V44 D3D11 BACKEND REQUIRED:' in f:
    raise SystemExit('ERROR: V46 verification failed: V44 forced D3D11 failure is still present')
print('MXXHUB_WINDOWS_V46_D3D11_DEVICE_CONTEXT_BOOTSTRAP_OK')
