# Chroma Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the working spike into one elevated background program that emulates the Razer Chroma ARGB controller and forwards Chroma Studio frames to Viper Steel RAM and Gigabyte RGB Fusion 2 USB headers, started at logon.

**Architecture:** A Python package `chroma_bridge` with pure, unit-tested modules (Razer protocol, USB/IP server, config, frame router) and two hardware targets behind small transport interfaces (PawnIO SMBus, Windows HID). These interfaces are faked in tests and smoke-tested on real hardware. `main.py` supervises: it starts the server, runs `usbip.exe attach`, re-attaches when the connection drops, and detaches on exit. A scheduled task (highest privileges, at logon) runs it.

**Tech Stack:** Python 3.11+ standard library only (ctypes for `PawnIOLib.dll`, `hid.dll` and `setupapi.dll`), pytest for tests, PowerShell for install/remove.

**Spec:** `docs/DESIGN.md`. Protocol facts: `README.md` (protocol table and frame format). The spike being replaced: `spike/fakebox.py`, `spike/ram_writer.ps1`.

## Global Constraints
- No Synapse patching or hooking, and no test-signing mode.
- SMBus: only ever `write_byte_data(0x77, 0x17+led, R)` and `write_byte_data(0x77, G, B)`. No reads, no probes, no other address. Hold the `Global\Access_SMBUS.HTP.Method` mutex around every transaction (200 ms timeout; skip the frame if it can't be acquired).
- License is MIT: do not copy OpenRGB (GPL-2.0) code. Write the Gigabyte protocol from a protocol note (Task 5).
- USB/IP listens only on `127.0.0.1`, default port 3340.
- At most 30 updates/s per target, and only when the colours changed.
- No real device serial in the repo. Generate one on first run and store it in the config.
- OpenRGB must not run alongside. On startup, refuse to open the Gigabyte device if `OpenRGB.exe` is running, and log why.

## Review Focus
1. Synapse sends a frame for a channel whose LED count exceeds what the target supports. Expected: extra LEDs dropped, no exception (test in Task 6).
2. Synapse restarts mid-stream or frames stop. Expected: hold the last colours; no crash; resume when frames return (Task 6).
3. The usbip connection drops (sleep/resume, usbip-win2 restart). Expected: re-attach within 10 s (Task 7).
4. The SMBus mutex is held by another tool (HWiNFO). Expected: skip that RAM update, never block the USB/IP thread (Task 4).
5. The Gigabyte controller is absent or busy (OpenRGB running, unplugged). Expected: log once, keep serving Synapse and the RAM (Tasks 5 and 7).

---

### Task 1: Razer protocol module

**Files:**
- Create: `chroma_bridge/__init__.py`, `chroma_bridge/razer.py`
- Test: `tests/test_razer.py`

**Interfaces:**
- Produces: `crc(report: bytes) -> int`; `class RazerDevice(serial: bytes, led_counts: list[int])` with `handle(request: bytes) -> bytes` (90-byte reply); `parse_frame(data: bytes) -> tuple[int, list[tuple[int,int,int]]] | None` returning (channel 0–5, RGB list) or None for short or unknown reports.

- [ ] **Step 1: Write failing tests**
```python
def test_crc_matches_capture():  # request 00 0c 0000 00 03 00 86 -> crc 0x85
    r = bytes.fromhex('000c000000030086') + bytes(82); assert crc(r) == 0x85
def test_power_supply_status_is_on():      assert reply(0x00, 0xb7)[8] == 0x01
def test_power_security_is_clear():        assert reply(0x07, 0x8e)[8:11] == b'\0\0\0'
def test_region_list():                    assert reply(0x0f, 0x80)[8:14] == bytes(range(0x1a, 0x20))
def test_hw_info_counts():  # led_counts [10,60,0,0,0,0]
    assert reply(0x00, 0xb5)[8:22] == bytes([3,6, 0,10, 0,60]) + bytes(8)
def test_stripe_led_number(): assert reply(0x0f, 0x88)[8:21] == bytes([6, 0x1a,10, 0x1b,60, 0x1c,0, 0x1d,0, 0x1e,0, 0x1f,0])
def test_serial(): assert reply(0x00, 0x82)[8:23] == b'IO0000CHROMABR1'
def test_reply_status_and_crc(): r = reply(0x00, 0x86); assert r[0] == 0x02 and r[88] == crc(r)
def test_unknown_command_echoes_args(): ...  # 0f/04 args 01 1a 51 come back unchanged
def test_parse_frame_channel0(): assert parse_frame(bytes([4,0,0,0,0x4f]) + bytes([1,2,3]*80) + bytes(75))[1][0] == (1,2,3)
def test_parse_frame_channel5(): assert parse_frame(bytes([0x84,5,5,0,0x4f]) + bytes(315))[0] == 5
def test_parse_frame_short_returns_none(): assert parse_frame(b'\x04\x00') is None
```
(`reply(cls, id)` is a test helper that builds a request with that class/id and calls `RazerDevice(b'IO0000CHROMABR1', [10,60,0,0,0,0]).handle`.)

- [ ] **Step 2:** `python -m pytest tests/test_razer.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `razer.py` from the README protocol table and `spike/fakebox.py` (`razer_command`, the frame header). `0f/08` stores the counts Synapse sends but doesn't change what `0f/88`/`00/b5` report (those come from the config).
- [ ] **Step 4:** Run the tests again. Expected: PASS.
- [ ] **Step 5:** Commit `feat: razer protocol module`.

### Task 2: USB/IP device server

**Files:**
- Create: `chroma_bridge/usbip_server.py`
- Test: `tests/test_usbip_server.py`

**Interfaces:**
- Consumes: `RazerDevice.handle`.
- Produces: `class UsbipServer(device: RazerDevice, on_frame: Callable[[bytes], None], port: int = 3340)` with `start() -> None` (background thread), `stop() -> None`, `connected: threading.Event` (set while imported), `disconnected: threading.Event`.

- [ ] **Step 1: Write failing tests** against a real socket on an ephemeral port:
  - `test_devlist_reports_1532_0f1f`: OP_REQ_DEVLIST reply has 1 device, idVendor 0x1532, idProduct 0x0F1F, busid `1-1`.
  - `test_import_then_get_device_descriptor`: after OP_REQ_IMPORT, a CMD_SUBMIT GET_DESCRIPTOR(device, 18) returns `120100020000004032151f0f000201020001`.
  - `test_set_report_if1_calls_on_frame`: SET_REPORT (bmRequestType 0x21, bRequest 9, wIndex 1) with 320 bytes → `on_frame` called with those bytes.
  - `test_unlink_pending_interrupt_in`: submit an IN URB on ep1, unlink it → RET_UNLINK status -104.
  - `test_client_close_sets_disconnected`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Move the descriptor constants, `serve_urbs`, `handle` and `control` from `spike/fakebox.py`, removing the spike-only code (the power cycling and the ram_frame file).
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: usbip device server`.

### Task 3: Config

**Files:**
- Create: `chroma_bridge/config.py`, `chroma-bridge.example.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `@dataclass Port(target: str, leds: int, header: str | None)` with target in {"ram","gigabyte","none"}; `@dataclass Config(serial: bytes, ports: list[Port], usbip_port: int, usbip_exe: str, pawnio_dir: str, max_hz: int)`; `load(path: Path) -> Config` (creates the serial on first run and writes it back); `Config.led_counts() -> list[int]` (6 entries).

- [ ] **Step 1: Write failing tests:** `test_example_loads`, `test_first_run_generates_15_char_serial_and_persists`, `test_leds_over_80_rejected` (ValueError mentioning the port), `test_more_than_6_ports_rejected`, `test_ram_port_defaults_to_5_leds`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. The serial format is `"IO"` + 13 uppercase alphanumerics from `secrets`. The example config: port1 = ram (5 LEDs), port2 = gigabyte `D_LED1` 60, port3 = gigabyte `D_LED2` 60, the rest none. Defaults: `max_hz` 30, `usbip_exe` `C:\Program Files\USBip\usbip.exe`, `pawnio_dir` `C:\Tools\OpenRGB\OpenRGB Windows 64-bit`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: config`.

### Task 4: RAM target (Viper Steel over PawnIO)

**Files:**
- Create: `chroma_bridge/targets/__init__.py`, `chroma_bridge/targets/ram.py`
- Test: `tests/test_ram.py`

**Interfaces:**
- Produces: `class SmbusTransport(Protocol): write_byte_data(addr:int, cmd:int, val:int) -> int; select_port(port:int) -> None`; `class PawnioSmbus(pawnio_dir: str)` implementing it; `class Lockable(Protocol): acquire(timeout_ms:int) -> bool; release() -> None`; `class NamedMutex(name: str)` implementing it; `class RamTarget(bus: SmbusTransport, mutex: Lockable)` with `show(colors: list[tuple[int,int,int]]) -> bool` (False when skipped).

- [ ] **Step 1: Write failing tests** with a fake bus that records calls:
  - `test_writes_exact_steel_sequence`: colours [(1,2,3)]*5 → calls are select_port(0), then for led 0..4: (0x77, 0x17+led, 1), (0x77, 2, 3).
  - `test_only_address_0x77_ever_used`.
  - `test_fewer_than_5_colors_pads_with_last`.
  - `test_mutex_busy_skips_without_writing` (the fake mutex returns False → show() is False, no calls).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `PawnioSmbus` mirrors `spike/ram_writer.ps1` (`pawnio_open`/`pawnio_load SmbusPIIX4.bin`, `ioctl_piix4_port_sel [0]`, `ioctl_smbus_xfer [addr,0,cmd,2,val,0,0,0,0]`) via ctypes. The mutex is the named `Global\Access_SMBUS.HTP.Method` (ctypes `CreateMutexW`/`WaitForSingleObject` 200 ms/`ReleaseMutex`).
- [ ] **Step 4:** Run. Expected: PASS. Hardware smoke test, elevated: `python -m chroma_bridge.targets.ram --test` sets the RAM blue for 2 s, then red. the user looks at it.
- [ ] **Step 5:** Commit `feat: viper steel ram target`.

### Task 5: Gigabyte RGB Fusion 2 USB target

**Files:**
- Create: `docs/gigabyte-fusion2-usb-protocol.md`, `chroma_bridge/targets/hid.py`, `chroma_bridge/targets/gigabyte.py`
- Test: `tests/test_gigabyte.py`

**Interfaces:**
- Produces: `class HidDevice(Protocol): send_feature(report: bytes) -> None; get_feature(report_id: int, size: int) -> bytes`; `open_hid(vid: int, pid_set: set[int]) -> HidDevice | None` (ctypes, `hid.dll` + `setupapi.dll`); `class GigabyteTarget(dev: HidDevice, headers: dict[str,int])` with `init() -> str` (product name from the info report) and `show(header: str, colors: list[tuple[int,int,int]]) -> None`.

- [ ] **Step 1: Write the protocol note** (no code copied) from reading OpenRGB's `GigabyteRGBFusion2USBController` and running `--probe`. Cover: VID `0x048D` and the PIDs to match; the 64-byte feature report with report ID `0xCC`; the info request (`0xCC 0x60`) and the fields read back (product name, D_LED header count); enabling per-LED direct mode for an ARGB header; the per-LED data report for D_LED1/D_LED2 (report IDs, offset/count fields, maximum LEDs per packet); the LED byte order (calibration); and how the 12V RGB headers take one colour. Mark each fact as verified on hardware or read from source.
- [ ] **Step 2: Write failing tests** with a fake HidDevice, values from the protocol note: `test_init_reads_product_name` (the fake returns an info report containing "B550 UD AC-Y1"), `test_show_d_led1_packets` (60 LEDs → expected packet count and first packet bytes), `test_show_truncates_to_header_max`, `test_byte_order_applied`, `test_rgb_header_uses_average`.
- [ ] **Step 3:** Run. Expected: FAIL.
- [ ] **Step 4:** Implement `hid.py` and `gigabyte.py`. `python -m chroma_bridge.targets.gigabyte --probe` prints the info report fields (read-only).
- [ ] **Step 5:** Run the tests. Expected: PASS. Hardware smoke test with OpenRGB closed: `--test D_LED1 60` runs a red→green→blue chase. the user confirms which physical strip moved, and the header mapping goes into the config.
- [ ] **Step 6:** Commit `feat: gigabyte fusion2 usb target`.

### Task 6: Frame router

**Files:**
- Create: `chroma_bridge/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `parse_frame`, `Config.ports`, `RamTarget.show`, `GigabyteTarget.show`.
- Produces: `class Router(config: Config, targets: dict[str, object], clock=time.monotonic)` with `on_frame(data: bytes) -> None` (called on the USB/IP thread; must not block) and `run(stop: threading.Event) -> None` (the worker loop that pushes to targets at most `max_hz` per target, only on change).

- [ ] **Step 1: Write failing tests:** `test_channel_maps_to_configured_target`, `test_unchanged_colors_not_resent`, `test_rate_limited_to_max_hz` (fake clock), `test_more_leds_than_configured_are_dropped`, `test_frames_stop_holds_last_colors` (no target call and no exception after 5 s of silence), `test_resumes_after_gap`, `test_target_exception_logged_and_other_targets_continue`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `on_frame` stores the latest colours per channel under a lock. `run` drains them on its own thread.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat: frame router`.

### Task 7: Supervisor (`python -m chroma_bridge`)

**Files:**
- Create: `chroma_bridge/__main__.py`, `chroma_bridge/attach.py`
- Test: `tests/test_attach.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `attach(exe: str, port: int) -> bool`, `detach_all(exe: str) -> None` (both via `subprocess.run`, with the output logged); `main(argv) -> int`.

- [ ] **Step 1: Write failing tests** with a fake `subprocess.run`: `test_attach_command_line` (`[exe, '-t', '3340', 'attach', '-r', '127.0.0.1', '-b', '1-1']`), `test_detach_all_command_line`, `test_reattach_after_disconnect` (when the server's `disconnected` is set, attach is called again within 10 s of the fake clock), `test_missing_gigabyte_device_still_runs_ram`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `main`. Load the config from `%ProgramData%\ChromaBridge\chroma-bridge.json`, log to `%ProgramData%\ChromaBridge\bridge.log` (rotating, 1 MB × 3), open the targets (a failure is logged once and that target skipped), start the server and the router, then attach. Loop: if disconnected, `detach_all` + `attach`. On Ctrl+C or SIGTERM, `detach_all`. If `OpenRGB.exe` is running, skip the Gigabyte target and log it.
- [ ] **Step 4:** Run. Expected: PASS. Manual check, elevated: `python -m chroma_bridge`. Synapse shows the box with the configured LED counts, a Studio effect drives the RAM and the headers, and quitting and reopening Synapse resumes on its own.
- [ ] **Step 5:** Commit `feat: supervisor`.

### Task 8: Install / uninstall and docs

**Files:**
- Create: `install.ps1`, `uninstall.ps1`; Modify: `README.md`

- [ ] **Step 1:** `install.ps1` (self-elevates once): checks Python, usbip-win2 and PawnIO; copies the package to `C:\Tools\ChromaBridge\`; creates `%ProgramData%\ChromaBridge\chroma-bridge.json` from the example if missing; registers the scheduled task `ChromaBridge` (At logon of the current user, RunLevel Highest, restart on failure 3×/1 min, no execution time limit) running `pythonw -m chroma_bridge`; starts it.
- [ ] **Step 2:** `uninstall.ps1`: stops and deletes the task, runs `usbip.exe detach -a`, deletes `C:\Tools\ChromaBridge\`, and asks before deleting `%ProgramData%\ChromaBridge\`.
- [ ] **Step 3: Verify:** install → log off and on → a Studio effect runs on the RAM and headers with no UAC prompt. Uninstall → the device disappears from Synapse, and the task and folder are gone.
- [ ] **Step 4:** README: Install, Configure (port mapping, LED counts), Remove, Troubleshooting (OpenRGB conflict, Hyper-V port range, Synapse static quick effect skipped). Move `spike/` under `docs/spike/` for reference.
- [ ] **Step 5:** Commit `feat: installer, uninstaller, docs`.
