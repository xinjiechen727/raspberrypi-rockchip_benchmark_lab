# Raspberry Pi 5 vs RK3588 — Reproducible Video Benchmark Lab

A reproducible benchmark lab accompanying Seeed Studio videos comparing **Raspberry Pi 5 (16 GB)** with **Seeed reComputer RK3588 Devkit (16 GB)**. It now contains two independent cases:

- **4K H.264 encoding:** FFmpeg/libx264 on both boards and dedicated Rockchip MPP H.264 encoding on RK3588.
- **4K60 HEVC decoding:** Raspberry Pi stateless V4L2 hardware decode versus Rockchip MPP, including unpaced throughput and paced multi-stream tests.

- **[Open the H.264 encoding dashboard](website/index.html)**
- **[Open the 4K60 HEVC decoding dashboard](website/hevc-decode.html)**

Both pages are self-contained and can be downloaded and opened directly in a desktop browser.

> **Results are observed measurements, not vendor specifications.** The source and raw input were byte-identical across boards. Tests use a fixed 10-second 4K animated clip, RAM-backed input, three repeats, and elapsed-time medians. Different encoders need not produce the same quality at a target bitrate.

## HEVC decoding headline results

The decode case uses the same verified 5-second HEVC Main 3840×2160@60 source on both boards and measures delivery to a GStreamer `appsink`.

| Metric | Raspberry Pi 5 | RK3588 |
|---|---:|---:|
| Unpaced single-stream throughput, three-run median | 164.90 fps | **455.65 fps** |
| 2×4K60 for 60 seconds | PASS, zero late frames | PASS, zero late frames |
| 4×4K60 challenge | Real-time FAIL at 10 seconds | **PASS at 60 seconds** |

See [`decode/README.md`](decode/README.md) for code, final single-run commands, requirements and limitations. The detailed interactive report is [`website/hevc-decode.html`](website/hevc-decode.html).

The unpaced fps/60 values are rate equivalents, not validated concurrency counts. The 4-stream challenge durations differ intentionally. No 30-minute stability, HDMI timing, pixel-level correctness, power or thermal result is claimed.

## H.264 encoding headline results

## Headline results

| Metric | Raspberry Pi 5: libx264 | RK3588: libx264 | RK3588: MPP H.264 |
|---|---:|---:|---:|
| Median wall time for 300 frames | 24.64 s | 20.04 s | **10.37 s** |
| Throughput, 300 / median wall time | 12.18 fps | 14.97 fps | **28.93 fps** |
| Median process CPU usage, GNU time | 327% | 357% | **54%** |
| SSIM All vs decoded raw source | 0.995376 | 0.995378 | 0.991389 |
| Video bitrate, representative encode | ~10.2 Mbps | ~10.2 Mbps | ~9.70 Mbps |

**RK3588 MPP yields 2.38× the throughput of Pi 5 libx264 in this specific setup.** RK3588 libx264 yields ~23% higher throughput than Pi 5 libx264. MPP is near — but **not reliably at** — 4K30 real-time throughput: two out of three end-to-end trials exceeded 10 seconds for 300 frames.

**CPU % definition:** `GNU time`'s “Percent of CPU this job got” is *process CPU utilization* where 100% approximately equals one logical CPU fully utilized. It is **not system-wide CPU usage or wall-power measurement**. Software and MPP throughput use different implementations and rate-control behavior; bitrate and SSIM are not controlled to strict equal-bitrate equivalence.

## Source integrity

The sample is the [Blender Foundation Big Buck Bunny Sunflower 2160p30](https://download.blender.org/demo/movies/BBB/bbb_sunflower_2160p_30fps_normal.mp4.zip) ZIP, **not** a raw `.mp4` download. It contains a Creative Commons Attribution 3.0 animated film; respect the original author/asset attribution in derived videos.

- Unzipped source MP4 SHA-256: `37f0ff251a606c2dcfa26c19fe6bf843234b4e7a8889cfab50bc26f644e55520`
- NV12 input (4K, 30 fps, 300 frames, 3,732,480,000 bytes) SHA-256: `d1e3403d6886a5472c7261dfe07a4149b941d1d7bdfa92d46f181dff381f5fd7`
- Extract starts at **00:01:00** (10 seconds) using FFmpeg. The raw-file SHA-256 was **identical on both test devices**, despite different installed FFmpeg versions. This hash is important: stop if it does not match rather than silently comparing different inputs.
- Synthetic `testsrc2` measurements were used only for early bring-up; the headline table above uses the Big Buck Bunny clip.

## Device environments measured

| Device | OS / FFmpeg | Encoding setup |
|---|---|---|
| Raspberry Pi 5, 16 GB | Debian 13 Trixie, FFmpeg 7.1.5 | libx264 software, 4 encoding threads |
| Seeed reComputer RK3588 Devkit, 16 GB | Armbian 26.8.7 (Ubuntu 26.04), vendor kernel 6.1.172, Rockchip MPP 1.5.0-1 plugin | libx264 or GStreamer `mpph264enc` |

The Pi 5 has no built-in H.264 hardware encoder; **MPP is Rockchip-specific** and is not a Raspberry Pi encoder. This encoding case does not benchmark AV1, camera pipeline power, video capture, or whole-device power. HEVC decoding is covered separately under `decode/`.

## Reproduce

**Dependencies:** Ubuntu/Debian-like Linux, `bash`, `wget`, `unzip`, `sha256sum`, `ffmpeg`, `ffprobe`, `/usr/bin/time`; MPP run additionally needs the Seeed/Rockchip GStreamer `mpph264enc` plugin, `gst-launch-1.0`, and functional MPP device/driver. You may need `sudo` on RK3588 because `/dev/mpp_service` is not accessible to unprivileged users in the test image.

**Capacity:** the raw NV12 file is **3.73 GB decimal (3.48 GiB)**, plus a RAM copy of the same size; 16 GB RAM strongly recommended and at least ~4 GB free in `/dev/shm`. Do not commit raw NV12 or downloaded MP4/ZIP into Git.

Run the following *separately on both devices* from this case folder. The `input/`, `output/`, and `logs/` folders are generated locally and gitignored.

```bash
# 0. Both boards: download, extract 10-second clip, verify hash and copy into RAM
bash scripts/00_prepare_input.sh

# 1. CPU software encoding, three runs per board
bash scripts/01_cpu_encode.sh pi5      # Raspberry Pi 5
bash scripts/01_cpu_encode.sh rk3588   # RK3588, run separately

# 2. RK3588 only: MPP hardware encoding, three runs
bash scripts/02_rk3588_mpp.sh

# 3. Compare outputs against uncompressed source (run on corresponding board)
bash scripts/03_measure_quality.sh output/cpu_pi5_run2.mp4
bash scripts/03_measure_quality.sh output/cpu_rk3588_run2.mp4
bash scripts/03_measure_quality.sh output/mpp_rk3588_run2.mp4
```

All codec commands intentionally operate on `/dev/shm/rkpi_real_4k_10s.nv12` so storage I/O does not dominate the timed stage. They use **H.264 10 Mbps target**, **GOP 60**, **300 input frames**, and **30 fps**. The libx264 process uses `-preset veryfast -threads 4 -maxrate 10M -bufsize 20M`; MPP uses `rc-mode=cbr bps=10000000 gop=60`. These are not algorithmically identical encoder configurations.

> **Never treat an FFmpeg source extraction run as an encoding benchmark.** The benchmark begins with the already-generated NV12 file. Recording a screen or using `htop` simultaneously can alter the measured timing. The supplied CSVs are the original no-recording results.

## Repository files

| Path | Role |
|---|---|
| `scripts/00_prepare_input.sh` | Download, validate input SHA-256, generate raw NV12, copy to RAM |
| `scripts/01_cpu_encode.sh` | Repeat CPU software encoding three times on the selected device |
| `scripts/02_rk3588_mpp.sh` | Repeat RK3588 hardware encode three times; check 300 output frames |
| `scripts/03_measure_quality.sh` | SSIM to uncompressed source, compare actual bitrate and frame count |
| `results/runs.csv` | Original per-run wall times and process CPU percentages |
| `results/summary.csv` | Published benchmark medians, fps and SSIM values |
| `website/index.html` | Self-contained offline bilingual HTML result presentation |
| `decode/` | Final HEVC hardware-decode benchmark and demo code |
| `decode/README.md` | Decode reproduction guide, commands, methodology and limitations |
| `website/hevc-decode.html` | Self-contained 4K60 HEVC decode result presentation |

## Limitations and attribution

- **Single 10-second animation clip**, not necessarily representative of camera surveillance, streaming, or natural footage.
- **Three runs** per mode; reported medians reflect this environment only. CPU frequencies, thermal behavior, storage, firmware and kernel matter.
- `SSIM` refers to the same uncompressed source on each device, not an independent human visual assessment. The MPP bitrate was ~9.70 Mbps versus ~10.2 Mbps for libx264; **do not present this as strictly equal-bitrate quality**.
- MPP command was run with `sudo` on the test RK3588 image. Nonfatal `client 12 driver is not ready` warnings may appear on plugin inspection; check frame counts and exit codes rather than assuming success or failure from a warning alone.
- The dashboard race animation replays the **measured median timings**; it is **not live encoding**.
- Big Buck Bunny and its content are **not included** in this repository. Attribution: *Big Buck Bunny*, Blender Foundation (2008), Sunflower 4K re-release, [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). This source license does not imply that the authors of the benchmark own the film.
- Script code is offered under the root repository's license if incorporated into that repository; the linked Blender footage retains its own license.
