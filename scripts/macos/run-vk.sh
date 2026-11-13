#!/bin/bash
# (macos) A test run of the standalone build, as scripts/run-vk.ps1 is on Windows: the Vulkan back end on MoltenVK,
# headless unless --window, muted unless --sound, a screenshot every --shot-every presents into
# <work>/runs/<name>/shots, the log in <work>/runs/<name>/log.txt. Extra environment: NAME=VALUE arguments.
#
#   scripts/macos/run-vk.sh [--seconds 120] [--name vk] [--shot-every 300] [--window] [--sound] [--max-gb 14] [A=1 ...]
#
# The game is stopped after --seconds, or when its memory footprint passes --max-gb. Delete the shots once looked at.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/env.sh"
SECONDS_MAX=120 NAME=vk SHOT_EVERY=300 WINDOW=0 SOUND=0 MAX_GB=14
EXTRA=()
while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) SECONDS_MAX=$2; shift 2 ;;
        --name) NAME=$2; shift 2 ;;
        --shot-every) SHOT_EVERY=$2; shift 2 ;;
        --max-gb) MAX_GB=$2; shift 2 ;;
        --window) WINDOW=1; shift ;;
        --sound) SOUND=1; shift ;;
        *=*) EXTRA+=("$1"); shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
if pgrep -x hookshot >/dev/null; then echo "another game run is going: one at a time"; exit 2; fi
WORK=$(cd "$OOT_WORK" && pwd)
ROMFS=${OOT_ROMFS:-$(grep -v '^#' "$WORK/dump.txt" | head -1)/romfs}
EXE=$WORK/sa/build/hookshot IMAGE=$WORK/sa/image/image.bin USERDIR=$WORK/sa/user OUT=$WORK/runs/$NAME
mkdir -p "$OUT/shots" "$USERDIR"
export RX_BACKEND=vulkan RX_VK_SHOTS=$OUT/shots RX_VK_SHOT_EVERY=$SHOT_EVERY
[ "$WINDOW" = 1 ] && unset HOS_HEADLESS || export HOS_HEADLESS=1
[ "$SOUND" = 1 ] && unset HOS_AUDIO || export HOS_AUDIO=0
for kv in ${EXTRA[@]+"${EXTRA[@]}"}; do export "$kv"; done
cd "$OUT" || exit 2
"$EXE" "$IMAGE" --romfs "$ROMFS" --user "$USERDIR" >stdout.txt 2>log.txt &
PID=$!
T0=$(date +%s) PEAK=0 EXITED=0
while :; do
    if ! kill -0 $PID 2>/dev/null; then EXITED=1; break; fi
    [ $(( $(date +%s) - T0 )) -ge "$SECONDS_MAX" ] && break
    MB=$(( $(ps -o rss= -p $PID 2>/dev/null | tr -d ' ' || echo 0) / 1024 ))
    [ "${MB:-0}" -gt "$PEAK" ] && PEAK=$MB
    if [ "${MB:-0}" -gt $(( MAX_GB * 1024 )) ]; then echo "memory guard: $MB MB, stopped"; break; fi
    sleep 1
done
if [ $EXITED = 1 ]; then wait $PID; CODE=$?; else kill -9 $PID 2>/dev/null; wait $PID 2>/dev/null; fi
echo "peak resident memory: $PEAK MB"
echo "run '$NAME': $(( $(date +%s) - T0 )) s, $([ $EXITED = 1 ] && echo "exited with $CODE" || echo "stopped after $SECONDS_MAX s")"
echo "log: $OUT/log.txt  shots: $OUT/shots"
