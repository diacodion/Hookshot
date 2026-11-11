# (macos) source this: the environment a source build on macOS runs in (scripts/macos/deps.sh made the folders).
#   . scripts/macos/env.sh
_hs_root=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)
export RX_CPM="${RX_CPM:-$_hs_root/../deps/cpm}"
export OOT_WORK="${OOT_WORK:-$_hs_root/../work}"
[ -x "$_hs_root/../.venv/bin/python" ] && export HOOKSHOT_PYTHON="$_hs_root/../.venv/bin/python"
unset _hs_root
