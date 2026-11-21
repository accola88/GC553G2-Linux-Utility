#!/usr/bin/env python3
"""Query GC553G2 firmware and USB descriptor index via the official AT commands.

Close OBS. Temporarily detaches/rebinds video drivers. Sends SET_CUR only to
submit the two status queries (AT 0x77 and AT 0xb1), then reads their replies.
Does not submit descriptor switching, firmware flashing or reboot commands.
Hardware execution requires --run. Requires avermedia-usb-probe.py alongside.
"""
import ctypes as C
import importlib.util
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location('usb_probe', Path(__file__).with_name('avermedia-usb-probe.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

EDID_MODES = {'merge': 4, 'copy': 1, 'internal': 0}
# Read command, write command, signed write width, and the SDK slider range.
AUDIO_GAINS = {
    'hdmi': (0x0D, 0x0C, 4, -72, 30),
    'headphones': (0x67, 0x66, 2, -50, 12),
    'microphone': (0x0B, 0x0A, 4, -18, 52),
    'controller': (0x7F, 0x80, 2, -10000, 10000),
}
STATUS_COMMANDS = {0x04, 0x06, 0x37, 0x38, 0x4E, 0x56, 0x65, 0x72, 0x76, 0x77, 0x95, 0xB1}
STATUS_COMMANDS.update(control[0] for control in AUDIO_GAINS.values())


def request_packet(command, data=b''):
    body = command.to_bytes(4, 'little') + data
    if len(body) < 127:
        header = bytes([0xA1, (len(body) + 2) & 127, 0, 0])
    else:
        length = len(body) + 1
        header = bytes([0xA1, 0x80 | (length >> 8), length & 255, 0])
    payload = header + body
    return payload + bytes([(-sum(payload)) & 255])


def packet(command):
    if command not in STATUS_COMMANDS:
        raise ValueError('Only known read-only status commands are permitted')
    return request_packet(command)


def exchange(usb, handle, payload):
    # The first transfer announces length; the second submits the request.
    for selector, content in ((2, len(payload).to_bytes(2, 'little')), (1, payload)):
        buffer = (C.c_ubyte * len(content)).from_buffer_copy(content)
        sent = base.check(usb, usb.libusb_control_transfer(
            handle, 0x21, 0x01, selector << 8, 4 << 8,
            buffer, len(content), 1000))
        if sent != len(content):
            raise RuntimeError(f'Short command transfer: {sent}/{len(content)}')
    time.sleep(0.05)
    status_len = int.from_bytes(base.read_control(usb, handle, 2, 0x85, 2), 'little')
    status = base.read_control(usb, handle, 2, 0x81, status_len)
    length = int.from_bytes(base.read_control(usb, handle, 1, 0x85, 2), 'little')
    response = base.read_control(usb, handle, 1, 0x81, length)
    return {'request_hex': payload.hex(' '),
            'status_hex': status.hex(' '), 'response_length': length,
            'response_hex': response.hex(' '),
            'checksum_mod256': sum(response) & 255,
            'printable_ascii': ''.join(chr(c) if 32 <= c < 127 else '.' for c in response)}


def submit_status(usb, handle, command):
    return {'command': hex(command), **exchange(usb, handle, packet(command))}


def write_setting(usb, handle, command, data, expected_echoes):
    reply = exchange(usb, handle, request_packet(command, data))
    try:
        response = decode_response(reply, allow_short=True)
        if response != b'' and response not in expected_echoes:
            raise RuntimeError('Device acknowledgement did not match the requested setting')
    except RuntimeError as error:
        raise RuntimeError(f"{error} (response: {reply['response_hex']})") from error


def set_edid_mode(usb, handle, mode):
    mode_data = EDID_MODES[mode].to_bytes(4, 'little')
    write_setting(usb, handle, 0x4D, mode_data, (mode_data,))


def set_hdmi_preset(usb, handle, code):
    if code not in (0, 1, 2, 4, 5, 6):
        raise ValueError('Unsupported HDMI preset code')
    data = bytes([code])
    write_setting(usb, handle, 0x55, data, (data, code.to_bytes(4, 'little')))


def reboot_for_hdmi_preset(usb, handle):
    write_setting(usb, handle, 0x25, b'', ())


def set_hdr_to_sdr(usb, handle, enabled):
    if type(enabled) is not bool:
        raise ValueError('HDR-to-SDR setting must be on or off')
    data = bytes([enabled])
    write_setting(usb, handle, 0x1F, data, (data, int(enabled).to_bytes(4, 'little')))


def set_auto_vrr(usb, handle, enabled):
    reply = exchange(usb, handle, request_packet(0x89, bytes([enabled])))
    expected = int(enabled).to_bytes(4, 'little')
    if decode_response(reply, allow_short=True) != expected:
        raise RuntimeError(f"Auto-VRR acknowledgement did not echo the requested setting (response: {reply['response_hex']})")


def set_audio_gain(usb, handle, channel, gain):
    _, command, size, minimum, maximum = AUDIO_GAINS[channel]
    if not minimum <= gain <= maximum:
        raise ValueError(f'{channel} gain must be between {minimum} and {maximum}')
    data = gain.to_bytes(size, 'little', signed=True)
    echoes = (data, gain.to_bytes(2, 'little', signed=True))
    if channel == 'controller':
        # The SDK uses a nonzero success byte for AUX writes.
        echoes += (b'\x01', b'\x01\x00\x00\x00')
    write_setting(usb, handle, command, data, echoes)


def set_audio_bitstream(usb, handle, enabled):
    if type(enabled) is not bool:
        raise ValueError('Audio bitstream setting must be on or off')
    data = int(enabled).to_bytes(4, 'little')
    write_setting(usb, handle, 0x94, data, (data, data[:1]))


def set_hdcp_detection(usb, handle, enabled):
    if type(enabled) is not bool:
        raise ValueError('HDCP detection must be on or off')
    data = int(enabled).to_bytes(4, 'little')
    write_setting(usb, handle, 0x03, data, (data, data[:1]))


def read_edid_block(usb, handle, preset, block, *, validate=True):
    if not 0 <= preset <= 6 or not 0 <= block < 4:
        raise ValueError('EDID preset or block is out of range')
    offset = preset * 512 + block * 128
    request = offset.to_bytes(2, 'little') + (128).to_bytes(2, 'little')
    reply = exchange(usb, handle, request_packet(0x54, request))
    try:
        data = decode_response(reply, allow_short=True)
        if len(data) < 128:
            raise RuntimeError(f'Expected 128 EDID bytes, received {len(data)}')
        data = data[:128]
        if validate and sum(data) & 255:
            raise RuntimeError('Invalid EDID block checksum')
        return data
    except RuntimeError as error:
        raise RuntimeError(f'EDID preset {preset}, block {block}: {error}') from error


def read_hdmi_rx_edid(usb, handle, block_count):
    if not 1 <= block_count <= 4:
        raise ValueError('HDMI RX EDID must have between one and four blocks')
    blocks = []
    for index in range(block_count):
        last = index + 1 == block_count
        request = bytes([last, 0]) + (128).to_bytes(2, 'little') + (128 * block_count).to_bytes(2, 'little')
        reply = exchange(usb, handle, request_packet(0x58, request))
        try:
            block = decode_response(reply, allow_short=True)[:128]
            if len(block) != 128 or sum(block) & 255:
                raise RuntimeError('Invalid EDID block length or checksum')
        except RuntimeError as error:
            raise RuntimeError(f'HDMI RX EDID block {index}: {error} (response: {reply["response_hex"]})') from error
        blocks.append(block)
    return b''.join(blocks)


def validate_hdmi_rx_edid(edid):
    if len(edid) not in (128, 256, 384, 512):
        raise ValueError('HDMI RX EDID must have between one and four complete blocks')
    if not edid.startswith(bytes.fromhex('00 ff ff ff ff ff ff 00')) or edid[126] + 1 != len(edid) // 128:
        raise ValueError('Invalid HDMI RX EDID header or extension count')
    if any(sum(edid[offset:offset + 128]) & 255 for offset in range(0, len(edid), 128)):
        raise ValueError('Invalid HDMI RX EDID checksum')


def write_hdmi_rx_edid(usb, handle, edid):
    validate_hdmi_rx_edid(edid)
    blocks = [edid[offset:offset + 128] for offset in range(0, len(edid), 128)]
    for index, block in enumerate(blocks):
        header = bytes([index + 1 == len(blocks), 0]) + b'\x80\0' + len(edid).to_bytes(2, 'little')
        reply = exchange(usb, handle, request_packet(0x57, header + block))
        try:
            decode_response(reply, allow_short=True)
        except RuntimeError as error:
            raise RuntimeError(f'HDMI RX EDID write block {index}: {error} (response: {reply["response_hex"]})') from error


def decode_response(reply, *, allow_short=False):
    raw = bytes.fromhex(reply['response_hex'])
    if len(raw) < 3 or raw[0] != 0xA1 or sum(raw) & 255:
        raise RuntimeError('Invalid AT response header/checksum')
    if raw[1] & 0x80:
        if len(raw) < 5 or raw[3] != 0:
            raise RuntimeError('Invalid extended AT response header')
        encoded_length = ((raw[1] & 0x7F) << 8) | raw[2]
        if encoded_length != len(raw) - 4:
            raise RuntimeError('Extended AT response length mismatch')
        return raw[4:-1]
    if not allow_short:
        raise RuntimeError('Unexpected short AT response format')
    if raw[1] != len(raw) - 3:
        raise RuntimeError('Short AT response length mismatch')
    if raw[1] == 0:
        return b''  # Acknowledgement without a payload.
    if len(raw) < 5 or raw[2:4] != b'\0\0':
        raise RuntimeError('Invalid short AT response header')
    return raw[4:-1]


def status(usb, handle):
    """Read GC553G2 firmware and 4K144 descriptor-index state via AT status queries.

    Uses SET_CUR transport packets to submit GET commands 0x77 and 0xb1.
    Temporarily detaches and restores the video driver. No setting commands.
    """
    result = {}
    for name, command in [('firmware', 0x77), ('descriptor_index', 0xB1)]:
        try:
            result[name] = submit_status(usb, handle, command)
        except (RuntimeError, ValueError) as error:
            result[name] = {'error': str(error)}
            break  # Avoid further requests after a protocol mismatch.
    return result


if __name__ == '__main__':
    base.main(status)
