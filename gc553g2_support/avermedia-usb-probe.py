#!/usr/bin/env python3
"""Read GC553G2 extension unit 4 directly with libusb.

Temporarily releases the video driver. Close OBS and capture applications.
Only GET_LEN, GET_INFO and GET_CUR device requests are issued.
No configuration changes, USB resets, firmware writes or SET_CUR requests.
"""
import argparse
import ctypes as C
import ctypes.util
import json
import signal


def load_usb():
    path = ctypes.util.find_library('usb-1.0')
    if not path:
        raise RuntimeError('libusb-1.0 is missing')
    usb = C.CDLL(path)
    signatures = {
        'init': ([C.POINTER(C.c_void_p)], C.c_int),
        'exit': ([C.c_void_p], None),
        'open_device_with_vid_pid': ([C.c_void_p, C.c_uint16, C.c_uint16], C.c_void_p),
        'close': ([C.c_void_p], None),
        'kernel_driver_active': ([C.c_void_p, C.c_int], C.c_int),
        'detach_kernel_driver': ([C.c_void_p, C.c_int], C.c_int),
        'attach_kernel_driver': ([C.c_void_p, C.c_int], C.c_int),
        'claim_interface': ([C.c_void_p, C.c_int], C.c_int),
        'release_interface': ([C.c_void_p, C.c_int], C.c_int),
        'control_transfer': ([C.c_void_p, C.c_uint8, C.c_uint8, C.c_uint16,
                              C.c_uint16, C.POINTER(C.c_ubyte), C.c_uint16,
                              C.c_uint], C.c_int),
        'error_name': ([C.c_int], C.c_char_p),
    }
    for name, (args, result) in signatures.items():
        function = getattr(usb, 'libusb_' + name)
        function.argtypes, function.restype = args, result
    return usb


def check(usb, code):
    if code < 0:
        raise RuntimeError(usb.libusb_error_name(code).decode())
    return code


def read_control(usb, handle, selector, operation, length):
    if operation not in (0x81, 0x85, 0x86) or not 1 <= length <= 4096:
        raise ValueError('Only bounded UVC GET requests are allowed')
    data = (C.c_ubyte * length)()
    # IN | CLASS | INTERFACE; wValue=selector; wIndex=unit 4/interface 0.
    count = check(usb, usb.libusb_control_transfer(
        handle, 0xA1, operation, selector << 8, 4 << 8, data, length, 1000))
    if count != length:
        raise RuntimeError(f'Short reply: {count} of {length} bytes')
    return bytes(data)


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(probe_callback=None):
    parser = argparse.ArgumentParser(description=probe_callback.__doc__ if probe_callback else __doc__)
    parser.add_argument('--run', action='store_true', help='Perform the temporary driver handoff and reads')
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return
    signal.signal(signal.SIGTERM, interrupt)
    usb = load_usb()
    context = C.c_void_p()
    check(usb, usb.libusb_init(C.byref(context)))
    handle = None
    claimed = False
    detached = False
    original = {}
    result = {'device': '07ca:2553', 'unit': 4, 'controls': [], 'restoration': {}}
    try:
        handle = usb.libusb_open_device_with_vid_pid(context, 0x07CA, 0x2553)
        if not handle:
            raise RuntimeError('Cannot open 07ca:2553; run as root and check the USB connection')
        original = {i: check(usb, usb.libusb_kernel_driver_active(handle, i)) for i in (0, 1)}
        result['original_video_driver_active'] = original
        if original[0]:
            check(usb, usb.libusb_detach_kernel_driver(handle, 0))
            detached = True
        check(usb, usb.libusb_claim_interface(handle, 0))
        claimed = True
        if probe_callback:
            result['at_status'] = probe_callback(usb, handle)
        for selector in ([] if probe_callback else [bit + 1 for bit in range(24) if 0x01FE0F & (1 << bit)]):
            entry = {'selector': selector}
            for name, operation, length in [('length', 0x85, 2), ('info', 0x86, 1)]:
                try:
                    entry[name] = int.from_bytes(read_control(usb, handle, selector, operation, length), 'little')
                except RuntimeError as error:
                    entry[name + '_error'] = str(error)
            if entry.get('info', 0) & 1 and 1 <= entry.get('length', 0) <= 4096:
                try:
                    value = read_control(usb, handle, selector, 0x81, entry['length'])
                    entry['current_hex'] = value.hex(' ')
                    entry['printable_ascii'] = ''.join(chr(c) if 32 <= c < 127 else '.' for c in value)
                except RuntimeError as error:
                    entry['current_error'] = str(error)
            result['controls'].append(entry)
    except (RuntimeError, OSError, KeyboardInterrupt) as error:
        result['error'] = str(error) or 'Interrupted'
    finally:
        if handle:
            if claimed:
                result['restoration']['release_result'] = usb.libusb_release_interface(handle, 0)
            if detached:
                # Binding interface 0 normally also restores streaming interface 1.
                for interface in (0, 1):
                    if original.get(interface) and usb.libusb_kernel_driver_active(handle, interface) == 0:
                        result['restoration'][f'attach_{interface}_result'] = usb.libusb_attach_kernel_driver(handle, interface)
                result['restoration']['video_driver_active'] = {
                    i: usb.libusb_kernel_driver_active(handle, i) for i in (0, 1)}
                result['restoration']['restored'] = all(
                    result['restoration']['video_driver_active'][i] == 1
                    for i in original if original[i])
            usb.libusb_close(handle)
        usb.libusb_exit(context)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
