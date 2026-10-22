# Chroma Bridge

Drive **non-Razer RGB from Razer Chroma Studio**, per LED, as if it were plugged into a Razer Chroma
Addressable RGB Controller.

Chroma Bridge emulates that controller (USB `1532:0F1F`, internal codename "Glitter") **in software**. A small
USB/IP server pretends to be the box, and [usbip-win2](https://github.com/vadimgrn/usbip-win2) attaches it as a virtual
USB device. Synapse loads Razer's own driver for it, shows its 6 ports in Chroma Studio, and streams per-LED frames
to it. Chroma Bridge forwards those frames to hardware Razer doesn't support.

No Synapse patching, no hooking, no Windows test-signing mode.

> **Status: working proof of concept.** On a Gigabyte B550 board with Patriot Viper Steel RGB DDR4, Synapse 4 accepts the
> virtual controller (no 12V or overcurrent warnings, Port 1 shows its LEDs), and a Chroma Studio effect drives the RAM live.
> Forwarding to motherboard ARGB headers (Gigabyte ITE RGB Fusion 2 USB controller) is the next step.

## How it works
```
Chroma Studio -> Synapse 4 -> Razer lighting driver -> virtual USB (usbip-win2) -> fakebox.py
                                                                                   '-> ram_frame.bin -> ram_writer.ps1 -> RAM (SMBus via PawnIO)
```

## Try the spike (Windows 11, Synapse 4)
Requirements: Python 3.11+, usbip-win2 (`winget install vadimgrn.usbip-win2`). For the RAM writer: PawnIO, plus
OpenRGB 1.x portable (for `PawnIOLib.dll` and `SmbusPIIX4.bin`); set the path at the top of `ram_writer.ps1`.

1. `python spike\fakebox.py`. It listens on `127.0.0.1:3340`, not the usbip default 3240, which Hyper-V/WinNAT often reserves.
2. In an **admin** PowerShell: `& 'C:\Program Files\USBip\usbip.exe' -t 3340 attach -r 127.0.0.1 -b 1-1`
3. Synapse shows "Razer Chroma Addressable RGB Controller". Port 1 reports 10 LEDs (edit `LED_COUNTS` in `fakebox.py`).
4. Apply a **Chroma Studio** effect that includes the controller. Synapse's own "static" quick effect is skipped for this
   device, so use Studio. `fakebox.log` shows `FRAME` lines.
5. Optional, for Viper Steel RGB RAM: run `spike\ram_writer.ps1` elevated. Port 1's first 5 LEDs go to the RAM.

To remove: `usbip.exe detach -a`, then stop `fakebox.py`.

## Protocol notes (what Synapse needs to hear)
Commands are standard 90-byte Razer feature reports on interface 0. Each reply is the request with status `02`.
Names come from Synapse's own command table.

| class/id | Name | Reply that works |
|---|---|---|
| 00/82 | Get Serial | 15-char serial |
| 00/b7 | Get Device External Power Supply Status | `01`. `00` gives "12V connection required" |
| 07/8e | Get Power Security Status | `00 00 00`. Anything else gives "OVERCURRENT PROTECTION ENABLED" |
| 00/87 | (IC data) | `01 00 08 00` |
| 0f/80 | Get Chroma Region ID List | `1a 1b 1c 1d 1e 1f` (ports = regions 26–31) |
| 00/b5 | Get Device HW Info | `03 06`, then a 16-bit big-endian physical LED count per port (port enabled when > 0) |
| 0f/88 | Get Chroma Stripe LED Number | `06`, then (region id, LED count) pairs |
| 0f/08 | Set Chroma Stripe LED Number | echo |

**LED frames** arrive as 320-byte feature reports on interface 1 (`hid.sendFeatureReportInBatch`):
`[04 for ch 0–3 / 84 for ch 4–5, ch, ch, 00, last LED index, R,G,B, R,G,B, ...]`, about 150 frames/s across channels.

`tools/` has a USBPcap capture script and a small decoder for USBPcap `.pcap` files.

## Roadmap
- Forward ports to Gigabyte RGB Fusion 2 USB (ITE) ARGB/RGB headers directly, using the protocol as implemented in OpenRGB.
- A single elevated process with a config file (port to target, LED counts), started at logon as a scheduled task, that recovers from
  Synapse restarts and sleep/resume, with install and uninstall scripts.

## Safety
- **SMBus:** `ram_writer.ps1` only sends the known Viper Steel writes to `0x77`. Probing `0x77` with other vendors' DRAM
  detectors can hang the AMD SMBus until a full power-off. Don't run OpenRGB's DRAM detection alongside.
- This is an unofficial, reverse-engineered interoperability project, not affiliated with or endorsed by Razer, Gigabyte or
  Patriot. Synapse updates may break it.
