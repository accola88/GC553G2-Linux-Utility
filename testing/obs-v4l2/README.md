# OBS Linux V4L2 Patches

An experimental patch series for the Linux V4L2 input module in **OBS Studio 33.0.1**. It fixes the selected capture frame interval, keeps MJPEG capture running after damaged frames, and adds optional hardware decoding.

The patches apply to the standard V4L2 source. They can be tested with any camera or capture card that exposes a suitable MJPEG mode.

## Patch series

| Patch | Change |
|---|---|
| [01 · Frame interval](patches/01-frame-interval.patch) | Keeps the packed frame interval at 64 bits so OBS can request the selected discrete frame rate. |
| [02 · MJPEG recovery](patches/02-mjpeg-recovery.patch) | Skips empty or damaged MJPEG frames and lets the source continue. |
| [03 · Hardware decoders](patches/03-hardware-decoders.patch) | Adds automatic, software, NVIDIA CUDA/NVDEC, VA-API, and Intel Quick Sync decoder choices. Hardware initialization failures and repeated decode errors fall back to software. |

Apply the first two patches for the CPU-only variant. Apply all three for the experimental decoder selector.

## Build

The base is OBS Studio tag `33.0.1` at commit `65c320b46e1af5c7a555161aea6182cdf7a8c524`. Start in the directory containing this README. The following commands apply all three patches to a fresh checkout.

```bash
patch_root=$(pwd)
work_dir=$(mktemp -d)
obs_source="$work_dir/obs-studio"

git clone --depth 1 --branch 33.0.1 https://github.com/obsproject/obs-studio.git "$obs_source"
for patch in "$patch_root"/patches/*.patch; do
  git -C "$obs_source" apply --check "$patch" || exit 1
  git -C "$obs_source" apply "$patch" || exit 1
done
```

For a complete OBS build, follow the upstream build instructions using the patched checkout. [OBS build instructions](https://github.com/obsproject/obs-studio/wiki/Building-OBS-Studio)

To build just the module for a test, install Python 3, a C compiler, `pkg-config`, the development headers for OBS 33.0.1, FFmpeg, libv4l2 and libudev, and the SIMDe headers. The standalone build was tested with the CachyOS packages for OBS 33.0.1 and FFmpeg 9.0.2.

```bash
python3 "$patch_root/tools/build-module.py" "$obs_source"
```

The helper writes `build/linux-v4l2.so`. If the SIMDe headers are outside the compiler's default search path, pass `--simde /path/to/include`, where the include directory contains `simde/x86/sse2.h`. Use matching OBS and FFmpeg libraries on the system that loads the module. The repository provides source patches and build instructions.

## Run the decoder smoke test

The test builds against the patched source and uses a synthetic JPEG. It checks software decoding, recovery after damaged or empty MJPEG packets, a simulated transition from hardware decoding to software, and the saved 64-bit frame interval. It does not access a capture card.

```bash
plugin="$obs_source/plugins/linux-v4l2"
obs_include=$(pkg-config --variable=includedir libobs)
ffmpeg -nostdin -v error -f lavfi -i testsrc2=size=128x96:rate=1 \
  -frames:v 1 -c:v mjpeg -threads 1 -update 1 "$work_dir/frame.jpg"

cc -I"$obs_include/obs" -I"$plugin" \
  $(pkg-config --cflags libobs libavcodec libavutil libavformat libv4l2) \
  "$patch_root/tests/mjpeg-decoder.c" "$plugin/v4l2-decoder.c" "$plugin/v4l2-helpers.c" \
  $(pkg-config --libs libobs libavcodec libavutil libavformat libv4l2) \
  -o "$work_dir/mjpeg-decoder-test"

"$work_dir/mjpeg-decoder-test" "$work_dir/frame.jpg"
```

If SIMDe is outside the default include path, add the same include directory with `-I/path/to/include` to the `cc` command.

## Capture-card testing

- The CPU-only patches were tested with a GC553G2 on CachyOS.
- The complete series applies to clean OBS 33.0.1, builds on the tested CachyOS setup, and passes the standalone smoke test.
- The NVIDIA decode path was benchmarked in FFmpeg. GPU decoding in a live OBS capture, VA-API and Intel Quick Sync hardware validation, and end-to-end 4K144 capture are open.

For a live OBS test, select **Video Capture Device (V4L2)**, choose MJPEG, and set the resolution and frame rate. With the third patch applied, choose **MJPEG decoder** and record a short scene with movement. Check the OBS log for the negotiated frame rate and decoder, and check the recording for repeated frames.

For the GC553G2, the capture card's extended USB mode exposes 4K120/144 in V4L2. The card utility configures that mode, and these patches let OBS request and decode the modes offered by V4L2. Capture at 4K120 has been verified with a 10 Gbit/s USB link. [GC553G2 Linux Utility](https://github.com/accola88/GC553G2-Linux-Utility)

## License and attribution

The patch series, build helper, and smoke test are distributed under GPL-2.0-or-later. The patched OBS files retain their upstream copyright and license notices. [GPL license text](COPYING) · [OBS Studio source](https://github.com/obsproject/obs-studio/tree/33.0.1)
