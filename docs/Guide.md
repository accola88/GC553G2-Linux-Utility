# AVerMedia Live Gamer ULTRA 2.1 on Linux

[Deutsch](Anleitung.md) · [Project README](../README.md)

## Requirements and setup

The GC553G2 (`07ca:2553`) uses the Linux `uvcvideo` and `snd-usb-audio` drivers. The CLI requires Python 3.8 or later, libusb 1.0, and `v4l2-ctl` from `v4l-utils`. It finds the USB device through sysfs and resolves its V4L2 capture node automatically. The CLI does not require a specific GPU.

Clone the repository or extract its ZIP archive, then change to the repository root. The examples work in Bash and Fish.

```sh
python3 gc553g2.py devices
python3 gc553g2.py formats
python3 gc553g2.py capture --width 3840 --height 2160 --format MJPEG --fps 120 --test-frames 240
```

If multiple cards are connected, pass the `usb_port` reported by `devices` before the subcommand, for example `python3 gc553g2.py --port 2-1 formats`. Close applications using the card before running a capture test. The test reads and discards frame buffers without decoding or saving them.

`devices`, `formats`, and `capture` report the negotiated USB link speed as `speed_mbps`. The field `usb_10gbps_or_faster` indicates whether the link is at least 10 Gbit/s. The CLI uses the modes currently advertised by V4L2. The 4K120 capture test used 10 Gbit/s. AVerMedia's Windows table lists 4K120 and 4K144 MJPEG at 10 Gbit/s and up to 4K60 MJPEG at 5 Gbit/s. [AVerMedia capture format table](https://www.avermedia.com/de/files/large/5eb4cd6e1570f4d)

## High-frame-rate capture and HDMI presets

Close OBS and other capture applications before running these commands. The utility temporarily unbinds the video driver to access the device controls. `enable` and `disable` can cause the card to disconnect and reappear on USB.

```sh
sudo python3 gc553g2.py status
sudo python3 gc553g2.py hdmi-status
sudo python3 gc553g2.py edid merge
sudo python3 gc553g2.py hdmi-preset
sudo python3 gc553g2.py edid-table
sudo python3 gc553g2.py edid-live
sudo python3 gc553g2.py enable
sudo python3 gc553g2.py disable
```

If `formats` lists only 60 fps for 4K MJPEG, `enable` switches to the extended capture mode and checks that V4L2 advertises 120 and 144 fps. `hdmi-status` reports the actual HDMI input timing and the EDID mode. The HDMI input timing and the USB capture mode can differ.

Preset and EDID changes require firmware **1.0.10.2**. The `edid` command accepts `merge` (modes shared by the card and monitor), `copy` (the monitor's EDID), or `internal` (the card's EDID). A change may briefly interrupt HDMI. The CLI reads the setting back, and switching between Merge and Internal was verified in both directions.

`hdmi-preset` shows the code for the active HDMI compatibility preset. Use `hdmi-preset 2.1-144`, `hdmi-preset 2.1-120`, or `hdmi-preset 2.0` to select a preset. The card restarts and the CLI checks the code after USB re-enumeration. Switching between 2.1-144 and 2.1-120 was verified in both directions. The return to 2.1-144 after a 2.0 test was verified. Readback of the 2.0 code itself remains unverified.

## EDID backup, preview, and restoration

`edid-table` reads the stored EDID and its extension blocks for the active HDMI preset. `edid-backup` reads all seven preset slots, including their reserved areas, and outputs JSON with a SHA-256 checksum. All seven slots on the test device contained two valid EDID blocks.

`edid-live` reads the EDID that the receiver currently presents to the HDMI source. In Merge mode, this differed from the stored preset on the test card. The first advertised CTA mode was already 4K120.

To preview a preferred timing, start with an EDID backup. These commands read the file and output the proposed change as JSON.

```sh
python3 gc553g2.py edid-prefer-preview gc553g2-edid-backup.json --width 2560 --height 1440 --hz 120
python3 gc553g2.py edid-prefer-preview gc553g2-edid-backup.json --vic 118
```

The second command moves the existing 4K120 mode (CTA VIC 118) to the first position in the CTA list. The preferred timing in the EDID base block stays in place.

Turn off the HDMI source before writing to the receiver and supply a new path for the binary backup. `edid-live-test` writes the temporary table, verifies it, and restores the original. `edid-live-set` leaves the temporary table active after verifying it. Use `edid-live-restore` to restore the original from the backup.

```sh
sudo python3 gc553g2.py edid-live-set --backup rx-original.bin --width 2560 --height 1440 --hz 120
sudo python3 gc553g2.py edid-live-restore rx-original.bin
```

Restoration validates the backup before accessing the device. If writing fails, the CLI attempts to restore and verify the previous receiver table. Writing a preferred 1440p120 timing and restoring the original were verified on firmware 1.0.10.2.

`edid-live-set` and `edid-live-test` also accept `--vic 118` in place of width, height, and refresh rate. The CTA change was tested against an EDID read from the device. The source's choice of output mode after applying that change remains unverified. Permanent custom EDID presets and firmware updates are outside the current feature set.

## HDR input and HDR-to-SDR control

`sudo python3 gc553g2.py hdmi-status` includes `hdmi_input.hdr_detected` when the input has locked. The flag comes from the receiver status observed when switching a PS5 between SDR and HDR. Use `hdr-metadata` for the input's transfer function.

Run `sudo python3 gc553g2.py hdr-to-sdr` to read the device flag. Add `on` or `off` to set and verify it on firmware 1.0.10.2. Switching off, on, and back off was verified on the test card.

`sudo python3 gc553g2.py hdr-metadata` reads the HDMI receiver's HDR metadata and reports the input transfer function when the InfoFrame is recognized. The PS5 reported PQ with no mastering values set. With a fixed PQ source image, switching the flag had no measurable effect on captured luma in NV12 or P010. V4L2 reported Rec.709 in both switch positions.

## Automatic VRR

Close capture applications before running `sudo python3 gc553g2.py auto-vrr on` or `sudo python3 gc553g2.py auto-vrr off`. On firmware 1.0.10.2 the command checks the acknowledgement observed in the Windows USB trace. The actual HDMI refresh behavior and any independent settings readback remain unverified.

## Audio, HDCP, and RGB

```sh
sudo python3 gc553g2.py audio
sudo python3 gc553g2.py audio hdmi
sudo python3 gc553g2.py audio hdmi -6
sudo python3 gc553g2.py audio-bitstream
sudo python3 gc553g2.py hdcp-detection
sudo python3 gc553g2.py rgb status
sudo python3 gc553g2.py rgb 00a0ff --brightness 30
sudo python3 gc553g2.py rgb autonomous
```

`audio` reads gain for HDMI, headphones, microphone, and controller. Passing a channel and numeric value sets the gain within the device's supported range and reads it back. `audio-bitstream` reads the non-PCM USB audio setting. `hdcp-detection` reads the card's HDCP detection setting. Both switches accept `on` and `off` for writes. These commands require firmware 1.0.10.2 to change settings.

All four gain settings were read on the test card. Changing and restoring the HDMI gain, non-PCM setting, and HDCP detection were verified. The effect of mixer changes on the audio signal, Dolby Digital 5.1 capture, and analog party chat remain unverified on hardware. AVerMedia lists two-channel LPCM support. [AVerMedia 5.1 audio guide](https://www.avermedia.com/pk/support/faq/can-gc553g2-support-5-1-surround-sound)

`rgb` accesses the LampArray interface through `hidraw` and uses the card's serial number to find the corresponding device node. Solid colors accept `--brightness` from 0 to 100 percent, with 100 percent as the default. `autonomous` returns lighting control to the card. The test card reported ten LEDs. Solid blue and the return to the card's effect were visually verified. Brightness scaling was tested with simulated HID reports.

Run `sudo python3 gc553g2.py rgb-effect rainbow-wave` to request a native preset. Use `rgb-effect --help` for all 15 names. If you previously set a solid color via LampArray, run `rgb autonomous` first to return lighting control to the card. The preset reports use the parameters observed in the Windows trace. Linux LED output and adjustable preset colors, speed and brightness still need verification.

## Verified PS5 capture modes

On CachyOS, the PS5 passed through at 3840×2160 and 119.88 Hz and captured at 3840×2160 and 120 fps in MJPEG. Nioh Remastered also produced roughly 120 fps in MJPEG at 1920×1080 and 2560×1440, with 357 consecutive distinct frames verified at each resolution. P010 capture also worked at 1920×1080 and 120 fps, with V4L2 reporting Rec.709.

The CLI works with standard V4L2 applications, including OBS. OBS recording performance depends on the decoder, GPU, and driver.
