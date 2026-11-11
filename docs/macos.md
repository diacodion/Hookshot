# macOS (Apple silicon)

Experimental. The game builds and runs natively on Apple silicon (arm64): the lifted C compiles for the Mac's own
CPU (the guest's CPU family, so the lifted floating point runs on the host FPU as it is), the host runtime takes the
Linux route (`host/posix`, SDL, FFmpeg), and the Vulkan back end runs on Metal through MoltenVK.

Checked on one machine only: a MacBook Pro with an M4 Max (36 GB), macOS 26.6, MoltenVK 1.4.2. Intel Macs are not
supported (the code paths are there, nothing was run).

## Build from source

```sh
scripts/macos/deps.sh                 # Homebrew's tools and libraries; the Vulkan back end's headers into ../deps/cpm
python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
. scripts/macos/env.sh                # RX_CPM, OOT_WORK (../work), HOOKSHOT_PYTHON

$HOOKSHOT_PYTHON -I nx2/nup.py <game.nup> --extract $OOT_WORK/dump
$HOOKSHOT_PYTHON -I nx2/dump.py --dump $OOT_WORK/dump --set
$HOOKSHOT_PYTHON -I nx2/lift.py --standalone --write          # ~80 s
$HOOKSHOT_PYTHON -I nx2/build.py --standalone --jobs 8        # ~5 minutes on an M4 Max

scripts/macos/run-vk.sh --window --sound --seconds 3600       # or headless, with screenshots: see the script
```

The game is `$OOT_WORK/sa/build/hookshot` with `hookshot_vk.so` beside it (a Mach-O dylib under the Linux name).
`make_game.py` and the builders have no macOS route yet.

## What differs from Linux

| Where | What |
|---|---|
| `nx2/build.py` (`MAC`, `ARM64`) | Apple's ld (`-dead_strip`, `-export_dynamic`, `-order_file`); `hookshot_vk.so` linked with `-undefined dynamic_lookup`; `-mcpu=apple-m1` and no `-mcx16`; Homebrew's prefix; free memory from `vm_stat` |
| `host/posix/darwin.h` | futexes on `os_sync_wait_on_address` (macOS 14.4+), thread ids from `pthread_threadid_np`, monotonic waits, no CPU affinity (masks say "every core") |
| `host/posix/win32_posix.c` | fibers switched in this file's own assembly (no `ucontext`); `VirtualQuery` from `mach_vm_region`; signal contexts for arm64; libraries looked for in Homebrew's prefix (`HOOKSHOT_LIB_PATH` first) |
| guest memory | 16 KB host pages under the guest's 4 KB ones: protections are widened to the host page, and freed pages are mapped anew (`hos_darwin_discard`: `MADV_DONTNEED` is only advice on macOS) |
| `host/hos_setjmp.c` | the lifted setjmp/longjmp for AAPCS64 |
| `host/posix/prof_darwin.c` | `HOS_PROFILE` and `HOS_THREAD_TIMES` on Mach; no `HOS_SAMPLE*` (use `sample <pid>`) and no `HOS_WATCH` |
| `host/posix/display_sdl.c` | the Vulkan surface (a Metal view) is made on the main thread, which the renderer's thread asks |

## MoltenVK

MoltenVK 1.4.2 has no mesh shaders (the compute fallback of issue 640 runs task and mesh programs), no cooperative
matrices (the upscaling networks take their stand-ins, as on AMD), no geometry shaders, and subgroups in fragment and
compute stages only. What had to change for it, each found in play:

- **Descriptor sets from a pool, not pushed** (`ensure_layout`, `RX_VK_PROGRAM_PUSH`). Eden's SPIR-V declares one
  binding several times; SPIRV-Cross aliases a descriptor array only inside a Metal argument buffer, and a pushed set
  is never one: "Attempting to alias same binding with a descriptor array which is not implemented through argument
  buffers".
- **Subgroup operations only where the driver has them** (`make_profile`, `supported_subgroup_stages`): vertex-stage
  programs run their one-lane paths. Before: "Subgroup builtins are not available in this type of function".
- **8-bit index buffers bound with their size** (`bind_index`, `vkCmdBindIndexBuffer2KHR`). MoltenVK converts the
  whole bound range to 16-bit indices before each such draw; the range of a plain bind is the rest of an imported pool.
  A hundred draws a frame made Metal refuse the command buffer: "Insufficient Memory
  (kIOGPUCommandBufferCallbackErrorOutOfMemory)", which MoltenVK reports as a lost device. An indirect draw's count is
  not known when it is bound: 16 KB of indices are bound for it.
- **No `VkPipelineCache` file** (`pcache_init`, `RX_VK_PCACHE_LOAD=1` to try it). With the file of an earlier run
  loaded, the first compute pipeline of gameplay never came back from Metal's compiler.

Useful when something goes wrong: `MVK_CONFIG_LOG_LEVEL=3` (MoltenVK's messages in the log), with `MVK_CONFIG_DEBUG=1`
the encoders of a failed Metal command buffer by name, and `MTL_DEBUG_LAYER=1` for Metal's own validation.

## Open

- Some objects draw as white shapes (seen in Kokiri Forest's first cutscene).
- "Metal does not support disabling primitive restart" (MoltenVK's warning): draws whose indices hold the restart value
  with restart off would break; none seen yet.
- Metal's validation reports an image view destroyed while a command buffer still uses it.
- HOS_NO_MOVIES=1 leaves this game on a black screen (not macOS's doing).
- No builder, launcher or `make_game.py` route; the hot-function list and profile (`work/hot_funcs.txt`,
  `hookshot_pieces.prof`) are not in the repository, so the build is the plain one.
