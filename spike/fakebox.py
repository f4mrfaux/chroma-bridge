"""THROWAWAY SPIKE: a USB/IP server that impersonates the Razer Chroma ARGB controller (1532:0F1F).

Attach with:  usbip.exe attach -r 127.0.0.1 -b 1-1
It answers Synapse's control requests from the 2026-10-22 capture, logs every Razer command,
and logs (does not forward) LED frames that arrive on interface 1.
"""
import argparse, os, socket, struct, threading, time, sys

RAM_FRAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ram_frame.bin')

DEV_DESC = bytes.fromhex('120100020000004032151f0f000201020001')
CFG_DESC = bytes.fromhex(
    '0902540003010080fa'
    '090400000103000000' '092111010001221600' '07058103080001'
    '090401000103000000' '092111010001221700' '07058203080001'
    '090402000103000000' '092111010001221600' '07058303100001')
HID_REPORT_DESC = {
    0: bytes.fromhex('050c0901a1010600ff0902150025017508955ab101c0'),
    1: bytes.fromhex('050c0901a1010600ff0902150025017508964001b101c0'),
    2: bytes.fromhex('050c0901a1010600ff090215002501750895108100c0'),
}
STRINGS = {1: 'Razer', 2: 'Razer Chroma Addressable RGB Controller'}
SERIAL = b'IO0000CHROMABR1'   # placeholder; any 15-char string works (Synapse treats a new serial as a new box)
LED_COUNTS = [10, 0, 0, 0, 0, 0]   # port 1 = the RAM (2 sticks x 5 LEDs)

BUSID = b'1-1'
SPEED_FULL = 2
EPIPE, ECONNRESET = -32, -104

log_lock = threading.Lock()
def log(*a):
    with log_lock:
        line = time.strftime('%H:%M:%S ') + ' '.join(str(x) for x in a)
        print(line, flush=True)
        with open(LOGFILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

def razer_crc(r):
    c = 0
    for b in r[2:88]:
        c ^= b
    return c

class FakeBox:
    def __init__(self, power_args, fw_args):
        # (class, id) -> (reply data_size, reply args) ; None args = echo the request's args
        self.table = {
            (0x00, 0x86): (2, b''),
            (0x00, 0x81): (2, fw_args),
            (0x00, 0x82): (0x16, SERIAL),
            (0x00, 0x84): (2, b'\x00\x00'),
            (0x00, 0x04): (None, None),
            (0x00, 0xb7): (1, b'\x01'),   # Get Device External Power Supply Status: 1 = 12V present (Synapse JS)
            (0x00, 0x87): (4, bytes.fromhex('01000800')),
            (0x07, 0x8e): (3, power_args),
            (0x00, 0xc4): (1, b'\x00'),
            # GUESS: "Get Chroma Region ID List" -> the six port regions Synapse names 26..31
            (0x0f, 0x80): (0x50, bytes(range(0x1a, 0x20))),
            # "Get Device HW Info": type 3, port count, then a 16-bit physical LED count per port (enable = count > 0)
            (0x00, 0xb5): (0x0e, bytes([3, 6]) + b''.join(n.to_bytes(2, 'big') for n in LED_COUNTS)),
            # "Get Chroma Stripe LED Number": port count then (region id, LED count) pairs
            (0x0f, 0x88): (0x0d, bytes([6]) + b''.join(bytes([0x1a + i, n]) for i, n in enumerate(LED_COUNTS))),
        }
        self.last_reply = bytes(90)
        # SPIKE: each 07/8e query gets the next candidate "12V OK" answer
        self.power_cycle = []\n        self.power_tries = 0\n        self.frames = 0
        self.last_frame_log = 0.0

    def razer_command(self, req):
        req = bytearray(req[:90].ljust(90, b'\0'))
        cls, cid, size = req[6], req[7], req[5]
        rep = bytearray(req)
        rep[0] = 0x02
        known = (cls, cid) in self.table
        rsize, args = self.table.get((cls, cid), (None, None))
        if (cls, cid) == (0x07, 0x8e) and self.power_cycle:
            args = bytes.fromhex(self.power_cycle[self.power_tries % len(self.power_cycle)])
            self.power_tries += 1
        if rsize is not None:
            rep[5] = rsize
        if args is not None:
            rep[8:88] = args.ljust(80, b'\0')[:80]
        rep[88] = razer_crc(rep)
        self.last_reply = bytes(rep)
        log(f"RAZER {'   ' if known else 'NEW'} cls={cls:02x} id={cid:02x} size={size:02x} "
            f"args={bytes(req[8:8 + max(size, 1)]).hex()} -> {bytes(rep[8:8 + max(rep[5], 1)]).hex()}")

    def led_frame(self, data):
        self.frames += 1
        # Frame header: report 04 (ch 0-3) / 84 (ch 4-5), channel, channel, 0, last LED index, then RGB triplets.
        # SPIKE: channel 0's first 5 LEDs go to the RAM via ram_frame.bin (ram_writer.ps1 runs elevated).
        if len(data) >= 20 and data[1] == 0:
            rgb = bytes(data[5:20])
            if rgb != getattr(self, 'ram_rgb', None):
                self.ram_rgb = rgb
                try:
                    with open(RAM_FRAME + '.tmp', 'wb') as f:
                        f.write(rgb)
                    os.replace(RAM_FRAME + '.tmp', RAM_FRAME)
                except OSError:
                    pass
        now = time.time()
        if now - self.last_frame_log > 1.0:
            self.last_frame_log = now
            log(f'FRAME #{self.frames} len={len(data)} head={data[:12].hex()} rgb0={data[5:20].hex()}')

    def control(self, setup, out_data):
        """Returns (status, in_data)."""
        bm, breq, wval, widx, wlen = struct.unpack('<BBHHH', setup)
        if bm == 0x80 and breq == 6:                     # GET_DESCRIPTOR (device)
            dtype, didx = wval >> 8, wval & 0xff
            if dtype == 1:
                return 0, DEV_DESC[:wlen]
            if dtype == 2:
                return 0, CFG_DESC[:wlen]
            if dtype == 3:
                if didx == 0:
                    return 0, bytes.fromhex('04030904')[:wlen]
                s = STRINGS.get(didx, STRINGS[2]).encode('utf-16-le')
                return 0, (bytes([len(s) + 2, 3]) + s)[:wlen]
            if dtype == 6:                               # device qualifier: full-speed only
                return EPIPE, b''
            log(f'GET_DESCRIPTOR unknown type {dtype:#x}')
            return EPIPE, b''
        if bm == 0x81 and breq == 6 and (wval >> 8) == 0x22:   # HID report descriptor
            return 0, HID_REPORT_DESC.get(widx & 0xff, b'')[:wlen]
        if bm == 0x00 and breq == 9:                     # SET_CONFIGURATION
            return 0, b''
        if bm == 0x01 and breq == 11:                    # SET_INTERFACE
            return 0, b''
        if bm == 0x21 and breq == 0x0a:                  # SET_IDLE
            return 0, b''
        if bm == 0x21 and breq == 0x09:                  # SET_REPORT
            iface = widx & 0xff
            if iface == 0:
                self.razer_command(out_data)
            elif iface == 1:
                self.led_frame(out_data)
            else:
                log(f'SET_REPORT if{iface} {out_data.hex()}')
            return 0, b''
        if bm == 0xa1 and breq == 0x01:                  # GET_REPORT
            if (widx & 0xff) == 0:
                return 0, self.last_reply[:wlen]
            return 0, bytes(wlen)
        if bm in (0x80, 0x81, 0x82) and breq == 0:       # GET_STATUS
            return 0, b'\x00\x00'[:wlen]
        if bm == 0x80 and breq == 8:                     # GET_CONFIGURATION
            return 0, b'\x01'
        log(f'CONTROL unhandled {setup.hex()} out={out_data.hex()}')
        return EPIPE, b''

def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('closed')
        buf += chunk
    return buf

def usb_device_struct():
    path = b'/sys/devices/fake/razer-argb'.ljust(256, b'\0')
    return path + BUSID.ljust(32, b'\0') + struct.pack(
        '>IIIHHHBBBBBB', 1, 2, SPEED_FULL, 0x1532, 0x0F1F, 0x0200, 0, 0, 0, 1, 1, 3)

def serve_urbs(conn, box):
    pending_in = {}   # seqnum -> ep, interrupt-IN URBs we hold (no events to report)
    send_lock = threading.Lock()
    def send(b):
        with send_lock:
            conn.sendall(b)
    while True:
        hdr = recv_exact(conn, 48)
        cmd, seq, devid, direction, ep = struct.unpack('>IIIII', hdr[:20])
        if cmd == 1:  # CMD_SUBMIT
            flags, tlen, sframe, npk, interval = struct.unpack('>IIiiI', hdr[20:40])
            setup = hdr[40:48]
            out = recv_exact(conn, tlen) if direction == 0 and tlen else b''
            if 0 < npk < 0x7fffffff:
                recv_exact(conn, 16 * npk)
            if ep == 0:
                status, data = box.control(setup, out)
                if direction == 0:
                    data_len, data = (len(out) if status == 0 else 0), b''
                else:
                    data_len = len(data)
                ret = struct.pack('>IIIIIiIiii', 3, seq, 0, 0, 0, status, data_len, 0, npk, 0) + bytes(8)
                send(ret + data)
            elif direction == 1:
                pending_in[seq] = ep
            else:
                log(f'OUT on ep{ep} len={len(out)} {out[:32].hex()}')
                send(struct.pack('>IIIIIiIiii', 3, seq, 0, 0, 0, 0, len(out), 0, npk, 0) + bytes(8))
        elif cmd == 2:  # CMD_UNLINK
            victim = struct.unpack('>I', hdr[20:24])[0]
            st = ECONNRESET if pending_in.pop(victim, None) is not None else 0
            send(struct.pack('>IIIIIi', 4, seq, 0, 0, 0, st) + bytes(24))
        else:
            log(f'unknown usbip cmd {cmd}')
            return

def handle(conn, addr, box):
    try:
        op = recv_exact(conn, 8)
        ver, code, status = struct.unpack('>HHI', op)
        if code == 0x8005:   # OP_REQ_DEVLIST
            body = usb_device_struct() + struct.pack('>BBBB', 3, 0, 0, 0) * 3
            conn.sendall(struct.pack('>HHII', ver, 0x0005, 0, 1) + body)
            log('devlist served')
        elif code == 0x8003: # OP_REQ_IMPORT
            busid = recv_exact(conn, 32).rstrip(b'\0')
            if busid != BUSID:
                conn.sendall(struct.pack('>HHI', ver, 0x0003, 1))
                return
            conn.sendall(struct.pack('>HHI', ver, 0x0003, 0) + usb_device_struct())
            log('IMPORTED by', addr)
            serve_urbs(conn, box)
        else:
            log(f'unknown op {code:#x}')
    except (ConnectionError, OSError) as e:
        log('connection ended:', e)
    finally:
        conn.close()

def main():
    global LOGFILE
    ap = argparse.ArgumentParser()
    ap.add_argument('--power', default='000000', help='hex args for reply to 07/8e (Get Power Security Status; nonzero = overcurrent trip)')
    ap.add_argument('--fw', default='0100', help='hex args for reply to 00/81 (firmware)')
    ap.add_argument('--log', default='fakebox.log')
    ap.add_argument('--port', type=int, default=3340, help='3240 sits in a Windows-reserved range on this PC')
    a = ap.parse_args()
    LOGFILE = a.log
    box = FakeBox(bytes.fromhex(a.power), bytes.fromhex(a.fw))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', a.port))
    srv.listen(4)
    log(f'fake ARGB box listening on 127.0.0.1:{a.port} power={a.power} fw={a.fw}')
    while True:
        c, addr = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=handle, args=(c, addr, box), daemon=True).start()

if __name__ == '__main__':
    main()
