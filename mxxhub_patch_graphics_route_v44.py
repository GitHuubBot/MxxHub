#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: mxxhub_patch_graphics_route_v44.py <WineGlass-root>')

wg = Path(sys.argv[1]).resolve()
p = wg / 'Sources/Core/wg_engine.c'
if not p.is_file():
    raise SystemExit(f'ERROR: missing {p}')

s = p.read_text(encoding='utf-8')
MARKER = 'MXXHUB_WINDOWS_V44_UNITY_D3D11_GRAPHICS_ROUTE'

if MARKER not in s:
    # V34 deliberately forced OpenGL 3.2 only to expose the next graphics wall.
    # The V43 device log proves Unity 6000.0.61f1 creates its real player window,
    # then aborts before calling any WGL entry point. Stop forcing that diagnostic
    # path and use the normal Windows D3D11 player backend instead.
    old_args = ' -force-glcore32 -force-clamped -force-gfx-direct -screen-fullscreen 0 -screen-width 1280 -screen-height 720'
    new_args = ' -force-d3d11 -force-d3d11-singlethreaded -force-d3d11-bitblt-model -force-gfx-direct -screen-fullscreen 0 -screen-width 1280 -screen-height 720'
    if old_args not in s:
        if new_args not in s:
            raise SystemExit('ERROR: V44 Hollow Knight graphics command-line anchor changed')
    else:
        s = s.replace(old_args, new_args, 1)

    # Add an unambiguous breadcrumb just before the normal Win32 dispatch chain.
    # Do not fabricate a renderer here; the goal of this patch is to stop the
    # known-bad forced-OpenGL selection and make the actual D3D11 route visible.
    dispatch_anchor = '        if (strcmp(fn, "RtlAddGrowableFunctionTable") == 0) {'
    if dispatch_anchor not in s:
        raise SystemExit('ERROR: V44 dispatch anchor changed')
    block = '''        /* MXXHUB_WINDOWS_V44_UNITY_D3D11_GRAPHICS_ROUTE */
        if (strcmp(fn, "D3D11CreateDevice") == 0) {
            static uint32_t s_v44_d3d11_calls = 0;
            s_v44_d3d11_calls++;
            WG_LOGI(TAG,
                    "GFX V44 D3D11 ROUTE: D3D11CreateDevice call=%u adapter=0x%llX driver=%llu flags=0x%llX sdk=%llu outDevice=0x%llX outContext=0x%llX",
                    s_v44_d3d11_calls,
                    (unsigned long long)args[0],
                    (unsigned long long)args[1],
                    (unsigned long long)args[4],
                    (unsigned long long)args[6],
                    (unsigned long long)args[8],
                    (unsigned long long)args[10]);
            /* V44 does NOT lie with S_OK + NULL device pointers. The current
             * WineGlass backend has no D3D11 implementation yet. Return the
             * Windows DXGI unsupported code with deterministic NULL outputs.
             * This prevents a bogus COM dereference while making the next
             * required graphics bridge explicit in the device log. */
            uint64_t zero64 = 0;
            uint32_t zero32 = 0;
            if (args[8])  wg_blink_write_mem(engine->blink, args[8],  is_32bit ? (void *)&zero32 : (void *)&zero64, is_32bit ? 4 : 8);
            if (args[9])  wg_blink_write_mem(engine->blink, args[9],  &zero32, 4);
            if (args[10]) wg_blink_write_mem(engine->blink, args[10], is_32bit ? (void *)&zero32 : (void *)&zero64, is_32bit ? 4 : 8);
            ret_val = 0x887A0004u; /* DXGI_ERROR_UNSUPPORTED */
            WG_LOGW(TAG, "GFX V44 D3D11 BACKEND REQUIRED: no D3D11->Metal bridge is present; returning DXGI_ERROR_UNSUPPORTED instead of false S_OK");
        } else if (strcmp(fn, "D3D11On12CreateDevice") == 0) {
            WG_LOGW(TAG, "GFX V44 D3D11ON12: unsupported in WineGlass graphics path");
            ret_val = 0x80004001u; /* E_NOTIMPL */
        } else if (strcmp(fn, "CreateDXGIFactory") == 0 || strcmp(fn, "CreateDXGIFactory2") == 0) {
            uint64_t outp = strcmp(fn, "CreateDXGIFactory2") == 0 ? args[2] : args[1];
            uint64_t zero64 = 0; uint32_t zero32 = 0;
            if (outp) wg_blink_write_mem(engine->blink, outp, is_32bit ? (void *)&zero32 : (void *)&zero64, is_32bit ? 4 : 8);
            WG_LOGW(TAG, "GFX V44 DXGI FACTORY: %s reached before a graphics bridge exists", fn);
            ret_val = 0x887A0004u; /* DXGI_ERROR_UNSUPPORTED */
        } else if (strcmp(fn, "RtlAddGrowableFunctionTable") == 0) {'''
    s = s.replace(dispatch_anchor, block, 1)

    # V43 handled the canonical backslash path. Mono also constructs mixed
    # slash AOT cache paths such as "\\Managed/mono/.../mscorlib.dll.dll".
    # V40's original pair (\\Managed\\ OR /Managed/) does not match that form.
    import re
    mixed_pat = re.compile(
        r'(bool\s+v40_managed_native_probe\s*=\s*\n\s*)'
        r'strcasestr\(load_name, "\\\\Managed\\\\"\) != NULL \|\|\s*\n\s*'
        r'strcasestr\(load_name, "/Managed/"\) != NULL;'
    )
    if 'strcasestr(load_name, "\\Managed/") != NULL' not in s:
        m = mixed_pat.search(s)
        if not m:
            raise SystemExit('ERROR: V44 Managed mixed-slash probe anchor changed')
        indent = re.search(r'\n(\s*)strcasestr', m.group(0)).group(1)
        replacement = (
            m.group(1) +
            'strcasestr(load_name, "\\\\Managed\\\\") != NULL ||\n' + indent +
            'strcasestr(load_name, "\\\\Managed/") != NULL ||\n' + indent +
            'strcasestr(load_name, "/Managed\\\\") != NULL ||\n' + indent +
            'strcasestr(load_name, "/Managed/") != NULL;'
        )
        s = s[:m.start()] + replacement + s[m.end():]

    p.write_text(s, encoding='utf-8')
    print('V44: removed the V34 forced OpenGL 3.2 diagnostic route')
    print('V44: Hollow Knight now requests Unity D3D11 single-threaded/BitBlt graphics')
    print('V44: D3D11/DXGI calls are deterministic and can no longer return false S_OK with NULL outputs')
else:
    print('V44: graphics route patch already present')

f = p.read_text(encoding='utf-8')
for token in (
    MARKER,
    '-force-d3d11 -force-d3d11-singlethreaded -force-d3d11-bitblt-model',
    'GFX V44 D3D11 ROUTE:',
    'GFX V44 D3D11 BACKEND REQUIRED:',
    'DXGI_ERROR_UNSUPPORTED',
    'strcasestr(load_name, \"\\\\Managed/\") != NULL',
):
    if token not in f:
        raise SystemExit('ERROR: V44 verification failed: ' + token)
if '-force-glcore32' in f:
    raise SystemExit('ERROR: V44 verification failed: old forced OpenGL route still present')

print('MXXHUB_WINDOWS_V44_UNITY_D3D11_GRAPHICS_ROUTE_OK')
