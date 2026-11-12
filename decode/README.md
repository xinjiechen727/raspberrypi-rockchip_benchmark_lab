# Reproducible 4K60 HEVC Hardware Decode Benchmark

This directory contains the final on-device code used to compare the Raspberry Pi 5 `v4l2slh265dec` path with the RK3588 `mppvideodec` path. The standalone bilingual result page is [`../website/hevc-decode.html`](../website/hevc-decode.html).

The benchmark measures decoded frames delivered to a GStreamer `appsink`. It does **not** claim HDMI presentation timing, pixel-perfect visual correctness, power consumption, or a validated 30-minute stability result.

## Published observations

All results use the same HEVC Main, 3840×2160, 60 fps, 8-bit 4:2:0 source. The verified test file SHA-256 was:

```text
743afe788b81814550f9ac2677906aecc03a5b9c8572e68996d572ff3f9de864
```

| Test | Raspberry Pi 5 | RK3588 |
|---|---:|---:|
| Unpaced single-stream throughput, three-run median | 164.90 fps | 455.65 fps |
| 2×4K60, 60 s | PASS, zero late frames | PASS, zero late frames |
| 4×4K60 challenge | FAIL real-time deadline at 10 s | PASS at 60 s, 14,400/14,400 on time |

Unpaced fps divided by 60 is a rate equivalent, not proof of that many simultaneous real-time streams. The 4-stream challenge uses intentionally different durations and is not a same-duration ranking.

## Files

| File | Purpose |
|---|---|
| `bench.py` | Strict paced multi-stream benchmark, per-frame accounting and CSV/JSON reports |
| `run.sh` | Per-user mutual exclusion and benchmark launcher |
| `demo_throughput.py` | Unpaced single-stream hardware-decode throughput |
| `demo_parallel.py` | 2- or 4-stream paced 4K60 test wrapper |
| `demo_hdmi.py` | Optional real-time Wayland or DRM/KMS playback demo |
| `prepare.sh` | Offline creation of a 5-second, closed-GOP, no-B-frame HEVC source |

## Requirements

- Linux, Python 3, Python GI bindings and GStreamer 1.0
- `ffprobe`, `dmesg`, `findmnt`, `flock`
- Raspberry Pi 5: working `v4l2slh265dec`
- RK3588: working Rockchip MPP and `mppvideodec`
- The user must be able to read the kernel log and access the video/render devices

Check the decoder before testing:

```bash
gst-inspect-1.0 v4l2slh265dec  # Pi 5
gst-inspect-1.0 mppvideodec    # RK3588
```

## Prepare the source

Source media is intentionally not committed. Create the short benchmark source offline, not during a measured run:

```bash
bash prepare.sh original-4k60.mp4 4K60 source-4k60.mp4
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,nb_frames -of default=nw=1 source-4k60.mp4
sha256sum source-4k60.mp4
```

For an exact reproduction of the published measurements, use a byte-identical file matching the published SHA-256. A newly encoded file is a new workload and its hash and encoder settings must be reported.

## Final single-run commands

Run from this directory. Output paths include a timestamp so repeat invocations do not collide.

### Unpaced throughput

```bash
# Pi 5
python3 demo_throughput.py --source ./source-4k60.mp4 --decoder v4l2slh265dec --frames 3600 --repeats 1 --json-out throughput-pi5.json

# RK3588
python3 demo_throughput.py --source ./source-4k60.mp4 --decoder mppvideodec --frames 3600 --repeats 1 --json-out throughput-rk3588.json
```

### Same-load 2×4K60 comparison

```bash
# Pi 5
python3 demo_parallel.py --source ./source-4k60.mp4 --decoder v4l2slh265dec --streams 2 --seconds 60 --repeats 1 --out /dev/shm/pi5-2x4k60-$(date +%s%N)

# RK3588
python3 demo_parallel.py --source ./source-4k60.mp4 --decoder mppvideodec --streams 2 --seconds 60 --repeats 1 --out /dev/shm/rk3588-2x4k60-$(date +%s%N)
```

### 4×4K60 load boundary

```bash
# Pi 5: intentionally short overload demonstration
python3 demo_parallel.py --source ./source-4k60.mp4 --decoder v4l2slh265dec --streams 4 --seconds 10 --repeats 1 --out /dev/shm/pi5-4x4k60-overload-$(date +%s%N)

# RK3588
python3 demo_parallel.py --source ./source-4k60.mp4 --decoder mppvideodec --streams 4 --seconds 60 --repeats 1 --out /dev/shm/rk3588-4x4k60-$(date +%s%N)
```

### Optional HDMI playback

Connect a display first. The current Pi 5 stateless HEVC DMABuf path is not compatible with this demo's direct `kmssink` pipeline, so Pi uses Wayland. The latest script can discover a local Wayland socket even when invoked over SSH. RK3588 may use direct KMS. Use `--sink fake` only for headless decode-path validation; it does not prove HDMI output.

```bash
# Pi 5
python3 demo_hdmi.py --source ./source-4k60.mp4 --decoder v4l2slh265dec --sink wayland --loop

# RK3588
python3 demo_hdmi.py --source ./source-4k60.mp4 --decoder mppvideodec --sink kms --loop
```

Stop playback with `Ctrl+C`. The script refuses RK KMS playback when no connected DRM display is detected and refuses Pi direct KMS with a corrective message.

## Result interpretation and known warnings

`bench.py` requires normal EOS, exact planned/input/decoded/delivered identity, no late deliveries, no input-release violations and no timestamp/duplicate errors. The 100 ms deadline is measured from each scheduled frame due time to `appsink` receipt.

The tested RK3588 MPP build emitted recurring HEVC SEI parse warnings and sometimes `client 12 driver is not ready!`; the published runs still delivered every expected frame. These messages must not be silently suppressed, but neither should they alone be treated as proof of a dropped frame. Pixel-level validation remains future work.

The benchmark checks the complete kernel log before and after the measured interval. It deliberately avoids scanning the full ring every second because that operation perturbed the Python feeder on the RK3588. A stopped process can still own the lock; the updated `run.sh` reports the lock-owning PID and command.

