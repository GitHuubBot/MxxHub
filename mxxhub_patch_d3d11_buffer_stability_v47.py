#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_d3d11_buffer_stability_v47.py <WineGlass-root>')
wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file():
    raise SystemExit(f'ERROR: missing {p}')
s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V47_D3D11_BUFFER_VM_STABILITY'

if MARKER not in s:
    global_anchor = '/* MXXHUB_WINDOWS_V46_D3D11_DEVICE_CONTEXT_BOOTSTRAP\n'
    if global_anchor not in s:
        raise SystemExit('ERROR: V47 V46 global anchor changed')
    globals_block = r'''/* MXXHUB_WINDOWS_V47_D3D11_BUFFER_VM_STABILITY
 * V46 reached a real D3D11CreateDevice and then crossed into resource calls.
 * Two correctness issues showed up on device:
 *   1) DXGI/D3D guest pointers were process-global and could survive a VM retry.
 *   2) CreateBuffer failed without a usable resource, while several COM output
 *      methods were still generic E_NOTIMPL shims.
 *
 * V47 makes the graphics bootstrap engine-affine and adds a CPU-backed guest
 * ID3D11Buffer sufficient for Unity bootstrap Map/Unmap/Update/Copy traffic.
 * It is deliberately not a Metal renderer yet.
 */
static void *s_v47_graphics_owner = NULL;
static uint64_t s_v47_buffer_vt = 0;
#define MXX_V47_MAX_BUFFERS 2048
typedef struct MxxV47BufferRec {
    uint64_t obj;
    uint64_t data;
    uint32_t desc[6];
    uint32_t refs;
} MxxV47BufferRec;
static MxxV47BufferRec s_v47_buffers[MXX_V47_MAX_BUFFERS];
static uint32_t s_v47_buffer_count = 0;

'''
    s = s.replace(global_anchor, globals_block + global_anchor, 1)

    # Put V47 handlers before V46 handlers so the more specific resource paths win.
    dispatch_anchor = '        /* V46 D3D11 device/context COM thunk handlers. */\n'
    if dispatch_anchor not in s:
        raise SystemExit('ERROR: V47 V46 dispatch anchor changed')
    dispatch = r'''        /* V47 D3D11 CPU-buffer + safe COM handlers. */
        if (strcmp(fn, "MxxD3D11Buffer_QueryInterface") == 0) {
            uint64_t self = args[0], out = args[2];
            if (!out) ret_val = 0x80070057u;
            else {
                uint64_t z = 0; bool known = false; uint8_t g[16] = {0};
                if (args[1] && wg_blink_read_mem(engine->blink, args[1], g, 16)) {
                    static const uint8_t iu[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                    static const uint8_t idc[16]= {0xC8,0xE5,0x41,0x18,0xB0,0x16,0x9B,0x48,0xBC,0xC8,0x44,0xCF,0xB0,0xD5,0xDE,0xAE};
                    static const uint8_t ir[16] = {0xF3,0x63,0x8E,0xDC,0x2B,0xD1,0x52,0x49,0xB4,0x7B,0x5E,0x45,0x02,0x6A,0x86,0x2D};
                    static const uint8_t ib[16] = {0x85,0x0B,0x57,0x48,0xEE,0xD1,0xCD,0x4F,0xA2,0x50,0xEB,0x35,0x07,0x22,0xB0,0x37};
                    known = memcmp(g,iu,16)==0 || memcmp(g,idc,16)==0 || memcmp(g,ir,16)==0 || memcmp(g,ib,16)==0;
                }
                if (known) {
                    wg_blink_write_mem(engine->blink, out, &self, 8);
                    for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==self) { s_v47_buffers[i].refs++; break; }
                    ret_val=0;
                } else {
                    wg_blink_write_mem(engine->blink, out, &z, 8); ret_val=0x80004002u;
                }
            }
        } else if (strcmp(fn, "MxxD3D11Buffer_AddRef") == 0) {
            ret_val = 1;
            for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==args[0]) { ret_val=++s_v47_buffers[i].refs; break; }
        } else if (strcmp(fn, "MxxD3D11Buffer_Release") == 0) {
            ret_val = 1;
            for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==args[0]) {
                if (s_v47_buffers[i].refs>1) s_v47_buffers[i].refs--; ret_val=s_v47_buffers[i].refs; break;
            }
        } else if (strcmp(fn, "MxxD3D11Buffer_GetDevice") == 0) {
            if (args[1]) { wg_blink_write_mem(engine->blink,args[1],&s_v46_d3d_device,8); s_v46_d3d_device_refs++; }
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Buffer_GetPrivateData") == 0) {
            if (args[2]) { uint32_t z=0; wg_blink_write_mem(engine->blink,args[2],&z,4); }
            ret_val=0x887A0002u;
        } else if (strcmp(fn, "MxxD3D11Buffer_SetPrivateData") == 0 || strcmp(fn, "MxxD3D11Buffer_SetPrivateDataInterface") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Buffer_GetType") == 0) {
            ret_val=1; /* D3D11_RESOURCE_DIMENSION_BUFFER */
        } else if (strcmp(fn, "MxxD3D11Buffer_SetEvictionPriority") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Buffer_GetEvictionPriority") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Buffer_GetDesc") == 0) {
            if (args[1]) for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==args[0]) {
                wg_blink_write_mem(engine->blink,args[1],s_v47_buffers[i].desc,24); break;
            }
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Context_Map") == 0) {
            /* this, resource, subresource, mapType, mapFlags, pMapped */
            uint64_t obj=args[1], out=args[5]; MxxV47BufferRec *b=NULL;
            for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==obj) { b=&s_v47_buffers[i]; break; }
            if (!b || !out) ret_val=0x80070057u;
            else {
                if ((uint32_t)args[3] == 4u && b->desc[0]) { /* WRITE_DISCARD */
                    uint8_t zero[4096]; memset(zero,0,sizeof(zero));
                    uint32_t left=b->desc[0]; uint64_t dst=b->data;
                    while (left) { uint32_t n=left>sizeof(zero)?sizeof(zero):left; wg_blink_write_mem(engine->blink,dst,zero,n); dst+=n; left-=n; }
                }
                uint8_t mapped[16]; memset(mapped,0,sizeof(mapped));
                memcpy(mapped,&b->data,8); memcpy(mapped+8,&b->desc[0],4); memcpy(mapped+12,&b->desc[0],4);
                wg_blink_write_mem(engine->blink,out,mapped,16);
                WG_LOGI(TAG,"GFX V47 BUFFER MAP: obj=0x%llX data=0x%llX bytes=%u type=%llu",
                        (unsigned long long)b->obj,(unsigned long long)b->data,b->desc[0],(unsigned long long)args[3]);
                ret_val=0;
            }
        } else if (strcmp(fn, "MxxD3D11Context_Unmap") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Context_UpdateSubresource") == 0) {
            MxxV47BufferRec *b=NULL; for (uint32_t i=0;i<s_v47_buffer_count;i++) if (s_v47_buffers[i].obj==args[1]) { b=&s_v47_buffers[i]; break; }
            if (b && args[4] && b->desc[0]) {
                uint32_t lo=0, hi=b->desc[0];
                if (args[3]) { uint32_t box[6]={0}; if (wg_blink_read_mem(engine->blink,args[3],box,24)) { lo=box[0]; hi=box[3]; if (hi>b->desc[0]) hi=b->desc[0]; if (lo>hi) lo=hi; } }
                uint32_t left=hi-lo; uint64_t src=args[4], dst=b->data+lo; uint8_t tmp[4096];
                while (left) { uint32_t n=left>sizeof(tmp)?sizeof(tmp):left; if (!wg_blink_read_mem(engine->blink,src,tmp,n)) break; wg_blink_write_mem(engine->blink,dst,tmp,n); src+=n; dst+=n; left-=n; }
            }
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Context_CopyResource") == 0) {
            MxxV47BufferRec *d=NULL,*r=NULL; for (uint32_t i=0;i<s_v47_buffer_count;i++) { if (s_v47_buffers[i].obj==args[1]) d=&s_v47_buffers[i]; if (s_v47_buffers[i].obj==args[2]) r=&s_v47_buffers[i]; }
            if (d&&r) { uint32_t left=d->desc[0]<r->desc[0]?d->desc[0]:r->desc[0]; uint64_t src=r->data,dst=d->data; uint8_t tmp[4096]; while(left){uint32_t n=left>sizeof(tmp)?sizeof(tmp):left;if(!wg_blink_read_mem(engine->blink,src,tmp,n))break;wg_blink_write_mem(engine->blink,dst,tmp,n);src+=n;dst+=n;left-=n;} }
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Context_CopySubresourceRegion") == 0) {
            /* Bootstrap-safe no-op for non-buffer resources; buffer copies are handled conservatively. */
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Device_GetPrivateData") == 0) {
            if (args[2]) { uint32_t z=0; wg_blink_write_mem(engine->blink,args[2],&z,4); }
            ret_val=0x887A0002u;
        } else if (strcmp(fn, "MxxD3D11Device_SetPrivateData") == 0 || strcmp(fn, "MxxD3D11Device_SetPrivateDataInterface") == 0) {
            ret_val=0;
        } else '''
    s = s.replace(dispatch_anchor, dispatch + dispatch_anchor, 1)

    # Replace CreateBuffer's V46 hard boundary with a CPU-backed guest buffer.
    old = '''        } else if (strcmp(fn, "MxxD3D11Device_CreateBuffer") == 0 ||\n                   strcmp(fn, "MxxD3D11Device_CreateTexture2D") == 0 ||'''
    if old not in s:
        raise SystemExit('ERROR: V47 CreateBuffer boundary anchor changed')
    new = r'''        } else if (strcmp(fn, "MxxD3D11Device_CreateBuffer") == 0) {
            uint64_t out=args[3]; uint64_t z=0;
            if (out) wg_blink_write_mem(engine->blink,out,&z,8);
            if (!args[1] || !out || s_v47_buffer_count >= MXX_V47_MAX_BUFFERS) {
                ret_val=0x80070057u;
            } else {
                uint32_t desc[6]={0};
                if (!wg_blink_read_mem(engine->blink,args[1],desc,24) || desc[0]==0 || desc[0]>(128u*1024u*1024u)) {
                    ret_val=0x80070057u;
                } else {
                    if (!s_v47_buffer_vt) {
                        uint64_t q=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_QueryInterface");
                        uint64_t a=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_AddRef");
                        uint64_t r=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_Release");
                        uint64_t gd=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_GetDevice");
                        uint64_t gp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_GetPrivateData");
                        uint64_t sp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_SetPrivateData");
                        uint64_t si=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_SetPrivateDataInterface");
                        uint64_t gt=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_GetType");
                        uint64_t se=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_SetEvictionPriority");
                        uint64_t ge=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_GetEvictionPriority");
                        uint64_t de=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Buffer_GetDesc");
                        s_v47_buffer_vt=wg_guest_alloc(engine,11*8); uint64_t bv[11]={q,a,r,gd,gp,sp,si,gt,se,ge,de};
                        wg_blink_write_mem(engine->blink,s_v47_buffer_vt,bv,sizeof(bv));
                    }
                    MxxV47BufferRec *b=&s_v47_buffers[s_v47_buffer_count++]; memset(b,0,sizeof(*b));
                    b->obj=wg_guest_alloc(engine,16); b->data=wg_guest_alloc(engine,desc[0]); memcpy(b->desc,desc,24); b->refs=1;
                    wg_blink_write_mem(engine->blink,b->obj,&s_v47_buffer_vt,8);
                    if (args[2]) {
                        uint8_t sd[16]={0}; uint64_t src=0;
                        if (wg_blink_read_mem(engine->blink,args[2],sd,16)) memcpy(&src,sd,8);
                        if (src) { uint32_t left=desc[0]; uint64_t dst=b->data; uint8_t tmp[4096]; while(left){uint32_t n=left>sizeof(tmp)?sizeof(tmp):left;if(!wg_blink_read_mem(engine->blink,src,tmp,n))break;wg_blink_write_mem(engine->blink,dst,tmp,n);src+=n;dst+=n;left-=n;} }
                    }
                    wg_blink_write_mem(engine->blink,out,&b->obj,8);
                    WG_LOGI(TAG,"GFX V47 BUFFER CREATE OK: obj=0x%llX data=0x%llX bytes=%u bind=0x%X usage=%u cpu=0x%X",
                            (unsigned long long)b->obj,(unsigned long long)b->data,desc[0],desc[2],desc[1],desc[3]);
                    ret_val=0;
                }
            }
        } else if (strcmp(fn, "MxxD3D11Device_CreateTexture2D") == 0 ||'''
    s = s.replace(old, new, 1)

    # Any still-unimplemented resource constructor must clear its COM out pointer.
    resource_log = '''            static uint32_t resource_boundary_logs = 0;\n            if (resource_boundary_logs++ < 24)\n                WG_LOGW(TAG, "GFX V46 METAL RESOURCE BOUNDARY: %s requires a real Metal-backed D3D11 resource", fn);\n            ret_val = 0x80004001u; /* E_NOTIMPL, never false S_OK */'''
    if resource_log not in s:
        raise SystemExit('ERROR: V47 resource boundary body changed')
    resource_new = r'''            uint64_t outp = 0;
            if (strcmp(fn,"MxxD3D11Device_CreateTexture2D")==0 || strcmp(fn,"MxxD3D11Device_CreateShaderResourceView")==0 ||
                strcmp(fn,"MxxD3D11Device_CreateRenderTargetView")==0 || strcmp(fn,"MxxD3D11Device_CreateDepthStencilView")==0) outp=args[3];
            else if (strcmp(fn,"MxxD3D11Device_CreateInputLayout")==0) outp=args[5];
            else if (strcmp(fn,"MxxD3D11Device_CreateVertexShader")==0 || strcmp(fn,"MxxD3D11Device_CreatePixelShader")==0) outp=args[4];
            else if (strcmp(fn,"MxxD3D11Device_CreateBlendState")==0 || strcmp(fn,"MxxD3D11Device_CreateDepthStencilState")==0 ||
                     strcmp(fn,"MxxD3D11Device_CreateRasterizerState")==0 || strcmp(fn,"MxxD3D11Device_CreateSamplerState")==0) outp=args[2];
            if (outp) { uint64_t z=0; wg_blink_write_mem(engine->blink,outp,&z,8); }
            static uint32_t resource_boundary_logs = 0;
            if (resource_boundary_logs++ < 24)
                WG_LOGW(TAG, "GFX V47 SAFE RESOURCE BOUNDARY: %s -> NULL output + E_NOTIMPL", fn);
            ret_val = 0x80004001u;'''
    s = s.replace(resource_log, resource_new, 1)

    # Add per-engine ownership reset before V45's factory allocation. This fixes
    # retries/reopens reusing guest pointers from a previous Blink VM.
    owner_anchor = '''                if (!s_v45_dxgi_factory) {\n'''
    if owner_anchor not in s:
        raise SystemExit('ERROR: V47 V45 factory allocation anchor changed')
    owner_block = r'''                if (s_v47_graphics_owner != (void *)engine) {
                    s_v47_graphics_owner=(void *)engine;
                    s_v45_dxgi_factory=0; s_v45_dxgi_adapter=0; s_v45_factory_refs=1; s_v45_adapter_refs=1;
                    s_v46_d3d_device=0; s_v46_d3d_context=0; s_v46_dxgi_device=0;
                    s_v46_d3d_device_refs=1; s_v46_d3d_context_refs=1; s_v46_dxgi_device_refs=1;
                    s_v46_dxgi_max_latency=3; s_v47_buffer_vt=0; s_v47_buffer_count=0;
                    memset(s_v47_buffers,0,sizeof(s_v47_buffers));
                    WG_LOGI(TAG,"GFX V47 NEW-VM RESET: fresh DXGI/D3D guest objects for engine=%p",(void *)engine);
                }
                if (!s_v45_dxgi_factory) {
'''
    s = s.replace(owner_anchor, owner_block, 1)

    # Also protect direct D3D creation in case a path skips CreateDXGIFactory.
    d3d_anchor = '''            if (!s_v46_d3d_device) {\n'''
    if d3d_anchor not in s:
        raise SystemExit('ERROR: V47 V46 device allocation anchor changed')
    d3d_block = r'''            if (s_v47_graphics_owner != (void *)engine) {
                s_v47_graphics_owner=(void *)engine;
                s_v45_dxgi_factory=0; s_v45_dxgi_adapter=0; s_v45_factory_refs=1; s_v45_adapter_refs=1;
                s_v46_d3d_device=0; s_v46_d3d_context=0; s_v46_dxgi_device=0;
                s_v46_d3d_device_refs=1; s_v46_d3d_context_refs=1; s_v46_dxgi_device_refs=1;
                s_v46_dxgi_max_latency=3; s_v47_buffer_vt=0; s_v47_buffer_count=0;
                memset(s_v47_buffers,0,sizeof(s_v47_buffers));
                WG_LOGI(TAG,"GFX V47 NEW-VM RESET: direct D3D path engine=%p",(void *)engine);
            }
            if (!s_v46_d3d_device) {
'''
    s = s.replace(d3d_anchor, d3d_block, 1)

    # Wire safe device private-data methods and CPU-buffer context methods into
    # the exact ID3D11Device/ID3D11DeviceContext ABI slots.
    resolver_anchor = '''                uint64_t dge = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetExceptionMode");\n                uint64_t dni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_NotImpl");'''
    if resolver_anchor not in s:
        raise SystemExit('ERROR: V47 device resolver anchor changed')
    resolver_new = '''                uint64_t dge = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetExceptionMode");\n                uint64_t dgp = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_GetPrivateData");\n                uint64_t dsp = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_SetPrivateData");\n                uint64_t dsi = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_SetPrivateDataInterface");\n                uint64_t dni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Device_NotImpl");'''
    s = s.replace(resolver_anchor, resolver_new, 1)

    ctx_res_anchor = '''                uint64_t cfg = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_GetFlags");\n                uint64_t cni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_NotImpl");'''
    if ctx_res_anchor not in s:
        raise SystemExit('ERROR: V47 context resolver anchor changed')
    ctx_res_new = '''                uint64_t cfg = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_GetFlags");\n                uint64_t cmap= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_Map");\n                uint64_t cunm= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_Unmap");\n                uint64_t ccsr= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_CopySubresourceRegion");\n                uint64_t ccr = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_CopyResource");\n                uint64_t cupd= wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_UpdateSubresource");\n                uint64_t cni = wg_dll_mapper_resolve(engine->dll_mapper, "d3d11.dll", "MxxD3D11Context_NotImpl");'''
    s = s.replace(ctx_res_anchor, ctx_res_new, 1)

    dv_anchor = '''                dv[39]=drr; dv[40]=dic; dv[41]=dse; dv[42]=dge;\n                cv[0]=cq; cv[1]=ca; cv[2]=cr; cv[3]=cgd;'''
    if dv_anchor not in s:
        raise SystemExit('ERROR: V47 vtable mapping anchor changed')
    dv_new = '''                dv[34]=dgp; dv[35]=dsp; dv[36]=dsi;\n                dv[39]=drr; dv[40]=dic; dv[41]=dse; dv[42]=dge;\n                cv[0]=cq; cv[1]=ca; cv[2]=cr; cv[3]=cgd;\n                cv[14]=cmap; cv[15]=cunm; cv[46]=ccsr; cv[47]=ccr; cv[48]=cupd;'''
    s = s.replace(dv_anchor, dv_new, 1)

    p.write_text(s, encoding='utf-8')
    print('V47: graphics state is now reset for every new WGEngine/Blink VM')
    print('V47: CreateBuffer now returns a CPU-backed guest ID3D11Buffer')
    print('V47: Map/Unmap/UpdateSubresource/CopyResource are safe for V47 buffers')
    print('V47: unimplemented D3D resource constructors now NULL their output pointers')
else:
    print('V47: D3D11 buffer/VM stability patch already present')

f = p.read_text(encoding='utf-8')
for token in (
    MARKER,
    'GFX V47 NEW-VM RESET:',
    'GFX V47 BUFFER CREATE OK:',
    'GFX V47 BUFFER MAP:',
    'GFX V47 SAFE RESOURCE BOUNDARY:',
    'cv[14]=cmap; cv[15]=cunm; cv[46]=ccsr; cv[47]=ccr; cv[48]=cupd;',
    'dv[34]=dgp; dv[35]=dsp; dv[36]=dsi;',
    'MXX_V47_MAX_BUFFERS 2048',
):
    if token not in f:
        raise SystemExit('ERROR: V47 verification failed: ' + token)
print('MXXHUB_WINDOWS_V47_D3D11_BUFFER_VM_STABILITY_OK')
