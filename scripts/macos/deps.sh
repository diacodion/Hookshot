#!/bin/bash
# (macos) What a source build on macOS needs beside the clone: Homebrew's tools and libraries, and the Vulkan back end's
# headers at the versions scripts/linux/deps.sh pins, in the CPM cache layout nx2/build.py reads (RX_CPM, or ./cpm).
#
#   scripts/macos/deps.sh [<deps folder>]      (default: ../deps beside the clone)
#
# then build with  RX_CPM=<deps folder>/cpm  (scripts/macos/env.sh sets it).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DEPS=${1:-$HERE/../../../deps}
mkdir -p "$DEPS" && DEPS=$(cd "$DEPS" && pwd)

VK_HEADERS=v1.4.345
SPIRV_HEADERS=vulkan-sdk-1.4.341.0
VMA=v3.3.0

if command -v brew >/dev/null; then
    # molten-vk + vulkan-loader: Vulkan on Metal. sdl2-compat: the window, sound and controllers. ffmpeg + dav1d: the
    # game's movies. glslang + spirv-tools: the host shaders' checks. pkgconf: nx2/build.py asks it for FFmpeg's flags.
    brew install molten-vk vulkan-loader vulkan-tools vulkan-validationlayers sdl2-compat ffmpeg dav1d glslang spirv-tools pkgconf
else
    echo "Homebrew is not installed (https://brew.sh): install molten-vk, vulkan-loader, sdl2, ffmpeg yourself" >&2
fi

get_headers() {   # repo tag name
    local dst="$DEPS/cpm/$3/$2"
    [ -d "$dst/include" ] && return 0
    mkdir -p "$dst"
    curl -fL --retry 5 --proto '=https' "https://github.com/$1/archive/refs/tags/$2.tar.gz" | tar -xz -C "$dst" --strip-components=1
}
get_headers KhronosGroup/Vulkan-Headers "$VK_HEADERS" vulkanheaders
get_headers KhronosGroup/SPIRV-Headers "$SPIRV_HEADERS" spirv-headers
get_headers GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator "$VMA" vulkanmemoryallocator
echo "deps: $DEPS (RX_CPM=$DEPS/cpm)"
