# Third-party components

MxxHub's own launcher code is separate from the upstream runtime projects fetched during the GitHub Actions build.

## WineGlass

Project: `Contonion/WineGlass`
Purpose: Windows PE loader, x86/x86-64 execution integration, Win32 emulation and Metal Win32 compositor.
License: Apache License 2.0 (per upstream repository).

## blink

Project: `jart/blink`
Purpose: x86-64/x86 CPU emulation used by the experimental WineGlass backend.
License: see the upstream blink repository included/fetched by the build.

The build records the exact fetched Git commit IDs in `External/UPSTREAM_VERSIONS.txt` before compiling.
