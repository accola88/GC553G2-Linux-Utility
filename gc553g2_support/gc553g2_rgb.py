"""Control the GC553G2's standard and vendor HID lighting interfaces."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import struct
import time

HIDRAW = Path("/sys/class/hidraw")
LAMP_DESCRIPTOR_SHA256 = "82475fa450cf24b689ecebcf0b342470412c19ac4f1970ff6a4fad66840b6d39"
NATIVE_DESCRIPTOR_SHA256 = "6dd103e16388da723b868d3e425b031cfcc777f5e7c3653dc9c6ca0ece6acccd"
NATIVE_EFFECTS = {
    "static": 0x01,
    "breathing": 0x02,
    "blinking": 0x03,
    "cycle": 0x04,
    "rainbow": 0x05,
    "rainbow-breath": 0x06,
    "comet": 0x07,
    "rainbow-comet": 0x08,
    "dancing-flash": 0x09,
    "rainbow-flash": 0x0A,
    "wave": 0x0B,
    "glowing-yoyo": 0x0C,
    "starry-night": 0x0D,
    "flashing": 0x0F,
    "rainbow-wave": 0x10,
}


def find_rgb_node(device, descriptor_sha256, interface_name):
    serial = (device / "serial").read_text().strip()
    if not serial:
        raise RuntimeError("GC553G2 USB serial is required to match the RGB interface")
    matches = []
    for node in HIDRAW.glob("hidraw*"):
        hid = node / "device"
        if not hid.resolve().name.startswith("0003:07CA:D553."):
            continue
        try:
            attributes = dict(line.split("=", 1) for line in (hid / "uevent").read_text().splitlines() if "=" in line)
            if attributes.get("HID_UNIQ") != serial:
                continue
            descriptor = (hid / "report_descriptor").read_bytes()
            if hashlib.sha256(descriptor).hexdigest() == descriptor_sha256:
                matches.append(node)
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"Expected one recognized GC553G2 {interface_name} interface for this USB serial")
    return matches[0]


@contextmanager
def open_rgb_node(node):
    path = Path("/dev") / node.name
    devno = tuple(map(int, (node / "dev").read_text().strip().split(":")))
    with path.open("r+b", buffering=0) as file:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        actual = os.fstat(file.fileno()).st_rdev
        if (os.major(actual), os.minor(actual)) != devno:
            raise RuntimeError("RGB device changed while opening its HID interface")
        yield path, file


def feature_report(file, report, *, read=False):
    buffer = bytearray(report)
    # HIDIOCGFEATURE and HIDIOCSFEATURE use both ioctl direction flags.
    request = 0xC0000000 | len(buffer) << 16 | ord("H") << 8 | (0x07 if read else 0x06)
    count = fcntl.ioctl(file, request, buffer, True)
    if count != len(buffer) or buffer[0] != report[0]:
        raise RuntimeError(f"Incomplete HID feature report {report[0]}: {count}/{len(buffer)} bytes")
    return buffer


def lamp_attributes(file, index):
    feature_report(file, struct.pack("<BH", 2, index))
    report = feature_report(file, b"\x03" + bytes(28), read=True)
    lamp_id, = struct.unpack_from("<H", report, 1)
    if lamp_id != index:
        raise RuntimeError(f"Lamp {index} attributes returned for lamp {lamp_id}")
    return tuple(report[23:28])


def color_reports(lamps, rgb, brightness=100):
    for start in range(0, len(lamps), 10):
        group = lamps[start:start + 10]
        updates = bytearray()
        for red_levels, green_levels, blue_levels, intensity_levels, programmable in group:
            levels = (red_levels, green_levels, blue_levels) if programmable else (0, 0, 0)
            # Scale the color channels even if the lamp's overall intensity is binary.
            channels = [(component * maximum * brightness + 12_750) // 25_500
                        for component, maximum in zip(rgb, levels)]
            updates.extend(channels)
            updates.append(intensity_levels if any(channels) else 0)
        finish = start + len(group)
        ids = list(range(start, finish))
        ids.extend([0] * (10 - len(ids)))
        header = struct.pack("<BBB10H", 4, len(group), finish == len(lamps), *ids)
        yield header + updates.ljust(40, b"\0")


def lamp_array(device, mode, brightness=None):
    if mode not in ("status", "autonomous") and re.fullmatch(r"#?[0-9a-fA-F]{6}", mode) is None:
        raise ValueError("RGB mode must be status, autonomous or a six-digit RGB color")
    if brightness is not None:
        if mode in ("status", "autonomous"):
            raise ValueError("--brightness requires a six-digit RGB color")
        if not 0 <= brightness <= 100:
            raise ValueError("RGB brightness must be from 0 to 100")
    else:
        brightness = 100
    node = find_rgb_node(device, LAMP_DESCRIPTOR_SHA256, "LampArray")
    with open_rgb_node(node) as (path, file):
        attributes = feature_report(file, b"\x01" + bytes(22), read=True)
        count, _, _, _, kind, delay_us = struct.unpack_from("<HIIIII", attributes, 1)
        if not 1 <= count <= 256:
            raise RuntimeError(f"Unexpected LampArray lamp count: {count}")
        result = {"hidraw": str(path), "lamps": count, "lamp_array_kind": kind, "min_update_us": delay_us}
        if mode == "status":
            return result
        if mode == "autonomous":
            feature_report(file, b"\x06\x01")
            return {**result, "autonomous": True}

        rgb = bytes.fromhex(mode.lstrip("#"))
        lamps = [lamp_attributes(file, index) for index in range(count)]
        if not any(lamp[-1] for lamp in lamps):
            raise RuntimeError("The LampArray has no programmable LEDs")
        feature_report(file, b"\x06\x00")
        for index, report in enumerate(color_reports(lamps, rgb, brightness)):
            if index and delay_us:
                time.sleep(delay_us / 1_000_000)
            feature_report(file, report)
    return {**result, "autonomous": False, "rgb_requested": "#" + rgb.hex(),
            "brightness_percent": brightness}


def set_native_effect(device, effect):
    node = find_rgb_node(device, NATIVE_DESCRIPTOR_SHA256, "native RGB")
    report = bytes([0x75, 0x20, 0x00, NATIVE_EFFECTS[effect], 0x04, 0x01, 0x05, 0x01]) + bytes(24)
    with open_rgb_node(node) as (path, file):
        feature_report(file, report)
    return {"hidraw": str(path), "effect_requested": effect, "report_sent": True}
