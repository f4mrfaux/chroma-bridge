"""Dump all USBPcap traffic for the Razer ARGB box (1532:0F1F) as readable text."""
import struct, sys, collections

def packets(path):
    with open(path, 'rb') as f:
        gh = f.read(24)
        if len(gh) < 24:
            return
        while True:
            h = f.read(16)
            if len(h) < 16:
                return
            ts, tu, incl, orig = struct.unpack('<IIII', h)
            yield ts + tu / 1e6, f.read(incl)

def parse(pkt):
    hlen, irp, status, func, info, bus, dev, ep, xfer, dlen = struct.unpack_from('<HQIHBHHBBI', pkt, 0)
    stage = pkt[27] if xfer == 2 and hlen >= 28 else None
    return dict(hlen=hlen, irp=irp, status=status, func=func, resp=bool(info & 1), bus=bus,
                dev=dev, ep=ep, xfer=xfer, stage=stage, data=pkt[hlen:hlen + dlen])

def main(path, out):
    recs = [(t, parse(p)) for t, p in packets(path)]
    # find device address whose device descriptor names 1532:0F1F
    targets = set()
    for t, r in recs:
        d = r['data']
        for i in range(len(d) - 18 + 1):
            if d[i] == 18 and d[i + 1] == 1 and d[i + 8:i + 12] == b'\x32\x15\x1f\x0f':
                targets.add((r['bus'], r['dev']))
    with open(out, 'w') as o:
        o.write(f'targets {targets}\n')
        counts = collections.Counter()
        for t, r in recs:
            if (r['bus'], r['dev']) not in targets:
                continue
            d = r['data']
            key = (r['xfer'], r['ep'], r['resp'], r['stage'], len(d))
            counts[key] += 1
            o.write(f"{t:.4f} xfer={r['xfer']} ep=0x{r['ep']:02x} {'IN<' if r['resp'] else 'OUT>'} "
                    f"stage={r['stage']} st=0x{r['status']:08x} len={len(d)} {d.hex()}\n")
        o.write('\nsummary (xfer, ep, resp, stage, len): count\n')
        for k, v in sorted(counts.items(), key=lambda kv: str(kv[0])):
            o.write(f'{k}: {v}\n')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
