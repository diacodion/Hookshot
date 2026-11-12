# Contributing to Hookshot

Hookshot is a static recompilation of the Switch 2 *The Legend of Zelda: Ocarina of Time* remake: the game's AArch64
code is lifted to C, the Horizon OS and NVN2 are implemented natively, and the GPU work is replayed on Vulkan. No
emulator is involved, and the game is built on your PC from your own copy.

It lives on the team's Forgejo, [git.notrexed.dev/Hookshot/Hookshot](https://git.notrexed.dev/Hookshot/Hookshot).
This page gets you set up, then covers what to send, how to build and test it, and the rules that keep the project
out of trouble. The [development guide](docs/development.md) has the full pipeline; the [design notes](docs/design.md)
have the architecture.

## The rule with no exceptions

**No game files, keys, or anything made from them leave your PC.** The repository does not contain the game and never
will. That includes:

- the `.nup`, `romfs`, `exefs`, `.nso`, `.nca`, `.nsp`, `.xci` or `.nsz`/`.xcz` files, and any title keys;
- `work/` and everything in it: the extracted dump, `image.bin`, the lifted C, test builds, runs and screenshots;
- `hookshot.exe`, `hookshot_vk.dll`, `hookshot_programs.bin` (and `hookshot_programs.idx`), `hookshot_pipelines.bin`,
  `mkw_vk_pipelines.bin` and other build output;
- saves (`user/`), `hookshot_options.ini`, `hookshot_env.txt` and `language.txt`.

Don't ask in an issue, a pull request or the chat where to get the game, how to decrypt it, or for someone else's
files. A screenshot, a video and `hookshot_log.txt` are fine and often necessary: the log holds no game data.
`.gitignore` covers most of this, but check `git status` (or `jj status`) before you commit. If you aren't sure whether
a file is game data, leave it out.

## Get started

Five minutes, then a first build.

1. **Clone:**

   ```
   git clone https://git.notrexed.dev/Hookshot/Hookshot.git
   cd Hookshot
   ```

   (jj users: `jj git clone --colocate https://git.notrexed.dev/Hookshot/Hookshot.git`.)

2. **Sign in:**

   ```
   python -I scripts/forge.py login
   ```

   Click **Sign in with Discord** (or sign in with the email account you made on git.magicbean.dev) and approve. That
   one sign-in covers git.magicbean.dev (git signs in by itself from then on, and the remote `forge` points at
   Hookshot), the team chat and [test requests](docs/test-requests.md). The first time creates your account.
   [docs/forge.md](docs/forge.md) has the details and the other commands.

3. **Check the game-free tests run** (no game needed):

   ```
   python -I -B tests/run_all.py
   ```

4. **Build the game** from your own dump: [Development setup](#development-setup) below, then [Building](#building).

Then say hello in Discord and claim something in `#dev-work` before you start on it.

## Ways to help

What to work on first, and the steps from claiming a problem to its release, are in the [workflow](docs/workflow.md):
priorities by severity and by how many players run the affected hardware, and the hardware tiers a change must not
break.

| | What |
|---|---|
| **Test on other hardware** | NVIDIA cards are the most tested; the README's [status table](readme.md#project-status) says what works where, and [tested configurations](docs/tested-configurations.md) lists the runs behind it. Reports from AMD and Intel cards, other drivers, other distributions and SteamOS help even when everything works. Pull requests that need a run on your hardware are queued as [test requests](docs/test-requests.md). |
| **Play further** | The tested area is the start of the game. Reaching new areas, cutscenes, menus and items finds problems nobody has seen. |
| **Report what is broken** | Use the [issue forms](https://git.notrexed.dev/Hookshot/Hookshot/issues/new/choose): game problem, build problem or idea. Include the version, your hardware, where you were and `hookshot_log.txt`. |
| **Send a fix** | Branch, fix, test, open a pull request from your fork ([how](#commits-and-pull-requests)). Small, focused changes are the easiest to review. |
| **Improve the docs** | The README, the development guide and the design notes are part of the project. |

## Before you open an issue or a PR

- Try current `main` first: it has every fix so far.
- Search the [open issues](https://git.notrexed.dev/Hookshot/Hookshot/issues). If yours is there, add your
  details instead of opening another.
- English, please. One topic per issue or pull request.
- Report the version you actually ran: the first line of `hookshot_log.txt`.
- For a graphics or performance problem, test without ReShade, shader-cache tools, overlays and frame-generation
  injectors: they change what Hookshot does.

## Development setup

You need your own supported dump: program `0400486024DEC000`, update v131072, build `e0f3ccfa`. Other versions don't
work, and the build reads the dump where it lies without changing it. A build needs about 60 GB free and a run about
10 GB of RAM. Build on an SSD if you can: a hard drive works but takes much longer.

Hookshot has no releases of its own yet. Where a step below needs files from the Builder, use the newest
[Hookshot release](https://github.com/NotRexed/Hookshot/releases/latest), which installs the same tools under the name
Hookshot Builder.

### Windows

1. Install Python 3 from [python.org](https://www.python.org/downloads/) (not the Microsoft Store build: `python -I`
   doesn't see packages installed there), then the pipeline's packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Get [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) (the `ucrt-x86_64` build) and set `HOOKSHOT_MINGW` to its
   folder. Without it the build falls back to emsdk's clang at `C:\emsdk` with Visual Studio's C++ tools and the
   Windows SDK (`HOOKSHOT_MSVC=1` forces that route).

3. Copy what a clone doesn't carry from the Builder's `tools` folder. Run the Builder once; the folder is
   `%LOCALAPPDATA%\HookshotBuilder\versions\<version>\HookshotBuilder\tools`.

   | From `tools` | To | Variable |
   |---|---|---|
   | `cpm` | anywhere | `RX_CPM` points at it (Vulkan headers and FFmpeg; without it the standalone build stops, unless system headers or `HOOKSHOT_*_HEADERS` are set) |
   | `wgpu-native` | anywhere | `WGPU_NATIVE` points at it (the WebGPU fallback renderer; without it the game has none) |
   | `third_party` | `third_party\` | its `.dll`, `.exe`, `.lib` and `.sha1` files, to the same places: `a64emit`, `lld-link`, `zbic` and SDL2 |
   | `work` | `work\` | hot-function list, link order, profile and icon (without them the game builds but runs slower) |

4. Run everything with `python -I` from the repository root.

### Linux

Build the environment and the AppImage:

```sh
scripts/linux/deps.sh       # LLVM, Python packages, FFmpeg with dav1d, SDL2, Vulkan headers
scripts/linux/appimage.sh   # the AppImage, in build-linux/
```

The scripts expect Ubuntu 22.04: the AppImage carries that system's C library, so the game it builds runs on glibc
2.35 and newer. On another distribution, build in a container with the same system (Docker, or Podman with
`DOCKER=podman`):

```sh
scripts/linux/docker.sh             # deps.sh, then appimage.sh, in scripts/linux/Dockerfile's Ubuntu 22.04
scripts/linux/docker.sh shell       # a shell in that environment, with the checkout at /src
```

The container keeps its own `build-linux/docker-deps` and `build-linux/docker-vk-work` (parts built on the host link
against its newer glibc). Where SELinux is enforcing, the script runs the container with
`--security-opt label=disable` rather than relabelling your checkout. The AppImage needs glibc 2.35 or newer and a
Vulkan 1.3 driver with mesh shaders. `HOOKSHOT_JOBS` sets how many files compile at once. The README's
[Linux section](readme.md#on-linux) has the runtime paths.

### NVIDIA DLSS (optional)

DLSS (`gpu/vk/vk_dlss.cpp`) is built in only with NVIDIA's DLSS SDK, which is NVIDIA's, under NVIDIA's own license,
and is never committed, bundled or uploaded: not in a PR, not in a release, not in a CI artifact. Fetch it for your own
builds (it prints NVIDIA's license notice and needs `--accept-nvidia-license` or a typed yes), then build with
`HOOKSHOT_NGX_SDK`:

```powershell
python -I scripts/ngx_sdk.py --platform windows C:\path\ngx-sdk
$env:HOOKSHOT_NGX_SDK = "C:\path\ngx-sdk"
python -I nx2/make_game.py --nup "C:\path\your-game.nup" --out "C:\Games\Hookshot-dlss"
```

On Windows, NVIDIA's static `nvsdk_ngx_s.lib` is linked into `hookshot_vk.dll` (with `gpu/vk/vk_ngx_msvc_rt.cpp` and
`host/mingw_msvc_shims.c` for the runtime it expects), and `make_game.py` copies `nvngx_dlss.dll` and
`nvngx_dlssg.dll` into the game's `ngx` folder; never put NGX's core, `_nvngx.dll`, beside the game. On Linux it is
`--platform linux` (or `scripts/linux/ngx-sdk.sh`). Never share a game folder built with the SDK. A change to the
pinned SDK release updates `scripts/ngx_sdk.py`, `tests/test_ngx_sdk.py` and the release named in the Builder's license
text (`builder/HookshotBuilder/MainWindow.xaml.cs`).

### Hookshot Builder

The Builder is a .NET 8 WPF app: open `builder/HookshotBuilder.sln` in Visual Studio 2022 with the .NET desktop
workload, or `dotnet build builder/HookshotBuilder.sln`. You only need Visual Studio for the Builder; the game builds
with llvm-mingw. For a Builder that keeps its state on an external SSD, see
[portable Builder setup](docs/windows-portable-builder.md).

## Building

| Command | What it does |
|---|---|
| `python -I nx2/make_game.py --nup "C:\path\your-game.nup" --out "C:\Games\Hookshot" --language en-US` | A complete game folder from a `.nup`, in about 40 minutes. |
| `python -I nx2/make_game.py --romfs "C:\path\romfs" --exefs "C:\path\exefs" --out "C:\Games\Hookshot"` | The same, from extracted folders. |
| `python -I nx2/make_game.py --check --nup "C:\path\your-game.nup"` | Check the game and its version without building. |
| `python -I nx2/dump.py --dump <folder with exefs/ and romfs/> --set` | Record the dump in `work/dump.txt`. |
| `python -I nx2/lift.py --standalone --write` | Lift the game to C in `work/sa/gen`. |
| `python -I nx2/build.py --standalone --jobs 4` | Compile `work/sa/build/hookshot.exe` and `hookshot_vk.dll`. |
| `powershell -File scripts/run-vk.ps1 -Window -Seconds 120` | Run the standalone build and take screenshots in `work/runs/<name>/shots`. |
| `python -I scripts/prof.py <stacks file>` | Read a profile sampled with `HOS_PROFILE`, `HOS_SAMPLE` and `HOS_SAMPLE_FILE`. |

Use a separate `--work` folder (or `OOT_WORK`) to keep more than one dump. The lifted C and every build stay local.
Not measuring speed? [A faster build for development](docs/development.md#a-faster-build-for-development) compiles the
hot code at `-O1`, about a quarter less compile time; never use it for fps, timings or a release.

## Testing

### Game-free checks

They need no dump. One command runs them all:

```
python -I -B tests/run_all.py
```

It runs every `tests/test_*.py` and `nx2/test_*.py` in its own process, the C++ unit tests under `gpu/vk/tests` and a
byte-compile of every tracked Python file, keeps going after a failure, and ends with a table and every failure's
output. Name part of a path to run only matching checks (`tests/run_all.py ub_`). A check whose tool is missing (a
compiler, `spirv-val`, the SPIR-V or Vulkan headers, `zstandard`) is skipped and says so; `--strict` fails it instead.
Checks that can't run everywhere say so in their own file (`RUN_ALL_SKIP`); `--no-skip` runs them anyway.

Forgejo doesn't run these automatically yet (there is no CI runner), so run them before you open a pull request and
say so in it.

### Checks that need your dump or a run

Slow and local; they need `work/dump.txt` from `nx2/dump.py --set`:

| Command | What it checks |
|---|---|
| `python -I nx2/test_insn.py` | Every instruction form the game uses, Unicorn against the lifted C. |
| `python -I nx2/test_leaf.py --calls` | Whole functions and call trees, native against Unicorn. |
| `python -I nx2/census.py --nx2` | The instruction census per module. |
| `python -I nx2/lower_test.py work/lowfail` | Shader programs that failed to lower. |
| `powershell -File scripts/run-vk.ps1 -Window` | An in-game run: screenshots, the flash detector, frame dumps and the profiler. |

Screenshots in `work/runs/<name>/shots` reach gigabytes and come from the game: look, then delete them. The switches
for scripted input, screenshots, frame dumps and profiles are in the development guide's
[reference](docs/development.md#switches-for-testing-and-debugging).

### When you add a fix or a check

- Add a game-free test where the behaviour can be tested without the dump: a small Python file under `tests/` or
  `nx2/` that reads source files and runs them, or compiles a tiny C++ fixture.
- Name it `tests/test_*.py` or `nx2/test_*.py`, and `tests/run_all.py` picks it up. What it needs from the runner it
  declares in its own file, after its docstring and imports, as plain literals (the runner's docstring has the
  details):

  ```python
  RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]   # a compiler, headers, spirv-val ...
  RUN_ALL_MODULES = ["zstandard"]                                       # packages it imports
  RUN_ALL_SKIP = {"windows": "POSIX signals: the Linux guard"}          # can't run there, and why
  ```

  `{name}` is a tool the runner finds (`cxx`, `vulkan_headers`, `spirv_headers`, `spirv_val`, `glslang`, `dotnet`).
- Keep it fast, and make it pass on Linux and Windows where it can.
- For a renderer or runtime fix, say what you saw before and after, on which GPU and driver, and where in the game.

## Code and data conventions

- Match the file you're editing: there is no formatter, so follow the indentation, comments and naming around you. Don't
  reformat unrelated code in the same change.
- Keep OoT-specific code in new files where possible. `nx2/`, `host/` and `gpu/` stay close to mkw-recomp so upstream
  fixes apply as a patch; each sync names its mkw-recomp base commit in the commit message.
- Don't modify `third_party/`: its parts are unmodified upstream copies with their notices in `PROVENANCE.md`. Add a
  new folder and a provenance note instead.
- Treat local mkw-recomp and Eglantine checkouts as read-only references.
- New switches use `RX_` in the renderer and `HOS_` in the runtime. They are off or neutral unless asked for, cost
  nothing while off, and are documented beside the switches around them.
- The GPU replay rules in [design notes, section 4](docs/design.md#4-the-lessons-from-mkw-as-rules-here) are not
  suggestions: nothing the replay reads may come from live guest memory, waits are honoured per queue, and texture and
  sampler registrations stay in submission order.
- Python runs with `-I`. Tests stay on the standard library. The pipeline's dependencies (lz4, numpy, capstone,
  unicorn, zstandard) are the exception, not a reason to add more; so are the button prompts' (fontTools, Pillow,
  astc-encoder-py, texture2ddecoder: `nx2/prompt_requirements.txt`), for `nx2/prompt_assets.py` and
  `nx2/prompt_draw.py` alone, at build time. Reviewers' agents: flag any other use of those four. Line endings are LF
  (`.gitattributes`).
- New code has to be GPL-compatible ([Licensing](#licensing)).

## Commits and pull requests

Hookshot's `main` takes reviewed pull requests only. You work on a branch, push it to your fork and open the pull
request from there. Set up pushing once: `python -I scripts/forge.py fork` makes your fork and the remote
`forge-fork` (`pr create` does it too if you haven't).

```
git fetch forge && git switch -c fix/arc-walls forge/main        jj: jj git fetch --remote forge; jj new main@forge
... change, build, test, commit ...
git push forge-fork fix/arc-walls                                 jj: jj bookmark create fix/arc-walls -r @;
                                                                      jj git push --remote forge-fork -b fix/arc-walls
python -I scripts/forge.py pr create fix/arc-walls --title "fix(vk): walls missing on Intel Arc" --body-file pr.md
```

1. Name the branch for the change: `fix/...`, `linux/...`, `builder/...`, `docs/...`. One topic per pull request: a fix
   doesn't carry a refactor, and two unrelated fixes are two PRs.
2. Write the title as a Conventional Commit ([below](#pull-request-titles)).
3. Start the description with why the PR is needed, in one or two plain sentences a player could follow ("The menu's
   text is cut off at 4K"). Then what changed and how it was tested: GPU, driver, game area, fps before and after.
4. `Fixes #123` (one line per issue) closes the issue when the PR merges; `Part of #123` or `See #123` keeps it open.
   Search the open issues first: your bug may already be reported.
5. Include screenshots when the change shows on screen: before and after for a fix, the new screen for a feature, and
   the sizes or settings checked. A screenshot of the game is fine; the rule above is about game files.
6. Run the game-free checks, and run the change in the game; put what you measured in the description. An untested
   change is opened as a draft (`pr create --draft`).
7. When `main` moves, rebase onto it (`git rebase forge/main`, or `jj rebase -d main@forge`) and resolve conflicts in
   your branch.
8. No build output, dumps, generated files or `.gitignore` bypasses.

A maintainer reviews and squash-merges it. `forge.py pr list --mine`, `pr view <n> --comments` and `pr comment` follow
up from the command line ([docs/forge.md](docs/forge.md)).

### Pull request titles

A pull request is squash-merged, so its title becomes the commit on `main`, and that commit decides the next release
and its changelog line. The title is a [Conventional Commit](https://www.conventionalcommits.org/): a type, an optional
scope in parentheses, a colon, and a short description (`forge.py pr create` refuses anything else).

| Type | For | Example |
|---|---|---|
| `feat` | something new for players or modders | `feat(menu): tabbed category layout for in-game options with bumper and Q/E navigation` |
| `fix` | a bug fix | `fix: preserve the changelog when release generation fails` |
| `perf` | faster, or less memory | `perf(linux): hand guest memory to the GPU as udmabuf dma-bufs, not host pointers` |
| `refactor` | a change with no effect on behaviour | `refactor(vk): ...` |
| `docs` | documentation only | `docs: document the built game's command-line arguments` |
| `test` | tests only | `test: cover pool-less NVN buffer mappings` |
| `ci` | the workflows | `ci: actions pinned by commit, least privilege, realistic timeouts` |
| `build` | the build scripts, the AppImage, the Builder package | `build: scope the shader cache stamp to its consumer` |
| `chore` | anything else | `chore: remove patch backup files added by mistake` |
| `revert` | undoing an earlier change | `revert: ...`, with the reverted PR in the body |

The scope names the part of the project: `vk`, `linux`, `builder`, `menu`, `video`, `runtime` and so on. `feat` makes
the next release a minor version, `fix` and `perf` a patch (each first as a release candidate), and a `!` after the
type or scope (`feat(builder)!: ...`) marks a breaking change. The other types make no release on their own. Commits
inside a branch can say anything: only the title reaches `main`. The version rules are in
[docs/versioning.md](docs/versioning.md).

Maintainer commits record AI assistance with `Assisted-by:` and `Co-Authored-By:` trailers. If you used a tool, add the
trailers that describe it; if you wrote the change yourself, there is nothing to add.

## Licensing

Hookshot is free software under the [GNU General Public License, version 3 or later](LICENSE). By submitting a change
you license it under those terms, so only send code you can license that way: nothing from a project with an
incompatible license, and no decompiled game code or anything generated from the dump.

The third-party parts (Eden, suyu, Sirit, the Vulkan Memory Allocator, SDL, Opus, Dear ImGui, zstd, the Libertinus
fonts, zbic and the a64-kit tools) keep their own notices: preserve them, and don't relicense their code. NVIDIA's DLSS
SDK is not one of them: it is never in the repository ([NVIDIA DLSS](#nvidia-dlss-optional)).

## Where to talk

- **Discord:** https://discord.gg/KX99srmQTe. Claim work and discuss pull requests in `#dev-work`; `#prs` shows every
  open pull request, kept up to date.
- **Issues:** [the forms on git.magicbean.dev](https://git.notrexed.dev/Hookshot/Hookshot/issues/new/choose).
- **Agents:** [AGENTS.md](AGENTS.md); they post progress to the team chat (`scripts/team_chat.py`).

Thanks for testing, reporting and sending fixes. Every report from a different GPU and every area played further than
before moves the port forward.
