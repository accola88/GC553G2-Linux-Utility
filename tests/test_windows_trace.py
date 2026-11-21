import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import gc553g2
from gc553g2_support import gc553g2_rgb as rgb


class WindowsTraceTests(unittest.TestCase):
    def test_hdmi_timing_distinguishes_sdr_and_hdr(self):
        # PS5 status replies 2662 and 4198 from the HDR/SDR trace.
        sdr = "a12a00003000ca0830117008000f520080016917ac34000a00580001020002000000401f78255d237e8cc10e7c"
        hdr = "a12a00003000ca0830117008000f520080016917ac34100a00580001020002090600401f78255d23b8dec10ed1"
        for raw, detected in ((sdr, False), (hdr, True)):
            with self.subTest(hdr=detected):
                data = gc553g2.at.decode_response({"response_hex": raw}, allow_short=True)
                timing = gc553g2.decode_hdmi_timing(data)
                self.assertEqual(timing, {
                    "width": 3840, "height": 2160, "refresh_hz": 59.93,
                    "interlaced": False, "hdr_detected": detected,
                })

    def test_auto_vrr_requires_the_matching_device_echo(self):
        on = "a10600000100000058"
        off = "a10600000000000059"
        for enabled, request, reply in (
            (True, "a10700008900000001ce", on),
            (False, "a10700008900000000cf", off),
        ):
            with self.subTest(enabled=enabled):
                with patch.object(gc553g2.at, "exchange", return_value={"response_hex": reply}) as exchange:
                    gc553g2.at.set_auto_vrr("usb", "handle", enabled)
                    exchange.assert_called_once_with("usb", "handle", bytes.fromhex(request))

        for reply in (off, "a1005f", "a10600000100000000"):
            with self.subTest(reply=reply):
                with patch.object(gc553g2.at, "exchange", return_value={"response_hex": reply}):
                    with self.assertRaises(RuntimeError):
                        gc553g2.at.set_auto_vrr("usb", "handle", True)

    def test_native_rainbow_wave_uses_the_vendor_hid_interface(self):
        @contextmanager
        def open_node(node):
            self.assertEqual(node, "vendor-node")
            yield Path("/dev/vendor-node"), "hid-file"

        find = patch.object(rgb, "find_rgb_node", return_value="vendor-node")
        open_hid = patch.object(rgb, "open_rgb_node", open_node)
        send = patch.object(rgb, "feature_report")
        with find as find_node, open_hid, send as feature_report:
            result = rgb.set_native_effect("capture-device", "rainbow-wave")

        find_node.assert_called_once_with("capture-device", rgb.NATIVE_DESCRIPTOR_SHA256, "native RGB")
        feature_report.assert_called_once_with("hid-file", bytes.fromhex("7520001004010501" + "00" * 24))
        self.assertEqual(result, {
            "hidraw": "/dev/vendor-node", "effect_requested": "rainbow-wave", "report_sent": True,
        })
