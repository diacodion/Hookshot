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

## The dev loop: edit, build, measure in a scene

```sh
$HOOKSHOT_PYTHON -I nx2/build.py --standalone --jobs 8     # what changed only: 8 s for a gpu/vk/*.cpp edit
python3 -I scripts/macos/scene.py kokiri                   # boot, Continue, settle, 60 s measured, summary.json
python3 -I scripts/macos/scene.py market RX_VK_CHUNK=60    # a teleport first (--list: the scenes)
python3 -I scripts/macos/scene.py kokiri --ab RX_VK_CAPTURE_WAIT=0 --repeat 2   # alternating runs, per-arm fps
```

`scene.py` (a wrapper of the control bridge, `scripts/hookshot_ctl.py`, and the frame trace, `RX_VK_FRAME_TRACE`) runs the
game from `$OOT_WORK/dev`: clones of the build's binaries (APFS, free; taken again after each build) with an options file
of its own (`scripts/macos/bench_options.ini`: uncapped, 2048x1152), so the player's options and saves are never touched.
Each run plays from a fresh copy of the saves (a teleport persists into them), presses through the title by the game's
state, teleports, waits until no draw is left out for pipelines still compiling, measures, and writes
`$OOT_WORK/runs/<name>/summary.json`: fps, frame-time p50/p95/p99, the time to play and to settle, the stalls before.

Measured on the M4 Max (2026-11-12): a build with nothing to compile 6.7 s, a one-line renderer edit 8.0 s, a host edit
4.5 s (the lift is kept). A run in Kokiri Forest reaches play in ~18 s and is settled in ~33 s, ~63 s in all for 30 s
measured (the fixed-time input script took ~100 s to play); the Market, by teleport, settles in ~52 s. Two runs of the
same build agree within ~0.3 fps, so an A/B of a few runs tells 1% apart. After a change to the shader translation (or
with Metal's cache cold) the first run compiles ~800 pipelines: 5-9 minutes before Kokiri Forest is settled.

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
  loaded, the first compute pipeline of gameplay never came back from Metal's compiler (likely the compiles below that
  never finished, asked for in the draw).
- **No `VkPipelineCache` at all** (`driver_cache_off`, `RX_VK_MVK_PIPELINE_CACHE=1` for one). MoltenVK compiles a
  pipeline's Metal libraries while holding its pipeline cache's one lock, so every compile queued behind the one under
  way and a draw waiting for its pipeline waited behind compiles it had nothing to do with: submits of 2-12 s after a save
  loaded. Kokiri Forest from a cold Metal cache, two runs each: frames of over 250 ms before it settled 26-29 s (was
  75-81 s), in play after 35-43 s (was 67-96 s); every pipeline compiled after 452-530 s (was 314-382 s).
- **The big programs' global memory helpers in one exit** (`Profile::global_memory_single_exit`, `RX_VK_GM_SINGLE_EXIT`).
  Eden's helpers for a guest pointer search the bound buffers, the tables and the page table with a return in each
  test and are `DontInline`, which MoltenVK's SPIRV-Cross drops in MSL: Metal inlined them at every access and its
  compiler never finished two compute programs (3e6fe279, 44511d69; a worker and a compiler process at 100% held for
  the session, the dispatch left out every frame), 95 s for a third. They are the forest's distant trees: until this,
  Kokiri Forest had open sky where they stand. A program with at least 64 global accesses in 24000 IR instructions now
  gets helpers with one return (a result and a matched flag); they compile in 2-7 s. Every program in one exit cost
  ~1.3 ms a frame (each access runs all its tests), hence the limits (`EDEN_NX2_GM_SINGLE_EXIT_MIN_GLOBAL`, `_MIN_INSTS`).

## The picture: FSR 3.1 in the game's upscaler's place

OoT draws its 3D below the output size and scales it up with a network of its own (one for each Render resolution share),
whose matrix multiplies want cooperative matrices. Without them (MoltenVK, as AMD RDNA 2) the networks are left out and
their upscales become linear stretches: the 3D looks soft and blocky, and the game's dithering and the distant grass
blades, which the network resolves, stay as dots. On a Mac the first start therefore picks **AMD FSR 3.1** as the Upscaler
(built from AMD's shaders, `vk_fsr3.cpp`; about 1 ms of an M4 Max's frame), and the Quality preset by the Mac's memory (8 GB
Minimum, 16 GB Low, larger the defaults; before, every Apple GPU was "integrated" and had Minimum: the 3D at half size).
FSR 3.1 also takes the shares whose networks do not translate (upstream's `temporal_rejected_network`, issue #1829), and
since #2305 it runs on the temporal chain, with the characters' own motion.
An options file saved before keeps its rows: Insert menu, Graphics, Upscaler and Render resolution.

## Performance (M4 Max, Kokiri Forest standing, headless, 2048x1152 output)

| Render resolution | GPU per frame | fps |
|---|---|---|
| 50% | 26 ms | 34.5 |
| 75% (the game's, Auto) | 28 ms | 30 |
| 75%, FSR 3.1 | 29 ms | 31 |
| 100% | 36 ms | 25 |

Measured before the merge of upstream's main of 2026-11-11 (0ec31ff6). After it, same place and settings: 30.8 fps
(the pre-merge build 32 in the same session), GPU time unchanged at 28 ms. The first start after an update that changes
the shader translation builds every pipeline again (Metal's own cache misses): ~800 pipelines, several minutes in
Kokiri Forest, during which draws are left out and the picture is washed out white.

After the merge of 2026-11-12 (upstream 5a0c21cb): 31.0 and 30.9 fps against 30.6 for the build before it, run between
them, so no change in speed; the same GPU time and picture. Game memory is imported in 4 MB chunks now that the pools
have mirrors (#2685): ~1,590 dirty pages per 600 submits synced instead of ~2,580, and ~100 MB less in textures.

**VRAM mirrors on** (`mirror_on`, `RX_VK_MIRROR=0` to turn off). Upstream's #2419 makes a readback of GPU results wait for
its submission's last part wherever a page is written in place, as on a GPU with no mirrors (issue 997's rule; #2602 lets
mirrored pools wait only for the part that writes them back). The Mac had none: readbacks 3.3 ms each, 26.8 fps. With
them, 0.4 ms and 30.8 fps, the same GPU time and picture.

MoltenVK encodes a submission's Metal commands inside `vkQueueSubmit`; the host sets `MVK_CONFIG_SYNCHRONOUS_QUEUE_SUBMITS=0`
(`driver_cache_room`, unless it is set already) so that this happens on MoltenVK's own thread: at 75% 30 -> 33.5 fps, the
replay's submits from ~8 ms a frame to nothing.

Most of the frame does not scale with resolution. About 10 ms of it is the task and mesh programs' compute fallback (issue
640: ~50 mesh draws a frame, each a render pass split, four full barriers and its dispatches; the dispatches ~5 ms, the
drawing itself ~4 ms, the splits and barriers ~1 ms, single runs before two merges), which MoltenVK's mesh shaders would
replace in part (an open MoltenVK PR, KhronosGroup/MoltenVK#2842, not in a release). Metal's fast math
(`MVK_CONFIG_FAST_MATH_ENABLED`), the NaN guard and invariant positions made no difference.

**What the frame waits for (2026-11-12, Metal System Trace).** With Xcode installed,
`xcrun xctrace record --template 'Metal System Trace' --attach <pid> --time-limit 4s` records every encoder's GPU time
(export the `metal-gpu-intervals` and `metal-application-command-buffer-submissions` tables). In Kokiri Forest the GPU is
busy 73% of the time at its top clock (~23.5 ms of a 32 ms frame): ~3.7 ms a frame it waits for the next command buffer to
be committed (MoltenVK encodes ~10 ms of Metal commands a frame on its queue thread) and ~2.7 ms goes in gaps of over
0.1 ms inside command buffers, most at the fallback's fill -> dispatch -> render steps. The `GPU time` line (a timestamp
pair a command buffer: MoltenVK samples them at encoder boundaries) counts that idle too; MoltenVK's timestamps inside a
command buffer collapse, so `RX_VK_PASS_TIMING` has nothing to show. Not the bottleneck, measured in alternating runs:
the replay's wait for the exposure meter's late pages (~10 ms a frame on the game's main thread; `RX_VK_CAPTURE_WAIT=0`
31.2/31.1 fps against 31.6/31.0) and the submission's part size (`RX_VK_CHUNK` 60: 30.6/30.9, 120: 30.8/30.8, 300: 29.2/29.3).

Settled scenes, uncapped, 2048x1152, `scripts/macos/scene.py`, 2026-11-12 (Kokiri Forest was 30.9 fps without its trees):

| Scene | Draws a frame | fps | Frame time p50 / p95 / p99 |
|---|---|---|---|
| Kokiri Forest, standing (with its distant trees) | ~2440 | 29.8 | 33.5 / 35.1 / 36.0 ms |
| Market, at the gate | ~2930 | 34.4 | 29.0 / 31.3 / 32.5 ms |
| Kakariko Village, at the gate | ~2600 | 34.0 | 29.3 / 30.8 / 31.6 ms |

A first visit to an area compiles its pipelines: the Market left ~200 draws a frame out for ~3 minutes, Hyrule Field some
for over 6. One machine only: no other Apple GPU has been measured, so these say nothing of a smaller Mac.

### Native mesh shaders (MoltenVK PR #2842): not yet

Tried on 2026-11-11 and 2026-11-12 with that PR built locally (on MoltenVK main; `VK_DRIVER_FILES` pointing at the build's
`MoltenVK_icd.json`). The game runs and most of the PR's mesh pipelines build, but foliage comes out wrong (white leaves, glowing
or missing grass), so the compute fallback stays. What is known:

- **Metal refuses four to eight of the ~50 mesh pipelines in Kokiri Forest**: "Threadgroup memory size (35868..42908) exceeds the
  maximum threadgroup memory allowed (32768)", the foliage's G-buffer passes (8-9 outputs; SPIRV-Cross stages a mesh's outputs
  in threadgroup memory). Their draws take the compute fallback one program at a time (`mesh_native_refused`).
- **That per-program fallback was itself wrong until b27b89b0**: on a driver with mesh shaders its compute translations took
  the task and mesh stages' one-lane paths (every lane read as lane 0), and the grass's refused colour pass drew nothing. Fixed:
  `profile_pinned` gives them compute's subgroups. With every program sent to the fallback one by one, the PR's MoltenVK now
  matches Homebrew's picture. The bisects before that fix (which named 879fe82e and 8a8304ed) were confounded by it.
- **Native task and mesh stages have no subgroups on this driver** (it reports them in tessellation control, fragment and
  compute only, 0x32), so they take the one-lane paths, which are wrong by construction for OoT's foliage: its task and mesh
  programs index their items by lane and compact them with ballots. Metal itself has SIMD-group functions in object and mesh
  functions (a standalone test on the M4 Max: lanes, broadcasts and ballots right in both); `RX_VK_MESH_SUBGROUPS=1` gives
  the native stages subgroups for testing.
- **With subgroups, most native mesh programs draw right, but not all.** Mesh-only programs native, task + mesh programs on
  the fallback (`RX_VK_TASK_MESH_FALLBACK=1`): 879fe82e alone (the falling leaves' depth pass) puts white leaves in the air,
  and at least one other mesh-only program white plants; seven others checked together are clean. Every program native with
  `RX_VK_MESH_SUBGROUPS=1`: the native task programs (the grass's) also differ from the fallback's (the grass glows white).
  Not the cause: the task payload's size (set explicitly in the PR's pipeline: no change), Metal's fast math, depth-equal
  precision (`RX_VK_EQ_BIAS=64`, in the clean configuration too), mesh-to-fragment interfaces (all 25 pairs match), multi-record
  indirect draws (879fe82e's have one record).
- No GPU time saving is known yet: the native runs that ran faster drew less.
- To bisect: `RX_VK_LOG_MESH_KEYS=1` names the programs, `RX_VK_MESH_NATIVE_KEYS=<hex>,...` keeps only those native and
  `RX_VK_MESH_FALLBACK_KEYS=<hex>,...` sends those to the fallback (prefixes match); `MVK_CONFIG_SHADER_DUMP_DIR` writes the
  MSL (`pipeline-*.txt` names a pipeline's `shader-*` and `shader-fs-*` files). Judge a run by a pixel difference against a
  fallback run at the same frame: the white shapes move between runs.

Useful when something goes wrong: `MVK_CONFIG_LOG_LEVEL=3` (MoltenVK's messages in the log), with `MVK_CONFIG_DEBUG=1`
the encoders of a failed Metal command buffer by name, and `MTL_DEBUG_LAYER=1` for Metal's own validation.

## Open

- Some objects draw as white shapes in Kokiri Forest. Seen on the Linux build too (v2.0.x), so not taken as macOS's.
- "Metal does not support disabling primitive restart" (MoltenVK's warning): draws whose indices hold the restart value
  with restart off would break; none seen yet.
- Metal's validation reports an image view destroyed while a command buffer still uses it.
- HOS_NO_MOVIES=1 leaves this game on a black screen (not macOS's doing).
- The grass and other mesh-drawn objects go through the compute fallback (above; native mesh shaders: not yet).
- No builder, launcher or `make_game.py` route (PR #1872 upstream has a macOS Builder, for its WebGPU renderer); the hot-function list and profile (`work/hot_funcs.txt`,
  `hookshot_pieces.prof`) are not in the repository, so the build is the plain one.
