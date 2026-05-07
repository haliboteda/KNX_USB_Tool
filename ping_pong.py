# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "hidapi",
# ]
# ///

"""
KNX TP ping-pong test for a Windows KNX/USB HID converter.

Default flow:
  OpenPLC writes raw bytes to GA 0/0/1.
  This tool waits for GroupValueWrite telegrams, appends a loop counter byte,
  then writes the reply to GA 0/0/2 and waits again.

Useful KNX_MODE values:
  append_counter: RX [AA] -> TX [AA 01], then RX [...] -> TX [... 02]
  increment:      RX [00] -> TX [01]
  append:         RX [AA] -> TX [AA KNX_APPEND_BYTE]
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Final

import hid


GA_FROM_PLC: Final = os.getenv("KNX_RX_GA", "0/0/1")
GA_TO_PLC: Final = os.getenv("KNX_TX_GA", "0/0/2")
MODE: Final = os.getenv("KNX_MODE", "append_counter").strip().lower()
APPEND_BYTE: Final = int(os.getenv("KNX_APPEND_BYTE", "1"), 0) & 0xFF
MAX_APPEND_PAYLOAD: Final = int(os.getenv("KNX_MAX_APPEND_PAYLOAD", "14"), 0)
USB_VID: Final = os.getenv("KNX_USB_VID")
USB_PID: Final = os.getenv("KNX_USB_PID")
DEBUG_ALL: Final = os.getenv("KNX_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

REPORT_ID = 0x01
PACKET_INFO_ALL_IN_ONE = 0x13
USB_PROTOCOL_VERSION = 0x00
USB_HEADER_LENGTH = 0x08
USB_PROTOCOL_ID = 0x01
USB_EMI_ID_CEMI = 0x03
USB_MANUFACTURER_CODE = (0x00, 0x00)
HID_REPORT_SIZE = 64

CEMI_L_DATA_REQ = 0x11
CEMI_L_DATA_CON = 0x2E
CEMI_L_DATA_IND = 0x29
GROUP_VALUE_WRITE_MASK = 0x3C0
GROUP_VALUE_WRITE = 0x080

rx_count = 0


@dataclass(frozen=True)
class KnxTelegram:
    message_code: int
    source: int
    destination: int
    destination_is_group: bool
    apci: int
    payload: tuple[int, ...]


def format_bytes(data: tuple[int, ...] | bytes | bytearray) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def parse_group_address(address: str) -> int:
    parts = [int(part, 10) for part in address.split("/")]
    if len(parts) != 3:
        raise ValueError(f"group address must be main/middle/sub, got {address!r}")
    main, middle, sub = parts
    if not (0 <= main <= 31 and 0 <= middle <= 7 and 0 <= sub <= 255):
        raise ValueError(f"group address out of range: {address!r}")
    return (main << 11) | (middle << 8) | sub


def format_group_address(address: int) -> str:
    return f"{(address >> 11) & 0x1F}/{(address >> 8) & 0x07}/{address & 0xFF}"


def format_individual_address(address: int) -> str:
    return f"{(address >> 12) & 0x0F}.{(address >> 8) & 0x0F}.{address & 0xFF}"


def normalize_hid_report(report: list[int]) -> bytes:
    data = bytes(report)
    if not data:
        return b""
    if data[0] != REPORT_ID:
        data = bytes((REPORT_ID,)) + data
    return data


def parse_knx_usb_report(report: list[int]) -> KnxTelegram | None:
    data = normalize_hid_report(report)
    if len(data) < 3:
        return None

    if data[0] != REPORT_ID:
        return None

    packet_info = data[1]
    if (packet_info & 0x0F) != 0x03:
        if DEBUG_ALL:
            print(f"  [DBG] unsupported packet type: 0x{packet_info:02X}", flush=True)
        return None

    body_length = data[2]
    body = data[3 : 3 + body_length]
    if len(body) < USB_HEADER_LENGTH:
        return None

    protocol_version = body[0]
    header_length = body[1]
    cemi_length = (body[2] << 8) | body[3]
    protocol_id = body[4]
    emi_id = body[5]
    if (
        protocol_version != USB_PROTOCOL_VERSION
        or header_length != USB_HEADER_LENGTH
        or protocol_id != USB_PROTOCOL_ID
        or emi_id != USB_EMI_ID_CEMI
    ):
        if DEBUG_ALL:
            print(f"  [DBG] unsupported USB body: {format_bytes(body)}", flush=True)
        return None

    cemi = body[USB_HEADER_LENGTH : USB_HEADER_LENGTH + cemi_length]
    if len(cemi) < 9:
        return None

    message_code = cemi[0]
    additional_info_length = cemi[1]
    index = 2 + additional_info_length
    if len(cemi) < index + 8:
        return None

    control_2 = cemi[index + 1]
    source = (cemi[index + 2] << 8) | cemi[index + 3]
    destination = (cemi[index + 4] << 8) | cemi[index + 5]
    npdu_length = cemi[index + 6]
    apdu = cemi[index + 7 :]
    if len(apdu) < 2:
        return None

    apci = ((apdu[0] & 0x03) << 8) | apdu[1]
    if npdu_length <= 1:
        payload = (apdu[1] & 0x3F,)
    else:
        payload = tuple(apdu[2 : 2 + (npdu_length - 1)])

    return KnxTelegram(
        message_code=message_code,
        source=source,
        destination=destination,
        destination_is_group=bool(control_2 & 0x80),
        apci=apci,
        payload=payload,
    )


def build_reply(data: tuple[int, ...], counter: int) -> tuple[int, ...] | None:
    if MODE == "append_counter":
        counter_byte = counter & 0xFF
        if len(data) >= MAX_APPEND_PAYLOAD:
            return (*data[: MAX_APPEND_PAYLOAD - 1], counter_byte)
        return (*data, counter_byte)

    if MODE == "increment":
        if len(data) != 1:
            print(f"  [WARN] increment mode expects 1 byte, got {len(data)}: {format_bytes(data)}", flush=True)
            return None
        return ((data[0] + 1) & 0xFF,)

    if MODE == "append":
        if len(data) >= MAX_APPEND_PAYLOAD:
            print(
                f"  [WARN] append mode payload already {len(data)} bytes; "
                f"not sending a frame larger than {MAX_APPEND_PAYLOAD}",
                flush=True,
            )
            return None
        return (*data, APPEND_BYTE)

    print(f"  [ERROR] unsupported KNX_MODE={MODE!r}; use append_counter, increment, or append", flush=True)
    return None


def build_group_write_report(destination: int, payload: tuple[int, ...]) -> bytes:
    if not payload:
        raise ValueError("payload must not be empty")
    if len(payload) > 14:
        raise ValueError("payload too large for this simple KNX TP test")

    control_1 = 0x96
    control_2 = 0xE0
    source = 0x0000
    npdu_length = 1 + len(payload)
    apdu = bytes((0x00, 0x80, *payload))

    cemi = bytes(
        (
            CEMI_L_DATA_REQ,
            0x00,
            control_1,
            control_2,
            (source >> 8) & 0xFF,
            source & 0xFF,
            (destination >> 8) & 0xFF,
            destination & 0xFF,
            npdu_length,
        )
    ) + apdu

    usb_body = bytes(
        (
            USB_PROTOCOL_VERSION,
            USB_HEADER_LENGTH,
            (len(cemi) >> 8) & 0xFF,
            len(cemi) & 0xFF,
            USB_PROTOCOL_ID,
            USB_EMI_ID_CEMI,
            *USB_MANUFACTURER_CODE,
        )
    ) + cemi

    report = bytes((REPORT_ID, PACKET_INFO_ALL_IN_ONE, len(usb_body))) + usb_body
    return report.ljust(HID_REPORT_SIZE, b"\x00")


def parse_optional_hex(value: str | None) -> int | None:
    if not value:
        return None
    return int(value, 0)


def find_knx_usb_device() -> dict:
    expected_vid = parse_optional_hex(USB_VID)
    expected_pid = parse_optional_hex(USB_PID)
    devices = hid.enumerate()

    candidates = []
    for device in devices:
        name = f"{device.get('manufacturer_string') or ''} {device.get('product_string') or ''}".lower()
        usage_page = device.get("usage_page")
        vid_matches = expected_vid is None or device.get("vendor_id") == expected_vid
        pid_matches = expected_pid is None or device.get("product_id") == expected_pid
        looks_like_knx = "knx" in name or usage_page == 0xFFA0
        if vid_matches and pid_matches and looks_like_knx:
            candidates.append(device)

    if not candidates:
        print("No KNX USB HID device found. HID devices visible to Python:", file=sys.stderr)
        for device in devices:
            print(
                "  "
                f"VID={device.get('vendor_id', 0):04X} "
                f"PID={device.get('product_id', 0):04X} "
                f"usage_page={device.get('usage_page')} "
                f"{device.get('manufacturer_string') or ''} "
                f"{device.get('product_string') or ''}",
                file=sys.stderr,
            )
        sys.exit(1)

    return candidates[0]


def main() -> None:
    global rx_count

    rx_group = parse_group_address(GA_FROM_PLC)
    tx_group = parse_group_address(GA_TO_PLC)
    device_info = find_knx_usb_device()

    print("Connecting to KNX/USB HID device...")
    device = hid.device()
    try:
        device.open_path(device_info["path"])
        device.set_nonblocking(1)
    except OSError as exc:
        print(f"\nError opening KNX/USB adapter: {exc}", file=sys.stderr)
        print("Close ETS or any other software holding the USB interface, then retry.", file=sys.stderr)
        sys.exit(1)

    print("Connected.")
    print(
        "  USB device         : "
        f"VID={device_info.get('vendor_id', 0):04X} PID={device_info.get('product_id', 0):04X} "
        f"{device_info.get('manufacturer_string') or ''} {device_info.get('product_string') or ''}"
    )
    print(f"  RX group address   : {GA_FROM_PLC}")
    print(f"  TX group address   : {GA_TO_PLC}")
    print(f"  mode               : {MODE}")
    if MODE == "append_counter":
        print("  reply rule         : append loop counter byte to each received payload")
    elif MODE == "append":
        print(f"  append byte        : 0x{APPEND_BYTE:02X}")
    elif MODE == "increment":
        print("  reply rule         : increment 1-byte payload")
    print("\nWaiting for OpenPLC telegrams. Press Ctrl+C to stop.\n")

    try:
        while True:
            report = device.read(HID_REPORT_SIZE, 100)
            if not report:
                continue

            if DEBUG_ALL:
                print(f"  [RAW] {format_bytes(report)}", flush=True)

            telegram = parse_knx_usb_report(report)
            if telegram is None:
                continue

            if DEBUG_ALL:
                dest = format_group_address(telegram.destination) if telegram.destination_is_group else format_individual_address(telegram.destination)
                print(
                    f"  [DBG] mc=0x{telegram.message_code:02X} "
                    f"src={format_individual_address(telegram.source)} dst={dest} "
                    f"apci=0x{telegram.apci:03X} data=[{format_bytes(telegram.payload)}]",
                    flush=True,
                )

            if telegram.message_code != CEMI_L_DATA_IND:
                continue
            if not telegram.destination_is_group or telegram.destination != rx_group:
                continue
            if (telegram.apci & GROUP_VALUE_WRITE_MASK) != GROUP_VALUE_WRITE:
                continue

            next_count = rx_count + 1
            reply = build_reply(telegram.payload, next_count)
            if reply is None:
                continue

            tx_report = build_group_write_report(tx_group, reply)
            written = device.write(tx_report)
            rx_count = next_count
            print(
                f"  loop={rx_count:05d}  "
                f"{format_individual_address(telegram.source)} -> {GA_FROM_PLC} "
                f"RX [{format_bytes(telegram.payload)}]  ->  {GA_TO_PLC} TX [{format_bytes(reply)}] "
                f"({written} bytes)",
                flush=True,
            )
            time.sleep(0.02)
    except KeyboardInterrupt:
        print(f"\nStopped after {rx_count} exchanges.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
