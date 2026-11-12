# Publish the benchmark lab

The GitHub connector may be read-only even if your GitHub user has admin permissions. Use the commands below from a local machine with Git configured and authenticated as `xinjiechen727`. Both destinations were empty when inspected on 2026-11-08.

## Windows PowerShell

Extract the ZIP, then open PowerShell in the extracted `rkpi_benchmark_lab_case` folder and run:

```powershell
# Ensure Git is installed and authenticated before continuing.
git init -b main
git add .
git commit -m "Add reproducible Raspberry Pi 5 vs RK3588 4K H.264 benchmark"

git remote add personal https://github.com/xinjiechen727/raspberrypi-rockchip_benchmark_lab.git
git remote add seeed https://github.com/Seeed-Projects/raspberrypi-rockchip_benchmark_lab.git

git push -u personal main
git push seeed main
```

If the folder is already a Git repository, skip `git init` and check `git remote -v` before adding remotes. Never run `git push --force`. If either destination gained commits since this guide was created, integrate those commits rather than overwriting them. You may be prompted to sign into GitHub; organizational authorization/SSO may be needed for the Seeed remote.

## Linux / macOS

The same commands work in a shell; `git init -b main` requires a recent Git version.

## Check success

Confirm `README.md`, `scripts/`, `decode/`, `results/`, and `website/` appear at the destination. `website/index.html` presents H.264 encoding, `website/hevc-decode.html` presents HEVC decoding, and `website/legacy-results.html` preserves the initial simpler encoding prototype.

The ZIP excludes large videos, original source media, benchmark outputs, tokens, and local environment files.
