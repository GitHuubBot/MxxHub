#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_texture_com_v48.py <WineGlass-root>')
wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file():
    raise SystemExit(f'ERROR: missing {p}')
s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V48_TEXTURE_SRV_SAFE_COM'
if MARKER in s:
    print('V48 already applied')
    raise SystemExit(0)

global_anchor = '/* MXXHUB_WINDOWS_V47_D3D11_BUFFER_VM_STABILITY\n'
if global_anchor not in s:
    raise SystemExit('ERROR: V48 V47 global anchor changed')

globals_block = r'''/* MXXHUB_WINDOWS_V48_TEXTURE_SRV_SAFE_COM
 * Device log 2026-09-05 showed two concrete blockers after V47:
 *   - CreateTexture2D/CreateShaderResourceView were still E_NOTIMPL.
 *   - generic ole32!CoCreateInstance returned false S_OK with no interface;
 *     Unity trusted it and jumped through an invalid vtable to RIP=0xF.
 *
 * V48 adds CPU-backed bootstrap ID3D11Texture2D + ID3D11ShaderResourceView
 * objects and makes unsupported CoCreateInstance calls fail safely with a
 * NULL ppv. This still does not claim real GPU rendering; Metal backing comes
 * after Unity survives resource/bootstrap enumeration.
 */
#define MXX_V48_MAX_TEXTURES 1024
#define MXX_V48_MAX_SRVS 2048
typedef struct MxxV48TextureRec {
    uint64_t obj;
    uint64_t data;
    uint32_t desc[11];
    uint32_t refs;
    uint32_t row_pitch;
    uint32_t bytes;
} MxxV48TextureRec;
typedef struct MxxV48SrvRec {
    uint64_t obj;
    uint64_t resource;
    uint32_t desc[6];
    uint32_t refs;
} MxxV48SrvRec;
static uint64_t s_v48_texture_vt = 0;
static uint64_t s_v48_srv_vt = 0;
static MxxV48TextureRec s_v48_textures[MXX_V48_MAX_TEXTURES];
static MxxV48SrvRec s_v48_srvs[MXX_V48_MAX_SRVS];
static uint32_t s_v48_texture_count = 0;
static uint32_t s_v48_srv_count = 0;

'''
s = s.replace(global_anchor, globals_block + global_anchor, 1)

# Insert V48 before V47's D3D-specific handlers so texture/SRV and COM safety
# override the older generic paths.
dispatch_anchor = '        /* V47 D3D11 CPU-buffer + safe COM handlers. */\n'
if dispatch_anchor not in s:
    raise SystemExit('ERROR: V48 V47 dispatch anchor changed')

dispatch = r'''        /* V48 safe COM + CPU texture/SRV bootstrap handlers. */
        if (strcmp(fn, "CoCreateInstance") == 0) {
            /* Win64: rclsid, pUnkOuter, dwClsContext, riid, ppv. */
            uint64_t out=args[4], z=0; uint8_t cls[16]={0}, iid[16]={0};
            if (out) wg_blink_write_mem(engine->blink,out,&z,8);
            if (args[0]) wg_blink_read_mem(engine->blink,args[0],cls,16);
            if (args[3]) wg_blink_read_mem(engine->blink,args[3],iid,16);
            static uint32_t com_logs=0;
            if (com_logs++ < 24) {
                WG_LOGW(TAG,
                    "COM V48 SAFE FAIL: CoCreateInstance clsid=%02X%02X%02X%02X-%02X%02X-%02X%02X-... iid=%02X%02X%02X%02X-%02X%02X-%02X%02X-... -> ppv=NULL REGDB_E_CLASSNOTREG",
                    cls[3],cls[2],cls[1],cls[0],cls[5],cls[4],cls[7],cls[6],
                    iid[3],iid[2],iid[1],iid[0],iid[5],iid[4],iid[7],iid[6]);
            }
            ret_val=0x80040154u; /* REGDB_E_CLASSNOTREG; never false S_OK */
        } else if (strcmp(fn, "MxxD3D11Texture2D_QueryInterface") == 0) {
            uint64_t self=args[0], out=args[2], z=0; bool known=false; uint8_t g[16]={0};
            if (args[1] && wg_blink_read_mem(engine->blink,args[1],g,16)) {
                static const uint8_t iu[16] ={0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                static const uint8_t idc[16]={0xC8,0xE5,0x41,0x18,0xB0,0x16,0x9B,0x48,0xBC,0xC8,0x44,0xCF,0xB0,0xD5,0xDE,0xAE};
                static const uint8_t ir[16] ={0xF3,0x63,0x8E,0xDC,0x2B,0xD1,0x52,0x49,0xB4,0x7B,0x5E,0x45,0x02,0x6A,0x86,0x2D};
                static const uint8_t it2[16]={0xF2,0xAA,0x15,0x6F,0x08,0xD2,0x89,0x4E,0x9A,0xB4,0x48,0x95,0x35,0xD3,0x4F,0x9C};
                known=memcmp(g,iu,16)==0 || memcmp(g,idc,16)==0 || memcmp(g,ir,16)==0 || memcmp(g,it2,16)==0;
            }
            if (!out) ret_val=0x80070057u;
            else if (known) {
                wg_blink_write_mem(engine->blink,out,&self,8);
                for (uint32_t i=0;i<s_v48_texture_count;i++) if (s_v48_textures[i].obj==self) { s_v48_textures[i].refs++; break; }
                ret_val=0;
            } else { wg_blink_write_mem(engine->blink,out,&z,8); ret_val=0x80004002u; }
        } else if (strcmp(fn, "MxxD3D11Texture2D_AddRef") == 0) {
            ret_val=1; for (uint32_t i=0;i<s_v48_texture_count;i++) if (s_v48_textures[i].obj==args[0]) { ret_val=++s_v48_textures[i].refs; break; }
        } else if (strcmp(fn, "MxxD3D11Texture2D_Release") == 0) {
            ret_val=1; for (uint32_t i=0;i<s_v48_texture_count;i++) if (s_v48_textures[i].obj==args[0]) { if (s_v48_textures[i].refs>1) s_v48_textures[i].refs--; ret_val=s_v48_textures[i].refs; break; }
        } else if (strcmp(fn, "MxxD3D11Texture2D_GetDevice") == 0) {
            if (args[1]) { wg_blink_write_mem(engine->blink,args[1],&s_v46_d3d_device,8); s_v46_d3d_device_refs++; } ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Texture2D_GetPrivateData") == 0) {
            if (args[2]) { uint32_t z=0; wg_blink_write_mem(engine->blink,args[2],&z,4); } ret_val=0x887A0002u;
        } else if (strcmp(fn, "MxxD3D11Texture2D_SetPrivateData") == 0 || strcmp(fn, "MxxD3D11Texture2D_SetPrivateDataInterface") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Texture2D_GetType") == 0) {
            ret_val=3; /* D3D11_RESOURCE_DIMENSION_TEXTURE2D */
        } else if (strcmp(fn, "MxxD3D11Texture2D_SetEvictionPriority") == 0 || strcmp(fn, "MxxD3D11Texture2D_GetEvictionPriority") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Texture2D_GetDesc") == 0) {
            if (args[1]) for (uint32_t i=0;i<s_v48_texture_count;i++) if (s_v48_textures[i].obj==args[0]) { wg_blink_write_mem(engine->blink,args[1],s_v48_textures[i].desc,44); break; } ret_val=0;
        } else if (strcmp(fn, "MxxD3D11SRV_QueryInterface") == 0) {
            uint64_t self=args[0], out=args[2], z=0; bool known=false; uint8_t g[16]={0};
            if (args[1] && wg_blink_read_mem(engine->blink,args[1],g,16)) {
                static const uint8_t iu[16] ={0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
                static const uint8_t idc[16]={0xC8,0xE5,0x41,0x18,0xB0,0x16,0x9B,0x48,0xBC,0xC8,0x44,0xCF,0xB0,0xD5,0xDE,0xAE};
                static const uint8_t iv[16] ={0x16,0x12,0x9D,0x83,0x2E,0xBB,0x2B,0x41,0xB7,0xF4,0xA9,0xDB,0xEB,0xE0,0x8E,0xD1};
                static const uint8_t isrv[16]={0xE0,0x6F,0xE0,0xB0,0x92,0x81,0x1A,0x4E,0xB1,0xCA,0x36,0xD7,0x41,0x47,0x10,0xB2};
                known=memcmp(g,iu,16)==0 || memcmp(g,idc,16)==0 || memcmp(g,iv,16)==0 || memcmp(g,isrv,16)==0;
            }
            if (!out) ret_val=0x80070057u;
            else if (known) {
                wg_blink_write_mem(engine->blink,out,&self,8);
                for (uint32_t i=0;i<s_v48_srv_count;i++) if (s_v48_srvs[i].obj==self) { s_v48_srvs[i].refs++; break; }
                ret_val=0;
            } else { wg_blink_write_mem(engine->blink,out,&z,8); ret_val=0x80004002u; }
        } else if (strcmp(fn, "MxxD3D11SRV_AddRef") == 0) {
            ret_val=1; for (uint32_t i=0;i<s_v48_srv_count;i++) if (s_v48_srvs[i].obj==args[0]) { ret_val=++s_v48_srvs[i].refs; break; }
        } else if (strcmp(fn, "MxxD3D11SRV_Release") == 0) {
            ret_val=1; for (uint32_t i=0;i<s_v48_srv_count;i++) if (s_v48_srvs[i].obj==args[0]) { if (s_v48_srvs[i].refs>1) s_v48_srvs[i].refs--; ret_val=s_v48_srvs[i].refs; break; }
        } else if (strcmp(fn, "MxxD3D11SRV_GetDevice") == 0) {
            if (args[1]) { wg_blink_write_mem(engine->blink,args[1],&s_v46_d3d_device,8); s_v46_d3d_device_refs++; } ret_val=0;
        } else if (strcmp(fn, "MxxD3D11SRV_GetPrivateData") == 0) {
            if (args[2]) { uint32_t z=0; wg_blink_write_mem(engine->blink,args[2],&z,4); } ret_val=0x887A0002u;
        } else if (strcmp(fn, "MxxD3D11SRV_SetPrivateData") == 0 || strcmp(fn, "MxxD3D11SRV_SetPrivateDataInterface") == 0) {
            ret_val=0;
        } else if (strcmp(fn, "MxxD3D11SRV_GetResource") == 0) {
            uint64_t r=0; for (uint32_t i=0;i<s_v48_srv_count;i++) if (s_v48_srvs[i].obj==args[0]) { r=s_v48_srvs[i].resource; break; }
            if (args[1]) wg_blink_write_mem(engine->blink,args[1],&r,8); ret_val=0;
        } else if (strcmp(fn, "MxxD3D11SRV_GetDesc") == 0) {
            if (args[1]) for (uint32_t i=0;i<s_v48_srv_count;i++) if (s_v48_srvs[i].obj==args[0]) { wg_blink_write_mem(engine->blink,args[1],s_v48_srvs[i].desc,24); break; } ret_val=0;
        } else if (strcmp(fn, "MxxD3D11Device_CreateTexture2D") == 0) {
            uint64_t out=args[3], z=0; if (out) wg_blink_write_mem(engine->blink,out,&z,8);
            if (!args[1] || !out || s_v48_texture_count>=MXX_V48_MAX_TEXTURES) ret_val=0x80070057u;
            else {
                uint32_t desc[11]={0};
                if (!wg_blink_read_mem(engine->blink,args[1],desc,44) || desc[0]==0 || desc[1]==0 || desc[0]>16384 || desc[1]>16384) ret_val=0x80070057u;
                else {
                    uint32_t pitch=desc[0]*4u; uint64_t src=0; uint32_t src_pitch=0;
                    if (args[2]) { uint8_t sd[16]={0}; if (wg_blink_read_mem(engine->blink,args[2],sd,16)) { memcpy(&src,sd,8); memcpy(&src_pitch,sd+8,4); if (src_pitch && src_pitch<(64u*1024u*1024u)) pitch=src_pitch; } if (!src_pitch) src_pitch=pitch; }
                    uint64_t total=(uint64_t)pitch*(uint64_t)desc[1]; if (desc[3]>1 && total<=0xFFFFFFFFu/(uint64_t)desc[3]) total*=desc[3];
                    if (total==0 || total>(128ull*1024ull*1024ull)) ret_val=0x8007000Eu;
                    else {
                        if (!s_v48_texture_vt) {
                            uint64_t q=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_QueryInterface");
                            uint64_t a=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_AddRef");
                            uint64_t r=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_Release");
                            uint64_t gd=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_GetDevice");
                            uint64_t gp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_GetPrivateData");
                            uint64_t sp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_SetPrivateData");
                            uint64_t si=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_SetPrivateDataInterface");
                            uint64_t gt=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_GetType");
                            uint64_t se=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_SetEvictionPriority");
                            uint64_t ge=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_GetEvictionPriority");
                            uint64_t de=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11Texture2D_GetDesc");
                            uint64_t tv[11]={q,a,r,gd,gp,sp,si,gt,se,ge,de}; s_v48_texture_vt=wg_guest_alloc(engine,11*8); wg_blink_write_mem(engine->blink,s_v48_texture_vt,tv,sizeof(tv));
                        }
                        MxxV48TextureRec *t=&s_v48_textures[s_v48_texture_count++]; memset(t,0,sizeof(*t));
                        t->obj=wg_guest_alloc(engine,16); t->data=wg_guest_alloc(engine,(uint32_t)total); memcpy(t->desc,desc,44); t->refs=1; t->row_pitch=pitch; t->bytes=(uint32_t)total;
                        wg_blink_write_mem(engine->blink,t->obj,&s_v48_texture_vt,8);
                        if (src) { uint32_t rows=desc[1], copy_pitch=pitch; uint8_t tmp[4096]; for(uint32_t y=0;y<rows;y++){uint32_t left=copy_pitch;uint64_t ss=src+(uint64_t)y*src_pitch,dd=t->data+(uint64_t)y*pitch;while(left){uint32_t n=left>sizeof(tmp)?sizeof(tmp):left;if(!wg_blink_read_mem(engine->blink,ss,tmp,n))break;wg_blink_write_mem(engine->blink,dd,tmp,n);ss+=n;dd+=n;left-=n;}} }
                        wg_blink_write_mem(engine->blink,out,&t->obj,8);
                        WG_LOGI(TAG,"GFX V48 TEXTURE2D CREATE OK: obj=0x%llX data=0x%llX %ux%u fmt=%u mip=%u array=%u pitch=%u bind=0x%X",
                            (unsigned long long)t->obj,(unsigned long long)t->data,desc[0],desc[1],desc[4],desc[2],desc[3],pitch,desc[8]);
                        ret_val=0;
                    }
                }
            }
        } else if (strcmp(fn, "MxxD3D11Device_CreateShaderResourceView") == 0) {
            uint64_t out=args[3], z=0; if (out) wg_blink_write_mem(engine->blink,out,&z,8);
            if (!args[1] || !out || s_v48_srv_count>=MXX_V48_MAX_SRVS) ret_val=0x80070057u;
            else {
                bool resource_known=false; for(uint32_t i=0;i<s_v48_texture_count;i++) if(s_v48_textures[i].obj==args[1]) {resource_known=true;break;} for(uint32_t i=0;i<s_v47_buffer_count && !resource_known;i++) if(s_v47_buffers[i].obj==args[1]) {resource_known=true;break;}
                if (!resource_known) ret_val=0x80070057u;
                else {
                    if (!s_v48_srv_vt) {
                        uint64_t q=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_QueryInterface");
                        uint64_t a=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_AddRef");
                        uint64_t r=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_Release");
                        uint64_t gd=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_GetDevice");
                        uint64_t gp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_GetPrivateData");
                        uint64_t sp=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_SetPrivateData");
                        uint64_t si=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_SetPrivateDataInterface");
                        uint64_t gr=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_GetResource");
                        uint64_t de=wg_dll_mapper_resolve(engine->dll_mapper,"d3d11.dll","MxxD3D11SRV_GetDesc");
                        uint64_t sv[9]={q,a,r,gd,gp,sp,si,gr,de}; s_v48_srv_vt=wg_guest_alloc(engine,9*8); wg_blink_write_mem(engine->blink,s_v48_srv_vt,sv,sizeof(sv));
                    }
                    MxxV48SrvRec *v=&s_v48_srvs[s_v48_srv_count++]; memset(v,0,sizeof(*v)); v->obj=wg_guest_alloc(engine,16);v->resource=args[1];v->refs=1;if(args[2])wg_blink_read_mem(engine->blink,args[2],v->desc,24);
                    wg_blink_write_mem(engine->blink,v->obj,&s_v48_srv_vt,8); wg_blink_write_mem(engine->blink,out,&v->obj,8);
                    WG_LOGI(TAG,"GFX V48 SRV CREATE OK: obj=0x%llX resource=0x%llX",(unsigned long long)v->obj,(unsigned long long)v->resource); ret_val=0;
                }
            }
        } else '''

s = s.replace(dispatch_anchor, dispatch + dispatch_anchor, 1)

# Reset V48 guest objects with every V47 VM reset.
reset_old = 's_v46_dxgi_max_latency=3; s_v47_buffer_vt=0; s_v47_buffer_count=0;\n                    memset(s_v47_buffers,0,sizeof(s_v47_buffers));'
reset_new = 's_v46_dxgi_max_latency=3; s_v47_buffer_vt=0; s_v47_buffer_count=0;\n                    s_v48_texture_vt=0; s_v48_srv_vt=0; s_v48_texture_count=0; s_v48_srv_count=0;\n                    memset(s_v47_buffers,0,sizeof(s_v47_buffers)); memset(s_v48_textures,0,sizeof(s_v48_textures)); memset(s_v48_srvs,0,sizeof(s_v48_srvs));'
if s.count(reset_old) != 2:
    raise SystemExit(f'ERROR: V48 expected 2 V47 reset anchors, found {s.count(reset_old)}')
s = s.replace(reset_old, reset_new)

p.write_text(s, encoding='utf-8')
print('V48: safe CoCreateInstance + CPU Texture2D/SRV bootstrap applied')
