# GC553G2 Linux Utility

Command-line utility for the **AVerMedia Live Gamer ULTRA 2.1 (GC553G2)** on Linux. Discover capture modes, enable high-frame-rate MJPEG, inspect HDMI and EDID, and control device audio and RGB. The utility works with the Linux `uvcvideo` and `snd-usb-audio` drivers and prints JSON that can be used in scripts.

[English guide](docs/Guide.md) · [Deutsche Anleitung](docs/Anleitung.md)

## Requirements

- AVerMedia GC553G2 with USB vendor and product ID `07ca:2553`
- Linux, Python 3.8 or later, libusb 1.0, and `v4l2-ctl` from `v4l-utils`
- Access to the V4L2 device. Most device-specific commands require root. RGB requires access to the corresponding `hidraw` device.
- A 10 Gbit/s USB connection for the tested 4K120 capture mode

Device-specific writes are limited to firmware **1.0.10.2**. The RGB command identifies the standard LampArray interface by the card's serial number and a known HID descriptor.

## Quick start

Clone the repository or download its ZIP archive. Run the commands from the repository root.

```sh
python3 gc553g2.py devices
python3 gc553g2.py formats
python3 gc553g2.py capture --width 2560 --height 1440 --format MJPEG --fps 120 --test-frames 240
```

`formats` lists the modes the card currently advertises and reports the negotiated USB speed. When several GC553G2 cards are connected, put `--port` before the command, for example `python3 gc553g2.py --port 2-1 formats`. The optional capture test reads and discards V4L2 buffers.

If 4K MJPEG is listed at 60 fps only, close OBS and other applications using the card, then enable the extended mode.

```sh
sudo python3 gc553g2.py enable
python3 gc553g2.py formats
```

`enable` can cause the device to disconnect and reappear. It verifies that 120 and 144 fps are advertised for 4K MJPEG. The actual frame rate also depends on the HDMI source. AVerMedia lists 4K120 and 4K144 MJPEG for 10 Gbit/s and up to 4K60 MJPEG for 5 Gbit/s. [AVerMedia capture format table](https://www.avermedia.com/de/files/large/5eb4cd6e1570f4d)

## Device controls

Close capture applications before commands that access the device's USB controls. These commands can temporarily unbind the video driver.

| Command | Purpose |
|---|---|
| `status`, `enable`, `disable` | Inspect or switch the USB capture mode |
| `hdmi-status`, `hdmi-preset` | Read HDMI input timing and inspect or select a compatibility preset |
| `edid`, `edid-table`, `edid-backup` | Select Merge, Copy, or Internal EDID mode and read or back up stored tables |
| `edid-live`, `edid-live-set`, `edid-live-restore` | Inspect, temporarily change, or restore the HDMI receiver's EDID |
| `hdr-metadata`, `hdr-to-sdr` | Read HDMI HDR metadata and inspect or set the card's HDR-to-SDR flag |
| `audio`, `audio-bitstream` | Read or adjust mixer gains and inspect or set non-PCM audio over USB |
| `hdcp-detection` | Inspect or set the device's HDCP detection option |
| `rgb` | Read the LampArray, set a solid color, or restore the device lighting effect |

```sh
sudo python3 gc553g2.py hdmi-status
sudo python3 gc553g2.py edid-backup > gc553g2-edid-backup.json
sudo python3 gc553g2.py rgb 0000ff --brightness 30
sudo python3 gc553g2.py rgb autonomous
```

Run `python3 gc553g2.py --help` or append `--help` to a command for its arguments. The [full guide](docs/Guide.md#edid-backup-preview-and-restoration) includes temporary EDID and restore examples. The HDMI source must be off before writing the receiver EDID.

## Validation

| Feature | Result |
|---|---|
| PS5 capture on CachyOS | 1080p120, 1440p120, and 4K120 MJPEG verified. 4K120 used a 10 Gbit/s USB link. |
| PS5 HDMI passthrough | 4K120 verified |
| 4K144 MJPEG | Advertised by V4L2 when enabled. Capture from a 4K144 HDMI source remains unverified. |
| EDID | Merge and Internal switching, temporary receiver EDID write, and byte-for-byte restoration verified |
| Audio and HDCP | Stereo USB capture, HDMI gain write and readback, non-PCM switch, and HDCP detection switch verified. Hardware Dolby Digital 5.1 and analog party chat remain unverified. |
| HDR | PQ input detected. The HDR-to-SDR flag switched successfully, but had no measurable effect on captured luma in the tested NV12 and P010 modes. |
| RGB | Solid blue and the device lighting effect verified. Brightness scaling tested with simulated HID reports. |

The utility supports temporary receiver EDID changes. Permanent custom EDID profiles and firmware updates are outside the current feature set.

## OBS testing

The [OBS Linux V4L2 test bundle](testing/obs-v4l2/README.md) includes the frame-rate fix, MJPEG recovery, and experimental hardware decoding patches for OBS Studio 33.0.1. It includes build steps and a smoke test. The patches work with the V4L2 input module and are maintained as an independent patch series.

## Author

Accola

## License

The GC553G2 utility and its documentation are licensed under [MIT](LICENSE). The OBS test bundle is licensed under [GPL-2.0-or-later](testing/obs-v4l2/COPYING).
