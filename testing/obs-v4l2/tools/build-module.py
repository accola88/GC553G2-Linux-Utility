#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a patched OBS V4L2 module against the installed development libraries."""

import argparse
from pathlib import Path
import shlex
import subprocess

PACKAGES = ("libobs", "libavcodec", "libavutil", "libavformat", "libv4l2", "libudev")
SOURCES = (
    "linux-v4l2.c",
    "v4l2-controls.c",
    "v4l2-decoder.c",
    "v4l2-helpers.c",
    "v4l2-input.c",
    "v4l2-output.c",
    "v4l2-udev.c",
)


def pkg_config(*args):
    return subprocess.check_output(["pkg-config", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("obs_source", type=Path, help="Path to the patched OBS source tree")
    parser.add_argument("--output", type=Path, default=Path("build/linux-v4l2.so"))
    parser.add_argument("--simde", type=Path, help="Include directory containing simde/x86/sse2.h")
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()

    obs_version = pkg_config("--modversion", "libobs")
    if obs_version != "33.0.1":
        parser.error(f"this build targets OBS 33.0.1; installed libobs reports {obs_version}")

    plugin = args.obs_source / "plugins/linux-v4l2"
    for source in SOURCES:
        if not (plugin / source).is_file():
            parser.error(f"missing OBS source file: {plugin / source}")
    if args.simde and not (args.simde / "simde/x86/sse2.h").is_file():
        parser.error("--simde must point to the directory containing the simde/ headers")

    includedir = Path(pkg_config("--variable=includedir", "libobs"))
    flags = shlex.split(pkg_config("--cflags", "--libs", *PACKAGES))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [args.compiler, "-shared", "-fPIC", "-O2", "-g", "-DHAVE_UDEV", f"-I{includedir / 'obs'}"]
    if args.simde:
        command.append(f"-I{args.simde}")
    command += ["-o", str(args.output), *[str(plugin / name) for name in SOURCES], *flags, "-pthread"]
    subprocess.run(command, check=True)
    print(f"Built {args.output}")


if __name__ == "__main__":
    main()
