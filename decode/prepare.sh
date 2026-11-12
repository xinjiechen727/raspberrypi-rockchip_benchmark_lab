#!/usr/bin/env bash
set -euo pipefail
[[ $# == 3 ]] || { echo 'Usage: bash prepare.sh input-video 4K60|4K30|1080p30 output.mp4'; exit 2; }
case "$2" in
  4K60) SIZE=3840:2160; FPS=60;;
  4K30) SIZE=3840:2160; FPS=30;;
  1080p30) SIZE=1920:1080; FPS=30;;
  *) exit 2;;
esac
# Run OFFLINE on a workstation. Never encode while benchmarking.
# No overwrite; 5 seconds limits the compressed memory cache. Closed GOP, no B frames.
ffmpeg -nostdin -n -i "$1" -t 5 -an -vf "scale=$SIZE,fps=$FPS,setpts=PTS-STARTPTS" \
  -c:v libx265 -preset medium -pix_fmt yuv420p \
  -x265-params "bframes=0:open-gop=0:keyint=$FPS:min-keyint=$FPS:scenecut=0:repeat-headers=1" \
  -tag:v hvc1 -movflags +faststart "$3"
