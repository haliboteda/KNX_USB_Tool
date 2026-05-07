"""
KNX TP Ping-Pong — PC side (Windows 11, KNX/USB HID converter).

Listens on GA 0/0/1 (OpenPLC sends here).
On each received telegram: counter + 1, sends back on GA 0/0/2.

DPT: 5.010 — raw 1-byte unsigned value (0–255, wraps).

Requirements (installed by run.bat via uv):
    xknx >= 3.0
    hid   (bundled hidapi DLL on Windows)

The KNX/USB converter must be visible in Windows Device Manager
as a HID-class device (no COM port — just a USB HID entry).
ETS does NOT need to be running.
"""

import asyncio
import sys
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import Telegram, GroupAddress
from xknx.telegram.apci import GroupValueWrite
from xknx.dpt import DPTArray

GA_FROM_PLC = "0/0/1"   # OpenPLC writes here  →  we read
GA_TO_PLC   = "0/0/2"   # We write here        →  OpenPLC reads

rx_count = 0


async def main() -> None:
    global rx_count

    async def on_telegram(telegram: Telegram) -> None:
        global rx_count

        # Ignore anything not addressed to our GA
        if str(telegram.destination_address) != GA_FROM_PLC:
            return
        if not isinstance(telegram.payload, GroupValueWrite):
            return

        raw = telegram.payload.value
        if not isinstance(raw, DPTArray) or len(raw.value) != 1:
            print(f"  [WARN] unexpected payload: {raw!r}", flush=True)
            return

        val = raw.value[0]
        reply_val = (val + 1) & 0xFF
        rx_count += 1

        print(f"  #{rx_count:5d}  RX {val:3d}  →  TX {reply_val:3d}", flush=True)

        reply = Telegram(
            destination_address=GroupAddress(GA_TO_PLC),
            payload=GroupValueWrite(DPTArray((reply_val,))),
        )
        await xknx.telegrams.put(reply)

    xknx = XKNX(
        telegram_received_cb=on_telegram,
        connection_config=ConnectionConfig(connection_type=ConnectionType.USB),
    )

    print("Connecting to KNX/USB HID device...")
    try:
        async with xknx:
            print(f"Connected.  Listening on GA {GA_FROM_PLC} ...")
            print(f"Press Ctrl+C to stop.\n")
            await asyncio.sleep(float("inf"))
    except KeyboardInterrupt:
        print(f"\nStopped after {rx_count} exchanges.")
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        print("Check that the KNX/USB adapter is plugged in and no other", file=sys.stderr)
        print("software (ETS) is holding the device open.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
