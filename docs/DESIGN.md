# Chroma Bridge: design (APPROVED by the user 2026-10-22 18:54, including all five "my calls")

## Intent
Make the user's non-Razer RGB (Gigabyte B550 UD AC ARGB/RGB headers and Patriot Viper Steel RAM) show up
**inside Razer Chroma Studio as ports on a Razer Chroma ARGB controller**, with per-LED layouts.
The real Razer box is shorted and retired. The strips and fans get signal and power from the motherboard
headers. Proven by the spike: Synapse accepts a software fake box, and the RAM followed a Studio effect.

Possibly useful to many Gigabyte owners later, so keep it shareable, but build it for the user's PC first.

## Goals
- Synapse shows "Razer Chroma Addressable RGB Controller" with ports whose LED counts match the real hardware.
- Chroma Studio effects (and Chroma games) drive the RAM and the motherboard headers per LED, live.
- Starts at logon with no UAC prompt after install. Recovers by itself when Synapse restarts, the PC resumes from sleep, or the bridge crashes.
- Short install and remove docs. Removing it leaves the PC as it was.

## Non-goals
- No changes to Synapse files, no hooking Razer processes, no Windows test-signing mode.
- No GUI (a config file is enough for now).
- Not other motherboard brands yet.

## Anti-goals (failure even if the lights work)
- Any SMBus traffic to 0x77 other than the proven Viper Steel writes, or any SMBus probing at all.
  A wrong probe hangs the bus until a full power-off.
- Noticeable CPU use or stutter in games.
- Leaving a dead virtual USB device or stuck processes behind when it stops.

## How it works
```
Chroma Studio -> Synapse -> Razer lighting driver -> [virtual USB: usbip-win2] -> chroma-bridge
                                                                                     |-> RAM (PawnIO SMBus 0x77)
                                                                                     '-> Gigabyte ITE USB HID (ARGB headers)
```
One process, **chroma-bridge** (Python, running as a highest-privilege scheduled task at logon):
1. Starts the fake-box USB/IP server on 127.0.0.1:3340, then runs `usbip.exe attach` itself.
2. Answers Synapse with the protocol table found in the spike (see README-HANDOFF.md). Port LED counts come from the config.
3. Splits each 320-byte frame by channel and forwards it:
   - **Port 1 → RAM**: 5 LEDs via PawnIO (the same writes as `ram_writer.ps1`, now in-process). If both sticks
     turn out to be separately addressable, 10.
   - **Ports 2–5 → Gigabyte headers**: each ARGB header gets its LEDs. A 12V RGB header (single colour) takes the average of its port.
   - Port 6: spare.
4. Rate limit: at most 30 updates/s per target, and only when the colours changed.

## Config (`chroma-bridge.json`)
Port → target and LED count, e.g. `{"port1": {"target":"ram"}, "port2": {"target":"gigabyte", "header":"D_LED1", "leds": 60}}`.
LED counts are set by hand: ARGB strips can't report their length. These counts are what the fake box reports to Synapse.
Optional fallback colour for when Chroma stops.

## Recovery
- Synapse restarts: the virtual device stays attached and Synapse re-runs its handshake. Nothing to do.
- Frames stop (Synapse closed, Chroma NOT_LIVE): after 5 s, hold the last colours (or the fallback colour, if configured).
- Sleep/resume or usbip connection dropped: detach and re-attach automatically.
- Crash: the scheduled task restarts it (restart-on-failure, 3 tries per minute).
- Shutdown: detach the virtual device cleanly.

## Install / remove
- Install script (one UAC prompt): checks usbip-win2 and PawnIO are present, copies files to `C:\Tools\ChromaBridge\`,
  registers the scheduled task. The Razer 0F1F driver is installed automatically by Synapse.
- Remove script: stops the task, detaches, deletes the task and the folder. usbip-win2 and PawnIO stay unless the user removes them.

## Decided by the user
Software-only approach (fake Razer box via virtual USB). ESP32/Pi only as a fallback. RAM included. Retire the Razer box.

## My calls (the user to check)
1. **Talk to the Gigabyte ITE controller directly** (its USB HID protocol, as implemented in OpenRGB's Gigabyte RGB Fusion 2 USB code),
   instead of running OpenRGB as a server alongside. That means one process and no OpenRGB bugs, at the cost of porting about 150 lines.
2. **Python**, same as the spike: already installed, no build step. It talks to PawnIO and HID through Windows DLLs.
3. **Hold the last colours** when Chroma stops, rather than switching off.
4. **Generated serial** instead of the real box's serial, so a public version doesn't carry the user's.
   This would make Synapse treat it as a new box, so the port names and counts get set once.
5. The OpenRGB config stays as it is. OpenRGB must not run at the same time (it would fight over the ITE controller and the SMBus).

## Left to the builder
Exact ITE report format, the logging format, and file layout.
## Open before building
Which header each strip or fan is on, and its LED count (the user, or read it from the old Synapse port names: 16/44/56/60).


