# MxxHub v0.4.9.57 — Windows V48

Hollow Knight runtime build.

V48 keeps manual launch, V47 VM/buffer stability, adds CPU-backed D3D11 Texture2D + ShaderResourceView bootstrap objects, and fixes the false-success `CoCreateInstance` path that caused the observed `RIP=0xF` SIGSEGV.

Build the unsigned IPA with the included GitHub Actions workflow.
