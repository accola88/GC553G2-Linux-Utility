#!/usr/bin/env python3
"""Manage GC553G2 capture modes and report HDMI input status as JSON."""

import argparse
from contextlib import contextmanager
import ctypes as C
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import time
from gc553g2_support.gc553g2_rgb import NATIVE_EFFECTS, lamp_array, set_native_effect

spec = importlib.util.spec_from_file_location(
    "gc553g2_protocol", Path(__file__).with_name("gc553g2_support") / "avermedia-4k144.py"
)
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)
base = protocol.base
at = protocol.at
EDID_NAMES = {code: name for name, code in at.EDID_MODES.items()}
HDMI_PRESETS = {"2.1-144": 0, "2.1-120": 1, "2.0": 2}
HDMI_PRESET_NAMES = {code + offset: name for name, code in HDMI_PRESETS.items() for offset in (0, 4)}
HDMI_PRESET_NAMES[3] = "1.4"
SUPPORTED_WRITE_FIRMWARE = "1.0.10.2"
SYS_USB = Path("/sys/bus/usb/devices")
SYS_VIDEO = Path("/sys/class/video4linux")
NO_DEVICE = -4


def read_text(device, name):
    return (device / name).read_text().strip()


def find_devices(port):
    matches = []
    for device in SYS_USB.iterdir():
        if port and device.name != port:
            continue
        try:
            if (read_text(device, "idVendor"), read_text(device, "idProduct")) == ("07ca", "2553"):
                matches.append(device)
        except FileNotFoundError:
            continue
    return sorted(matches)


def find_device(port):
    devices = find_devices(port)
    if len(devices) != 1:
        raise RuntimeError("Expected one GC553G2. Run devices and select the USB port with --port.")
    return devices[0]


def identity(device):
    speed_mbps = float(read_text(device, "speed"))
    return {
        "usb_port": device.name,
        "bus": int(read_text(device, "busnum")),
        "address": int(read_text(device, "devnum")),
        "speed_mbps": speed_mbps,
        "usb_10gbps_or_faster": speed_mbps >= 10_000,
    }


def capture_node(device):
    for node in sorted(SYS_VIDEO.glob("video*")):
        path = Path("/dev") / node.name
        try:
            if device.resolve() in node.resolve().parents and read_text(node, "index") == "0" and path.exists():
                return path
        except FileNotFoundError:
            continue
    raise RuntimeError("The GC553G2 V4L2 capture node is not available")


def parse_modes(output):
    modes = []
    pixel_format = None
    mode = None
    for line in output.splitlines():
        if match := re.match(r"\s*\[\d+\]: '([^']+)'", line):
            pixel_format = match[1]
            mode = None
        elif pixel_format and (match := re.search(r"Size: Discrete (\d+)x(\d+)", line)):
            mode = {"format": pixel_format, "width": int(match[1]), "height": int(match[2]), "fps": []}
            modes.append(mode)
        elif mode is not None and (match := re.search(r"\(([\d.]+) fps\)", line)):
            mode["fps"].append(float(match[1]))
    if not modes:
        raise RuntimeError("No discrete V4L2 capture modes reported")
    return modes


def snapshot(device):
    info = identity(device)
    node = capture_node(device)
    result = subprocess.run(
        ["v4l2-ctl", "-d", str(node), "--list-formats-ext"],
        capture_output=True, text=True, check=True, timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    return {**info, "video_device": str(node), "modes": parse_modes(result.stdout)}


def select_capture(device, width, height, pixel_format, fps, frames):
    info = snapshot(device)
    pixel_format = pixel_format.upper()
    if pixel_format == "MJPEG":
        pixel_format = "MJPG"
    if not any(
        (mode["width"], mode["height"], mode["format"]) == (width, height, pixel_format)
        and any(abs(rate - fps) < 0.01 for rate in mode["fps"])
        for mode in info["modes"]
    ):
        raise RuntimeError("Capture mode is not currently advertised. See the formats command.")
    command = [
        "v4l2-ctl", "-d", info["video_device"],
        f"--set-fmt-video=width={width},height={height},pixelformat={pixel_format}",
        f"--set-parm={fps}", "--get-fmt-video", "--get-parm",
    ]
    if frames:
        command += ["--stream-mmap=8", "--stream-poll", f"--stream-count={frames}", "--stream-to=/dev/null"]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True,
            timeout=15 + frames / fps, env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.strip() or "V4L2 could not apply the capture mode") from error
    size = re.search(r"Width/Height\s*:\s*(\d+)/(\d+)", result.stdout)
    format_match = re.search(r"Pixel Format\s*:\s*'([^']+)'", result.stdout)
    rate = re.search(r"Frames per second\s*:\s*([\d.]+)", result.stdout)
    if not (size and format_match and rate):
        raise RuntimeError("Could not verify the negotiated V4L2 mode")
    actual = (int(size[1]), int(size[2]), format_match[1], float(rate[1]))
    if actual[:3] != (width, height, pixel_format) or abs(actual[3] - fps) >= 0.01:
        raise RuntimeError(f"V4L2 negotiated a different capture mode: {actual}")
    output = {
        "video_device": info["video_device"],
        "usb_port": info["usb_port"],
        "speed_mbps": info["speed_mbps"],
        "usb_10gbps_or_faster": info["usb_10gbps_or_faster"],
        "capture": {"width": actual[0], "height": actual[1], "format": actual[2], "fps": actual[3]},
    }
    if frames:
        output["buffer_test"] = {"requested_frames": frames, "log": result.stderr.strip()}
    return output


@contextmanager
def video_control(device):
    usb = base.load_usb()
    wrap = usb.libusb_wrap_sys_device
    wrap.argtypes = [C.c_void_p, C.c_ssize_t, C.POINTER(C.c_void_p)]
    wrap.restype = C.c_int
    info = identity(device)
    path = f"/dev/bus/usb/{info['bus']:03d}/{info['address']:03d}"
    context = C.c_void_p()
    handle = C.c_void_p()
    with open(path, "r+b", buffering=0) as usb_file:
        descriptor = usb_file.read(18)
        if len(descriptor) != 18 or descriptor[8:12] != bytes.fromhex("ca 07 53 25"):
            raise RuntimeError("USB device identity changed")
        base.check(usb, usb.libusb_init(C.byref(context)))
        claimed = detached = False
        active = {}
        try:
            base.check(usb, wrap(context, usb_file.fileno(), C.byref(handle)))
            active = {i: base.check(usb, usb.libusb_kernel_driver_active(handle, i)) for i in (0, 1)}
            if active[0]:
                base.check(usb, usb.libusb_detach_kernel_driver(handle, 0))
                detached = True
            base.check(usb, usb.libusb_claim_interface(handle, 0))
            claimed = True
            yield usb, handle
        finally:
            try:
                if claimed:
                    code = usb.libusb_release_interface(handle, 0)
                    if code != NO_DEVICE:
                        base.check(usb, code)
            finally:
                try:
                    if detached:
                        for interface in (0, 1):
                            state = usb.libusb_kernel_driver_active(handle, interface)
                            if state == NO_DEVICE:
                                break
                            if active[interface] and base.check(usb, state) == 0:
                                code = usb.libusb_attach_kernel_driver(handle, interface)
                                if code != NO_DEVICE:
                                    base.check(usb, code)
                finally:
                    if handle:
                        usb.libusb_close(handle)
                    usb.libusb_exit(context)


def send_index(usb, handle, index):
    payload = protocol.switch_packet(index)
    for selector, content in ((2, len(payload).to_bytes(2, "little")), (1, payload)):
        buffer = (C.c_ubyte * len(content)).from_buffer_copy(content)
        count = base.check(usb, usb.libusb_control_transfer(
            handle, 0x21, 1, selector << 8, 4 << 8, buffer, len(content), 1000
        ))
        if count != len(content):
            raise RuntimeError("Incomplete mode command")
    # An accepted command can remove this USB instance before we read its reply.
    time.sleep(0.2)
    try:
        size = int.from_bytes(base.read_control(usb, handle, 2, 0x85, 2), "little")
        base.read_control(usb, handle, 2, 0x81, size)
        size = int.from_bytes(base.read_control(usb, handle, 1, 0x85, 2), "little")
        reply = base.read_control(usb, handle, 1, 0x81, size)
    except RuntimeError as error:
        if str(error) == "LIBUSB_ERROR_NO_DEVICE":
            return
        raise
    if reply != bytes.fromhex("a1 00 5f"):
        raise RuntimeError(f"Unexpected mode command reply: {reply.hex(' ')}")


def reported_4k_mjpeg_rates(info):
    return {
        round(fps)
        for mode in info["modes"]
        if mode["format"] == "MJPG" and (mode["width"], mode["height"]) == (3840, 2160)
        for fps in mode["fps"]
    }


def wait_for_video(device, previous_address=None):
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        try:
            current = find_device(device.name)
            if previous_address is None or identity(current)["address"] != previous_address:
                capture_node(current)
                return snapshot(current)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
        time.sleep(0.25)
    raise RuntimeError("The capture device did not become ready after USB re-enumeration")


def decode_hdmi_timing(data):
    if len(data) < 40:
        raise RuntimeError("Incomplete HDMI input timing response")
    interlaced = bool(data[0] & 1)
    height, width = struct.unpack_from("<HH", data, 6)
    refresh_hz = struct.unpack_from("<H", data, 14)[0] / 100
    if not (width and height and refresh_hz):
        raise RuntimeError("HDMI input timing is not available")
    return {
        "width": width,
        "height": height * 2 if interlaced else height,
        "refresh_hz": refresh_hz,
        "interlaced": interlaced,
        # AT replies have a four-byte header. Wire byte 22 is payload byte 18.
        "hdr_detected": bool(data[18] & 0x10),
    }


def decode_hdmi_reply(reply, minimum_size):
    command = reply["command"]
    try:
        data = at.decode_response(reply, allow_short=True)
        if len(data) < minimum_size:
            raise RuntimeError(f"expected at least {minimum_size} payload bytes, got {len(data)}")
        return data
    except RuntimeError as error:
        raise RuntimeError(f"HDMI query {command}: {error} (response: {reply['response_hex']})") from error


def hdmi_status(device, *, raw=False):
    replies = {}
    with video_control(device) as (usb, handle):
        def read(command, minimum_size):
            reply = at.submit_status(usb, handle, command)
            replies[f"{command:#04x}"] = {key: reply[key] for key in ("status_hex", "response_hex")}
            if minimum_size is None:
                return
            return decode_hdmi_reply(reply, minimum_size)

        stable = int.from_bytes(read(0x38, 4)[:4], "little")
        code = read(0x4E, 1)[0]
        result = {"hdmi_input": {"stable": stable == 1}, "edid_mode_code": code, "edid_mode": EDID_NAMES.get(code)}
        if stable == 1:
            result["hdmi_input"].update(decode_hdmi_timing(read(0x37, 40)))
        else:
            # Capture the wire response even if the receiver reports no lock.
            try:
                read(0x37, None)
            except (RuntimeError, ValueError) as error:
                replies["0x37"] = {"error": str(error)}
    if raw or stable != 1:
        result["at_replies"] = replies
    return {**identity(device), **result}


def read_firmware(usb, handle):
    data, _ = read_at_reply(usb, handle, 0x77)
    return data.split(b"\0", 1)[0].decode("ascii")


def read_at_reply(usb, handle, command, *, allow_short=False):
    # The control endpoint can return stale data while the device settles.
    response_hex = None
    for delay in (0, 0.15, 0.35, 0.7):
        if delay:
            time.sleep(delay)
        try:
            reply = at.submit_status(usb, handle, command)
        except RuntimeError as error:
            if not str(error).startswith("Short reply:"):
                raise
            last_error = error
            response_hex = None
            continue
        response_hex = reply["response_hex"]
        try:
            data = at.decode_response(reply, allow_short=allow_short)
            if not data:
                raise RuntimeError("Missing response payload")
            return data, reply
        except RuntimeError as error:
            last_error = error
    detail = f" (response: {response_hex})" if response_hex is not None else ""
    raise RuntimeError(f"AT query {command:#04x}: {last_error}{detail}") from last_error


def read_hdmi_preset(usb, handle):
    data, reply = read_at_reply(usb, handle, 0x56, allow_short=True)
    return data[0], reply["response_hex"]


def hdmi_preset_result(device, firmware, code, response_hex):
    return {
        **identity(device),
        "firmware": firmware,
        "hdmi_preset_code": code,
        "hdmi_preset": HDMI_PRESET_NAMES.get(code),
        "response_hex": response_hex,
    }


def hdmi_preset(device):
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        code, response_hex = read_hdmi_preset(usb, handle)
    return hdmi_preset_result(device, firmware, code, response_hex)


def decode_edid_timing(timing):
    pixel_clock = int.from_bytes(timing[:2], "little") * 10_000
    width = timing[2] | (timing[4] & 0xF0) << 4
    height = timing[5] | (timing[7] & 0xF0) << 4
    if not (pixel_clock and width and height):
        return None
    h_blank = timing[3] | (timing[4] & 0x0F) << 8
    v_blank = timing[6] | (timing[7] & 0x0F) << 8
    interlaced = bool(timing[17] & 0x80)
    return {
        "width": width,
        "height": height * 2 if interlaced else height,
        "refresh_hz": round(pixel_clock / ((width + h_blank) * (height + v_blank + 0.5 * interlaced)), 3),
        "interlaced": interlaced,
    }


def edid_detailed_timings(block, start, stop):
    return [
        timing for offset in range(start, stop - 17, 18)
        if (timing := decode_edid_timing(block[offset:offset + 18])) is not None
    ]


def cta_data_blocks(block):
    end = block[2]
    if end == 0:
        return
    if not 4 <= end <= 127:
        raise RuntimeError("Invalid CTA timing descriptor offset")
    offset = 4
    while offset < end:
        header = block[offset]
        size = header & 31
        next_offset = offset + 1 + size
        if next_offset > end:
            raise RuntimeError("CTA data block exceeds its declared boundary")
        yield header >> 5, offset + 1, next_offset
        offset = next_offset


def cta_video_id(code):
    return code & 127 if 129 <= code <= 192 else code


def cta_video_modes(block):
    video_ids = [
        cta_video_id(code) for tag, start, stop in cta_data_blocks(block)
        if tag == 2 for code in block[start:stop]
    ]
    timings = edid_detailed_timings(block, block[2], 127) if block[2] else []
    return timings, video_ids


def describe_edid(data):
    blocks = [data[i:i + 128] for i in range(0, len(data), 128)]
    first = blocks[0]
    cta_timings = []
    cta_video_ids = []
    for block in blocks[1:]:
        if block[0] == 2:
            timings, video_ids = cta_video_modes(block)
            cta_timings.extend(timings)
            cta_video_ids.extend(video_ids)
    return {
        "extension_blocks": first[126],
        "base_detailed_timings": edid_detailed_timings(first, 54, 126),
        "cta_detailed_timings": cta_timings,
        "cta_video_ids": cta_video_ids,
        "edid_hex": data.hex(),
    }


def edid_table(device):
    with video_control(device) as (usb, handle):
        code, _ = read_hdmi_preset(usb, handle)
        first = at.read_edid_block(usb, handle, code, 0)
        if not first.startswith(bytes.fromhex("00 ff ff ff ff ff ff 00")) or first[126] > 3:
            raise RuntimeError("Invalid EDID base block or extension count")
        blocks = [first]
        for block in range(1, first[126] + 1):
            blocks.append(at.read_edid_block(usb, handle, code, block))
    return {
        **identity(device),
        "hdmi_preset_code": code,
        "hdmi_preset": HDMI_PRESET_NAMES.get(code),
        **describe_edid(b"".join(blocks)),
    }


def read_live_edid(usb, handle):
    first = at.read_hdmi_rx_edid(usb, handle, 1)
    if not first.startswith(bytes.fromhex("00 ff ff ff ff ff ff 00")) or first[126] > 3:
        raise RuntimeError("Invalid HDMI RX EDID header or extension count")
    block_count = first[126] + 1
    if block_count == 1:
        return first
    data = at.read_hdmi_rx_edid(usb, handle, block_count)
    if data[:128] != first:
        raise RuntimeError("HDMI RX EDID changed during the read")
    return data


def edid_live(device):
    with video_control(device) as (usb, handle):
        mode = decode_hdmi_reply(at.submit_status(usb, handle, 0x4E), 1)[0]
        data = read_live_edid(usb, handle)
    return {
        **identity(device),
        "edid_mode": EDID_NAMES.get(mode),
        "edid_mode_code": mode,
        "edid_bytes": len(data),
        "edid_sha256": hashlib.sha256(data).hexdigest(),
        **describe_edid(data),
    }


def edid_backup(device):
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        active, _ = read_hdmi_preset(usb, handle)
        if active not in HDMI_PRESET_NAMES:
            raise RuntimeError(f"Cannot identify the active EDID preset code {active}")
        storage = []
        for preset in range(7):
            blocks = [at.read_edid_block(usb, handle, preset, block, validate=False) for block in range(4)]
            if preset == active:
                first = blocks[0]
                if not first.startswith(bytes.fromhex("00 ff ff ff ff ff ff 00")) or first[126] > 3:
                    raise RuntimeError("Active EDID has an invalid base block")
                if any(sum(block) & 255 for block in blocks[:first[126] + 1]):
                    raise RuntimeError("Active EDID has an invalid block checksum")
            storage.extend(blocks)
    data = b"".join(storage)
    return {
        **identity(device),
        "firmware": firmware,
        "hdmi_preset_code": active,
        "edid_storage_bytes": len(data),
        "edid_storage_sha256": hashlib.sha256(data).hexdigest(),
        "edid_storage_hex": data.hex(),
    }


def load_edid_preview_source(backup_path):
    backup = json.loads(backup_path.read_text())
    if not isinstance(backup, dict) or not isinstance(backup.get("edid_storage_hex"), str):
        raise RuntimeError("Expected an EDID backup JSON object")
    storage = bytes.fromhex(backup["edid_storage_hex"])
    digest = hashlib.sha256(storage).hexdigest()
    if len(storage) != 3584 or backup.get("edid_storage_sha256") != digest:
        raise RuntimeError("Invalid EDID backup size or SHA-256")
    preset = backup.get("hdmi_preset_code")
    if type(preset) is not int or preset not in HDMI_PRESET_NAMES:
        raise RuntimeError("Invalid HDMI preset code in EDID backup")
    start = preset * 512
    bank = storage[start:start + 512]
    if not bank.startswith(bytes.fromhex("00 ff ff ff ff ff ff 00")) or bank[126] > 3:
        raise RuntimeError("Invalid EDID base block or extension count")
    size = 128 * (1 + bank[126])
    edid = bytearray(bank[:size])
    if any(sum(edid[i:i + 128]) & 255 for i in range(0, size, 128)):
        raise RuntimeError("Invalid EDID block checksum")
    return preset, digest, edid


def edid_prefer_preview(backup_path, width, height, rate):
    preset, digest, edid = load_edid_preview_source(backup_path)
    return {
        "hdmi_preset_code": preset,
        "source_edid_storage_sha256": digest,
        **prefer_edid_timing(edid, width, height, rate),
        "edid_hex": edid.hex(),
    }


def prefer_edid_timing(edid, width, height, rate):
    preferred_offset = 54
    previous = decode_edid_timing(edid[preferred_offset:preferred_offset + 18])
    if previous is None or not edid[24] & 2:
        raise RuntimeError("No preferred detailed timing in EDID base block")

    slots = list(range(54, 126, 18))
    for offset in range(128, len(edid), 128):
        block = edid[offset:offset + 128]
        if block[0] == 2:
            list(cta_data_blocks(block))
            if block[2]:
                slots.extend(range(offset + block[2], offset + 110, 18))

    matches = []
    for offset in slots:
        timing = decode_edid_timing(edid[offset:offset + 18])
        if (timing and not timing["interlaced"] and (timing["width"], timing["height"]) == (width, height)
                and abs(timing["refresh_hz"] - rate) < 0.1):
            matches.append((offset, timing))
    if len(matches) != 1:
        raise RuntimeError("Select a unique progressive mode listed in the EDID detailed timings")

    selected_offset, selected = matches[0]
    if selected_offset != preferred_offset:
        previous_data = edid[preferred_offset:preferred_offset + 18]
        edid[preferred_offset:preferred_offset + 18] = edid[selected_offset:selected_offset + 18]
        edid[selected_offset:selected_offset + 18] = previous_data
        for offset in {0, selected_offset // 128 * 128}:
            edid[offset + 127] = -sum(edid[offset:offset + 127]) & 255
    return {
        "previous_preferred": previous,
        "preferred": selected,
    }


def check_live_edid_write(usb, handle):
    firmware = read_firmware(usb, handle)
    if firmware != SUPPORTED_WRITE_FIRMWARE:
        raise RuntimeError(f"HDMI RX EDID writes are limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
    stable = decode_hdmi_reply(at.submit_status(usb, handle, 0x38), 4)
    if int.from_bytes(stable[:4], "little") != 0:
        raise RuntimeError("Turn off the HDMI source before writing the EDID")


def write_verified_live_edid(usb, handle, data):
    at.write_hdmi_rx_edid(usb, handle, data)
    if read_live_edid(usb, handle) != data:
        raise RuntimeError("HDMI RX EDID readback differs from the requested table")


def configure_live_edid(device, backup, width, height, rate, *, restore_after_test, video_id=None):
    with video_control(device) as (usb, handle):
        check_live_edid_write(usb, handle)
        original = read_live_edid(usb, handle)
        candidate = bytearray(original)
        if video_id is None:
            timings = prefer_edid_timing(candidate, width, height, rate)
        else:
            timings = prefer_cta_video_id(candidate, video_id)
        if candidate == original:
            raise RuntimeError("Select a different existing timing")
        with backup.open("xb") as file:
            file.write(original)
            file.flush()
            os.fsync(file.fileno())

        test_error = None
        restore = True
        try:
            write_verified_live_edid(usb, handle, candidate)
            restore = restore_after_test
        except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
            test_error = error
        finally:
            if restore:
                try:
                    write_verified_live_edid(usb, handle, original)
                except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
                    raise RuntimeError(f"Could not verify restoration of HDMI RX EDID, backup at {backup}: {error}") from error
        if test_error is not None:
            raise RuntimeError(f"HDMI RX EDID write failed, original verified after restoration: {test_error}") from test_error
    result = {**identity(device), **timings, "backup": str(backup), "write_verified": True}
    if restore_after_test:
        result["restore_verified"] = True
    return result


def restore_live_edid(device, backup):
    data = backup.read_bytes()
    at.validate_hdmi_rx_edid(data)
    with video_control(device) as (usb, handle):
        check_live_edid_write(usb, handle)
        original = read_live_edid(usb, handle)
        changed = original != data
        if changed:
            try:
                write_verified_live_edid(usb, handle, data)
            except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
                try:
                    write_verified_live_edid(usb, handle, original)
                except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as rollback_error:
                    raise RuntimeError(
                        f"Could not verify either EDID write; input backup is still at {backup}: {rollback_error}"
                    ) from error
                raise RuntimeError(f"Could not restore the backup; previous device EDID verified: {error}") from error
    return {
        **identity(device), "backup": str(backup), "changed": changed,
        "restore_verified": True, "edid_sha256": hashlib.sha256(data).hexdigest(),
    }


def edid_cta_prefer_preview(backup_path, video_id):
    preset, digest, edid = load_edid_preview_source(backup_path)
    return {
        "hdmi_preset_code": preset,
        "source_edid_storage_sha256": digest,
        **prefer_cta_video_id(edid, video_id),
        "edid_hex": edid.hex(),
    }


def prefer_cta_video_id(edid, video_id):
    offset = next((i for i in range(128, len(edid), 128) if edid[i] == 2), None)
    if offset is None:
        raise RuntimeError("No CTA extension in the EDID")
    block = edid[offset:offset + 128]
    video_offsets = []
    color_maps = []
    for tag, start, stop in cta_data_blocks(block):
        if tag == 2:
            video_offsets.extend(range(start, stop))
        elif tag == 7 and start < stop:
            if block[start] == 0x0D:
                raise RuntimeError("This CTA extension already has explicit video preferences")
            if block[start] == 0x0F:
                color_maps.append((start + 1, stop))
    matches = [i for i, pos in enumerate(video_offsets) if cta_video_id(block[pos]) == video_id]
    if len(matches) != 1:
        raise RuntimeError("Select a unique video ID in the first CTA extension")
    if len(color_maps) > 1:
        raise RuntimeError("Multiple CTA YCbCr 4:2:0 maps are unsupported")

    index = matches[0]
    first, selected = video_offsets[0], video_offsets[index]
    previous = cta_video_id(block[first])
    block[first], block[selected] = block[selected], block[first]
    if color_maps and index:
        start, stop = color_maps[0]
        if start < stop:
            bitmap = int.from_bytes(block[start:stop], "little")
            # CTA stores 4:2:0 capabilities by SVD position, not by video ID.
            if (bitmap & 1) != (bitmap >> index & 1):
                if index >= 8 * (stop - start):
                    raise RuntimeError("CTA YCbCr 4:2:0 map cannot represent the reordered modes")
                bitmap ^= 1 | 1 << index
            block[start:stop] = bitmap.to_bytes(stop - start, "little")
    block[127] = -sum(block[:127]) & 255
    edid[offset:offset + 128] = block
    return {
        "previous_first_cta_video_id": previous,
        "first_cta_video_id": video_id,
    }


def configure_hdmi_preset(device, preset):
    before = identity(device)
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        try:
            code, _ = read_hdmi_preset(usb, handle)
        except (RuntimeError, ValueError) as error:
            raise RuntimeError(f"Could not read HDMI preset before switching: {error}") from error
        previous = HDMI_PRESET_NAMES.get(code)
        if preset == previous:
            return {**before, "firmware": firmware, "hdmi_preset_code": code, "hdmi_preset": preset, "changed": False}
        if firmware != SUPPORTED_WRITE_FIRMWARE:
            raise RuntimeError(f"HDMI preset switching is limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
        if previous in (None, "1.4"):
            raise RuntimeError(f"Cannot determine the compatible HDMI preset group from code {code}")
        target = HDMI_PRESETS[preset] + (4 if code >= 4 else 0)
        at.set_hdmi_preset(usb, handle, target)
        time.sleep(0.2)
        try:
            at.reboot_for_hdmi_preset(usb, handle)
        except RuntimeError as error:
            # The reboot can disconnect the USB interface before it replies.
            if str(error) != "LIBUSB_ERROR_NO_DEVICE":
                raise
    wait_for_video(device, before["address"])
    try:
        with video_control(device) as (usb, handle):
            actual, response_hex = read_hdmi_preset(usb, handle)
    except (RuntimeError, ValueError, OSError) as error:
        raise RuntimeError(f"Could not verify HDMI preset after restart: {error}") from error
    if actual != target:
        raise RuntimeError(f"HDMI preset readback was {actual}, expected {target}")
    result = hdmi_preset_result(device, firmware, actual, response_hex)
    return {**result, "previous_hdmi_preset": previous, "changed": True}


def configure_edid(device, mode):
    target = at.EDID_MODES[mode]
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        before = decode_hdmi_reply(at.submit_status(usb, handle, 0x4E), 1)[0]
        if before != target:
            if firmware != SUPPORTED_WRITE_FIRMWARE:
                raise RuntimeError(f"EDID switching is limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
            if before not in EDID_NAMES:
                raise RuntimeError(f"Unknown current EDID mode code {before}")
            at.set_edid_mode(usb, handle, mode)
            for _ in range(5):
                time.sleep(0.2)
                current = decode_hdmi_reply(at.submit_status(usb, handle, 0x4E), 1)[0]
                if current == target:
                    break
            else:
                raise RuntimeError(f"EDID readback was {current}, expected {target}")
    return {
        **identity(device),
        "firmware": firmware,
        "previous_edid_mode": EDID_NAMES.get(before),
        "edid_mode": mode,
        "edid_mode_code": target,
        "changed": before != target,
    }


def read_hdr_to_sdr(usb, handle):
    data, reply = read_at_reply(usb, handle, 0x76, allow_short=True)
    if len(data) < 4:
        raise RuntimeError(f"HDR-to-SDR status is incomplete (response: {reply['response_hex']})")
    return bool(int.from_bytes(data[:4], "little"))


def describe_hdr_metadata(data):
    result = {"hdr_metadata_bytes": len(data), "hdr_metadata_hex": data.hex(" ")}
    # The receiver omits the HDMI checksum byte and pads the end of the InfoFrame.
    if len(data) >= 29 and data[:3] == b"\x87\x01\x1a":
        eotf = data[3]
        result.update({
            "input_eotf_code": eotf,
            "input_eotf": {0: "SDR", 1: "traditional HDR", 2: "PQ (SMPTE ST 2084)", 3: "HLG"}.get(eotf),
            "static_metadata_type_code": data[4],
            "mastering_values_present": any(data[5:29]) if data[4] == 0 else None,
        })
    return result


def hdr_metadata(device):
    with video_control(device) as (usb, handle):
        data, _ = read_at_reply(usb, handle, 0x65, allow_short=True)
    return {**identity(device), **describe_hdr_metadata(data)}


def hdr_to_sdr(device, target):
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle) if target is not None else None
        before = read_hdr_to_sdr(usb, handle)
        changed = target is not None and before != target
        if changed:
            if firmware != SUPPORTED_WRITE_FIRMWARE:
                raise RuntimeError(f"HDR-to-SDR switching is limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
            at.set_hdr_to_sdr(usb, handle, target)
            time.sleep(0.2)
            if read_hdr_to_sdr(usb, handle) != target:
                raise RuntimeError("HDR-to-SDR readback differs from the requested setting")
    return {**identity(device), "hdr_to_sdr": target if changed else before, "changed": changed}


def read_setting_value(usb, handle, command, size, *, signed=False):
    data, reply = read_at_reply(usb, handle, command, allow_short=True)
    if len(data) < size:
        raise RuntimeError(f"AT query {command:#04x} returned an incomplete response: {reply['response_hex']}")
    return int.from_bytes(data[:size], "little", signed=signed)


def audio_mixer(device, channel=None, gain=None):
    if channel is not None:
        reader, _, _, low, high = at.AUDIO_GAINS[channel]
        if gain is not None and not low <= gain <= high:
            raise ValueError(f"{channel} gain must be between {low} and {high}")
    with video_control(device) as (usb, handle):
        if channel is None:
            return {
                **identity(device),
                "gains": {
                    name: {"gain": read_setting_value(usb, handle, reader, 2, signed=True),
                           "min": low, "max": high}
                    for name, (reader, _, _, low, high) in at.AUDIO_GAINS.items()
                },
                "hdmi_audio_format_code": read_setting_value(usb, handle, 0x04, 1),
                "hdmi_sampling_rate_code": read_setting_value(usb, handle, 0x06, 1),
                "non_pcm_usb": bool(read_setting_value(usb, handle, 0x95, 1)),
            }
        before = read_setting_value(usb, handle, reader, 2, signed=True)
        changed = gain is not None and gain != before
        actual = before
        if changed:
            if read_firmware(usb, handle) != SUPPORTED_WRITE_FIRMWARE:
                raise RuntimeError(f"Audio gain writes are limited to firmware {SUPPORTED_WRITE_FIRMWARE}")
            at.set_audio_gain(usb, handle, channel, gain)
            time.sleep(0.2)
            actual = read_setting_value(usb, handle, reader, 2, signed=True)
            if actual != gain:
                raise RuntimeError(f"{channel} gain readback was {actual}, expected {gain}")
    return {**identity(device), "channel": channel, "gain": actual, "min": low, "max": high, "changed": changed}


def configure_switch(device, target, *, command, write, key):
    with video_control(device) as (usb, handle):
        before = bool(read_setting_value(usb, handle, command, 1))
        changed = target is not None and before != target
        if changed:
            if read_firmware(usb, handle) != SUPPORTED_WRITE_FIRMWARE:
                raise RuntimeError(f"{key} writes are limited to firmware {SUPPORTED_WRITE_FIRMWARE}")
            write(usb, handle, target)
            time.sleep(0.2)
            if bool(read_setting_value(usb, handle, command, 1)) != target:
                raise RuntimeError(f"{key} readback differs from the requested setting")
    return {**identity(device), key: target if changed else before, "changed": changed}


def set_auto_vrr(device, enabled):
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        if firmware != SUPPORTED_WRITE_FIRMWARE:
            raise RuntimeError(f"Auto-VRR writes are limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
        at.set_auto_vrr(usb, handle, enabled)
    return {**identity(device), "auto_vrr_requested": enabled, "device_echo_verified": True}


def configure(device, target):
    before = snapshot(device)
    with video_control(device) as (usb, handle):
        firmware = read_firmware(usb, handle)
        old_index, _ = protocol.get_index(usb, handle)
        changed = target is not None and old_index != target
        if changed:
            if firmware != SUPPORTED_WRITE_FIRMWARE:
                raise RuntimeError(f"Mode switching is limited to firmware {SUPPORTED_WRITE_FIRMWARE}, found {firmware}")
            send_index(usb, handle, target)
    after = wait_for_video(device, before["address"] if changed else None)
    result = {"firmware": firmware, "descriptor_index_before": old_index, "changed": changed, **after}
    if target is not None:
        rates = reported_4k_mjpeg_rates(after)
        verified = {120, 144} <= rates if target == 1 else 60 in rates and not rates.intersection({120, 144})
        if not verified:
            raise RuntimeError(f"Linux reported 4K MJPEG rates {sorted(rates)} after requesting index {target}")
        result["requested_index"] = target
        result["formats_verified"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB topology path, for example 2-1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List connected GC553G2 cards with their USB ports")
    for name in ("formats", "status", "enable", "disable"):
        commands.add_parser(name)
    hdmi = commands.add_parser("hdmi-status", help="Read HDMI input timing, HDR detection and the EDID mode code")
    hdmi.add_argument("--raw", action="store_true", help="Include USB replies even when HDMI input is stable")
    hdmi_preset_command = commands.add_parser("hdmi-preset", help="Read or set the HDMI compatibility preset")
    hdmi_preset_command.add_argument("preset", nargs="?", choices=HDMI_PRESETS, help="Setting a preset restarts the card")
    edid = commands.add_parser("edid", help="Select and verify the HDMI EDID mode")
    edid.add_argument("mode", choices=at.EDID_MODES)
    tone = commands.add_parser("hdr-to-sdr", help="Read or set the HDR-to-SDR device setting")
    tone.add_argument("mode", nargs="?", choices=("on", "off"))
    commands.add_parser("hdr-metadata", help="Read the receiver's raw HDR metadata")
    vrr = commands.add_parser("auto-vrr", help="Set the card's Automatic VRR option and check its reply")
    vrr.add_argument("mode", choices=("on", "off"))
    audio = commands.add_parser("audio", help="Read audio diagnostics or set one mixer gain")
    audio.add_argument("channel", nargs="?", choices=at.AUDIO_GAINS)
    audio.add_argument("gain", nargs="?", type=int)
    bitstream = commands.add_parser("audio-bitstream", help="Read or set the non-PCM USB audio switch")
    bitstream.add_argument("mode", nargs="?", choices=("on", "off"))
    hdcp = commands.add_parser("hdcp-detection", help="Read or set the card's HDMI HDCP detection")
    hdcp.add_argument("mode", nargs="?", choices=("on", "off"))
    rgb = commands.add_parser("rgb", help="Read LampArray attributes, set a solid color or restore autonomous lighting")
    rgb.add_argument("mode", nargs="?", default="status", help="status, autonomous, or RRGGBB")
    rgb.add_argument("--brightness", type=int, metavar="0..100", help="Brightness percentage for a solid color (default 100)")
    effect = commands.add_parser("rgb-effect", help="Send one of the card's native lighting presets")
    effect.add_argument("effect", choices=NATIVE_EFFECTS)
    commands.add_parser("edid-table", help="Read the EDID table stored for the selected HDMI preset")
    commands.add_parser("edid-live", help="Read the EDID table from the HDMI input receiver")
    for name, help_text in (
        ("edid-live-test", "Test a temporary EDID and restore the HDMI receiver table"),
        ("edid-live-set", "Set a preferred timing in the HDMI receiver EDID"),
    ):
        live = commands.add_parser(name, help=help_text)
        live.add_argument("--backup", type=Path, required=True, help="New file for the original EDID")
        live.add_argument("--width", type=int)
        live.add_argument("--height", type=int)
        live.add_argument("--hz", type=float)
        live.add_argument("--vic", type=int, help="Prioritize an existing CTA video ID")
    restore = commands.add_parser("edid-live-restore", help="Restore a binary HDMI receiver EDID backup")
    restore.add_argument("backup", type=Path)
    commands.add_parser("edid-backup", help="Read and back up all stored HDMI EDID presets as JSON")
    preview = commands.add_parser("edid-prefer-preview", help="Preview a preferred timing from an EDID backup")
    preview.add_argument("backup", type=Path)
    preview.add_argument("--width", type=int)
    preview.add_argument("--height", type=int)
    preview.add_argument("--hz", type=float)
    preview.add_argument("--vic", type=int, help="Move this CTA video ID to the first advertised CTA position")
    capture = commands.add_parser("capture", help="Select and verify the V4L2 capture mode")
    capture.add_argument("--width", type=int, required=True)
    capture.add_argument("--height", type=int, required=True)
    capture.add_argument("--format", required=True)
    capture.add_argument("--fps", type=float, required=True)
    capture.add_argument("--test-frames", type=int, default=0, choices=range(0, 2401),
                         metavar="0..2400", help="Read this many V4L2 buffers without saving them")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, base.interrupt)
    try:
        if args.command in ("edid-prefer-preview", "edid-live-test", "edid-live-set"):
            dimensions = (args.width, args.height, args.hz)
            provided = sum(value is not None for value in dimensions)
            if (args.vic is not None and provided != 0) or (args.vic is None and provided != 3):
                raise RuntimeError("Specify --vic or all of --width, --height and --hz")
        if args.command == "edid-prefer-preview":
            if args.vic is not None:
                result = edid_cta_prefer_preview(args.backup, args.vic)
            else:
                result = edid_prefer_preview(args.backup, *dimensions)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "devices":
            print(json.dumps({"devices": [identity(device) for device in find_devices(args.port)]}, indent=2))
            return 0
        device = find_device(args.port)
        if args.command == "formats":
            result = snapshot(device)
        elif args.command == "capture":
            result = select_capture(device, args.width, args.height, args.format, args.fps, args.test_frames)
        elif args.command == "rgb":
            result = {**identity(device), **lamp_array(device, args.mode, args.brightness)}
        elif args.command == "rgb-effect":
            result = {**identity(device), **set_native_effect(device, args.effect)}
        else:
            if os.geteuid():
                raise RuntimeError("Close capture applications and run this command with sudo")
            fd = os.open("/run/lock/gc553g2-mode.lock", os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
            with os.fdopen(fd, "r+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == "hdmi-status":
                    result = hdmi_status(device, raw=args.raw)
                elif args.command == "hdmi-preset":
                    result = configure_hdmi_preset(device, args.preset) if args.preset else hdmi_preset(device)
                elif args.command == "edid":
                    result = configure_edid(device, args.mode)
                elif args.command == "hdr-to-sdr":
                    result = hdr_to_sdr(device, {None: None, "on": True, "off": False}[args.mode])
                elif args.command == "hdr-metadata":
                    result = hdr_metadata(device)
                elif args.command == "auto-vrr":
                    result = set_auto_vrr(device, args.mode == "on")
                elif args.command == "audio":
                    result = audio_mixer(device, args.channel, args.gain)
                elif args.command == "audio-bitstream":
                    result = configure_switch(
                        device, {None: None, "on": True, "off": False}[args.mode],
                        command=0x95, write=at.set_audio_bitstream, key="non_pcm_usb",
                    )
                elif args.command == "hdcp-detection":
                    result = configure_switch(
                        device, {None: None, "on": True, "off": False}[args.mode],
                        command=0x72, write=at.set_hdcp_detection, key="hdcp_detection",
                    )
                elif args.command == "edid-table":
                    result = edid_table(device)
                elif args.command == "edid-live":
                    result = edid_live(device)
                elif args.command in ("edid-live-test", "edid-live-set"):
                    result = configure_live_edid(
                        device, args.backup, args.width, args.height, args.hz,
                        restore_after_test=args.command == "edid-live-test", video_id=args.vic,
                    )
                elif args.command == "edid-live-restore":
                    result = restore_live_edid(device, args.backup)
                elif args.command == "edid-backup":
                    result = edid_backup(device)
                else:
                    target = {"status": None, "enable": 1, "disable": 0}[args.command]
                    result = configure(device, target)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(json.dumps({"error": str(error) or "Interrupted"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
