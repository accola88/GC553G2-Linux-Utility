#!/usr/bin/env python3
"""Enable/disable the GC553G2 USB descriptor index used by Windows' 4K144 toggle.

Temporarily detaches video drivers. Uses AT 0xb0 with descriptor index 0 or 1.
Reads and validates firmware/index before changing anything, then reads back.
No flashing, reset or reboot command. Reconnect USB afterwards to refresh descriptors.
Requires avermedia-at-status.py and avermedia-usb-probe.py alongside this file.
"""
import argparse
import ctypes as C
import importlib.util
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location('at_status', Path(__file__).with_name('avermedia-at-status.py'))
at = importlib.util.module_from_spec(spec)
spec.loader.exec_module(at)
base = at.base


decode = at.decode_response


def get_index(usb, handle):
    reply = at.submit_status(usb, handle, 0xB1)
    data = decode(reply)
    # AT_Get_USB_decription_Index reads byte 4 of the decoded response.
    if len(data) != 132 or data[:4] != b'\x01\x00\x00\x00' or data[4] not in (0, 1):
        raise RuntimeError('Unexpected descriptor-index response; stopping')
    return data[4], reply


def switch_packet(index):
    if type(index) is not int or index not in (0, 1):
        raise ValueError('Only descriptor index 0 or 1 is allowed')
    # AT_Set_USB_decription_Index: command 0xb0, LE32(1), LE32(index).
    content = (0xB0).to_bytes(4, 'little') + (1).to_bytes(4, 'little') + index.to_bytes(4, 'little')
    payload = bytes([0xA1, len(content) + 2, 0, 0]) + content
    return payload + bytes([(-sum(payload)) & 255])


def set_index(usb, handle, index):
    payload = switch_packet(index)
    for selector, content in ((2, len(payload).to_bytes(2, 'little')), (1, payload)):
        buffer = (C.c_ubyte * len(content)).from_buffer_copy(content)
        count = base.check(usb, usb.libusb_control_transfer(
            handle, 0x21, 1, selector << 8, 4 << 8, buffer, len(content), 1000))
        if count != len(content):
            raise RuntimeError('Short descriptor-switch transfer; check device state before retrying')
    time.sleep(0.2)
    size = int.from_bytes(base.read_control(usb, handle, 2, 0x85, 2), 'little')
    status = base.read_control(usb, handle, 2, 0x81, size)
    size = int.from_bytes(base.read_control(usb, handle, 1, 0x85, 2), 'little')
    response = base.read_control(usb, handle, 1, 0x81, size)
    # Preserve the acknowledgement; read-back of index is the success criterion.
    return {'request_hex': payload.hex(' '), 'status_hex': status.hex(' '),
            'response_hex': response.hex(' '), 'checksum_mod256': sum(response) & 255}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--enable', action='store_true')
    group.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    target = int(args.enable)

    def perform(usb, handle):
        result = {'requested_index': target, 'setting_command_sent': False}
        try:
            firmware = at.submit_status(usb, handle, 0x77)
            version = decode(firmware).split(b'\0', 1)[0].decode('ascii')
            result['firmware'] = version
            if version != '1.0.10.2':
                raise RuntimeError('Firmware differs from the inspected device; stopping')
            before, reply = get_index(usb, handle)
            result.update(before_index=before, before_reply=reply)
            if before != target:
                result['setting_command_sent'] = True
                result['switch_ack'] = set_index(usb, handle, target)
                after, reply = get_index(usb, handle)
                result.update(after_index=after, after_reply=reply)
                if after != target:
                    raise RuntimeError('Descriptor-index read-back does not match target')
            else:
                result['after_index'] = before
            result['index_verified'] = True
            result['next_step'] = 'Reconnect USB and re-list V4L2 capture formats; index alone does not prove 4K144 capture'
        except (RuntimeError, ValueError, UnicodeError) as error:
            result['error'] = str(error)
        return result

    perform.__doc__ = __doc__
    # The base runner owns claim/release and driver restoration in a finally block.
    sys.argv = [sys.argv[0], '--run']
    base.main(perform)


if __name__ == '__main__':
    main()
