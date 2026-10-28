"""
CLI for offline .bin analysis / archival.

  python -m module_cal_backup.bincli info   dump.bin
  python -m module_cal_backup.bincli diff    before.bin after.bin
  python -m module_cal_backup.bincli archive eps.bin --out ./cal_archive \
         --module eps --vin 1G1... --note "pre-repair read"
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from . import bintools as bt
from . import calrecord as cr


def _cmd_info(args) -> int:
    data = pathlib.Path(args.file).read_bytes()
    cs = bt.checksums(data)
    print(f"file     {args.file}")
    print(f"size     {cs['len']} bytes ({cs['len'] / 1024:.1f} KiB)")
    print(f"sha256   {cs['sha256']}")
    print(f"crc32    {cs['crc32']}")
    print(f"sum16    0x{cs['sum16']:04X}   twos_comp16 0x{cs['twos_comp16']:04X}")

    segs = bt.segment_scan(data, min_gap=args.min_gap)
    print(f"\nsegments (fill-gap >= {args.min_gap}B):")
    for s in segs:
        print(f"  0x{s.start:08X}-0x{s.end:08X}  {s.length:>8}B  "
              f"entropy {s.entropy:.2f}")

    strings = bt.find_strings(data, min_len=args.min_str, limit=args.max_str)
    print(f"\nembedded strings (>= {args.min_str} chars, first {args.max_str}):")
    for off, s in strings:
        print(f"  0x{off:08X}  {s!r}")
    return 0


def _cmd_diff(args) -> int:
    a = pathlib.Path(args.a).read_bytes()
    b = pathlib.Path(args.b).read_bytes()
    r = bt.diff(a, b)
    if r.equal:
        print("identical")
        return 0
    if r.note:
        print(r.note)
    print(f"{r.changed_bytes} bytes differ across {len(r.ranges)} range(s):")
    for rng in r.ranges:
        print(f"  0x{rng.start:08X}-0x{rng.end:08X}  {rng.length}B")
    return 1  # non-zero so a scripted "did anything change?" can branch on it


def _cmd_archive(args) -> int:
    entry = bt.archive(args.file, args.out, module=args.module,
                       vin=args.vin, note=args.note)
    if entry.get("already_archived"):
        print(f"already archived (identical hash): {entry['stored_file']}")
    else:
        print(f"archived -> {pathlib.Path(args.out) / entry['stored_file']}")
    print(f"sha256 {entry['sha256']}")
    return 0


def _cmd_calrec(args) -> int:
    if args.calrec_cmd == "add":
        rec = cr.load_record(args.file)
        path = cr.save_record(rec, args.out)
        missing = sum(1 for s in rec.segments
                      if not s.cvn or s.cvn.upper() == "N/A")
        print(f"saved -> {path}")
        print(f"  {rec.controller_code} {rec.controller_name} / VIN {rec.vin}"
              f" / {len(rec.segments)} segments")
        if missing:
            print(f"  {missing} segment(s) still need a CVN (GET CVN per part #)")
        return 0
    if args.calrec_cmd == "list":
        recs = cr.list_records(args.out)
        if not recs:
            print("no cal records yet")
            return 0
        for r in recs:
            miss = sum(1 for s in r["segments"]
                       if not s["cvn"] or s["cvn"].upper() == "N/A")
            tag = f"  ({miss} missing CVN)" if miss else ""
            print(f"{r['vin']}  {r['controller_code']} {r['controller_name']}"
                  f"  {len(r['segments'])} seg{tag}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="module_cal_backup.bincli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="checksums, segment map, embedded strings")
    p.add_argument("file")
    p.add_argument("--min-gap", type=int, default=256)
    p.add_argument("--min-str", type=int, default=5)
    p.add_argument("--max-str", type=int, default=60)
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("diff", help="byte-range diff of two images")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=_cmd_diff)

    p = sub.add_parser("archive", help="copy into a hashed, dated archive")
    p.add_argument("file")
    p.add_argument("--out", default="./cal_archive")
    p.add_argument("--module", default="")
    p.add_argument("--vin", default="")
    p.add_argument("--note", default="")
    p.set_defaults(func=_cmd_archive)

    p = sub.add_parser("calrec", help="store/list structured cal-ID records")
    csub = p.add_subparsers(dest="calrec_cmd", required=True)
    pa = csub.add_parser("add", help="ingest a filled cal-record JSON")
    pa.add_argument("file")
    pa.add_argument("--out", default="./cal_archive")
    pl = csub.add_parser("list", help="list stored cal records")
    pl.add_argument("--out", default="./cal_archive")
    p.set_defaults(func=_cmd_calrec)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
