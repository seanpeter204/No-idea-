"""
Command-line entry point.

Examples:
  # List the common module map
  python -m module_cal_backup.cli --dll "C:\\path\\to\\vendor.dll" --list

  # Scan the bus to see what actually answers
  python -m module_cal_backup.cli --dll vendor.dll --scan

  # Back up EPS + a few modules' identification fingerprints
  python -m module_cal_backup.cli --dll vendor.dll --modules eps abs srs bcm \\
      --out ./backups

  # Load a vehicle-specific module map (addresses + mem regions)
  python -m module_cal_backup.cli --dll vendor.dll --config my_car.json \\
      --modules eps --out ./backups
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .backup import CalBackup
from .modules import DEFAULT_REGISTRY, ModuleDef


def _load_registry(path: str) -> dict[str, ModuleDef]:
    """JSON: {"eps": {"name":..,"tx_id":"0x730","rx_id":"0x738",
                      "extended":false,"mem_region":["0x8000","0x4000"]}}"""
    data = json.loads(pathlib.Path(path).read_text())
    reg: dict[str, ModuleDef] = {}
    for key, v in data.items():
        mem = v.get("mem_region")
        reg[key] = ModuleDef(
            key=key, name=v["name"],
            tx_id=int(str(v["tx_id"]), 0), rx_id=int(str(v["rx_id"]), 0),
            extended=bool(v.get("extended", False)),
            mem_region=(int(str(mem[0]), 0), int(str(mem[1]), 0)) if mem else None,
        )
    return reg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="module_cal_backup")
    ap.add_argument("--dll", help="path to the vendor J2534 PassThru DLL")
    ap.add_argument("--out", default="./backups", help="output directory")
    ap.add_argument("--baud", type=int, default=500000)
    ap.add_argument("--config", help="JSON module map for this vehicle")
    ap.add_argument("--modules", nargs="*",
                    help="module keys to back up (default: all in registry)")
    ap.add_argument("--list", action="store_true",
                    help="print the module registry and exit")
    ap.add_argument("--scan", action="store_true",
                    help="probe the bus for responding addresses")
    args = ap.parse_args(argv)

    registry = _load_registry(args.config) if args.config else DEFAULT_REGISTRY

    if args.list:
        for k, m in registry.items():
            region = f"  mem 0x{m.mem_region[0]:X}+{m.mem_region[1]}" \
                if m.mem_region else ""
            print(f"{k:6} {m.name:32} "
                  f"tx=0x{m.tx_id:03X} rx=0x{m.rx_id:03X}{region}")
        return 0

    if not args.dll:
        ap.error("--dll is required unless using --list")

    if args.scan:
        from .j2534 import J2534Device, PROTOCOL_ISO15765
        from .discover import scan
        with J2534Device(args.dll) as dev:
            ch = dev.connect(PROTOCOL_ISO15765, baud=args.baud)
            for d in scan(ch):
                print(f"responder: tx=0x{d.tx_id:03X} rx=0x{d.rx_id:03X}")
        return 0

    backup = CalBackup(args.dll, args.out, baud=args.baud, registry=registry)
    manifest = backup.run(args.modules)

    print(f"\nBackup written to: {manifest['_manifest_path']}")
    for m in manifest["modules"]:
        status = "ok" if m["responded"] else "no response"
        mem = f", mem {m['memory_bytes']}B" if m["memory_file"] else ""
        print(f"  {m['key']:6} {status:12} {len(m['dids'])} DIDs{mem}")
        for note in m["notes"]:
            print(f"         note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
