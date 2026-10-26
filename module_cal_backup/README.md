# module_cal_backup

Back up calibration/identification data from vehicle modules over a **J2534**
pass-thru OBD-II interface — EPS, ABS, SRS, BCM, SAS, cluster, and anything
else that answers on the diagnostic bus, not just ECU/TCU.

It reads; it never writes to a module. Output is a hashed, timestamped
archive you can keep for safekeeping or use to match/verify a replacement part.

## What it actually does (and what it can't)

J2534 is only a pass-thru pipe for CAN/ISO-TP frames. "Copying a cal file" is
you running diagnostic services (UDS / ISO 14229) on top of that pipe. So:

- **Always works (if the module answers):** full **identification fingerprint** —
  OEM part number, supplier number, hardware/software numbers and versions,
  calibration ID (CALID), calibration verification number (CVN), serial, VIN.
  This is usually what you need to re-order or match a replacement module.
- **Works only where the module permits it:** a raw **memory image** via
  `ReadMemoryByAddress` / `RequestUpload`. Most OEMs gate this behind
  **Security Access (0x27)** with a proprietary seed/key. No keys ship here —
  supply your own `key_fn(level, seed)` for a vehicle you're authorized to
  service (see `keys_example.py`). Without the key, the tool records the
  identification fingerprint and notes the memory region as "not permitted".

There is no universal "dump any module's binary cal on any car." Anyone
claiming otherwise is glossing over the security layer.

## Requirements

- **Windows** at runtime — the J2534 PassThru API is a Windows DLL contract.
  You need your interface's vendor DLL (e.g. the `.dll` that ships with your
  OpenPort/Mongoose/DrewTech/etc. device).
- Python 3.10+ (stdlib only for the tool; `pytest` for the tests).

## Usage

```bash
# See the built-in common module map
python -m module_cal_backup.cli --list

# Discover which addresses actually answer on THIS car (read-only probe)
python -m module_cal_backup.cli --dll "C:\path\to\vendor.dll" --scan

# Back up EPS + a few modules' identification data
python -m module_cal_backup.cli --dll vendor.dll \
    --modules eps abs srs bcm --out .\backups

# Use a vehicle-specific map (addresses + optional memory region to dump)
python -m module_cal_backup.cli --dll vendor.dll \
    --config module_cal_backup\sample_vehicle.json --modules eps --out .\backups
```

Default module addresses are **common conventions, not guarantees** — outside
the powertrain range (0x7E0–0x7E7) they're OEM-assigned. Run `--scan` first
and put the real addresses in a JSON config (`sample_vehicle.json` is the
template).

## Output

Each run creates `backups/backup_<UTC>/` containing:

- `manifest.json` — every module, every DID read (hex + ASCII), VIN, and the
  SHA-256 + size of any memory image.
- `<module>_mem_<addr>_<size>.bin` — raw memory images, where permitted.

## Working with a .bin you already read (bintools)

If you pulled a calibration/firmware image off a module you own (e.g. with your
DPS read tool), `bintools` handles the safekeeping side — all offline, no
vehicle or OEM service involved:

```bash
# Integrity + structure: checksums, segment map (code vs low-entropy cal
# tables), and embedded strings (part #, CALID, dates)
python -m module_cal_backup.bincli info eps.bin

# Catch drift/corruption: byte-range diff of two read-backs. Flags a length
# mismatch loudly (usually a truncated/failed read). Exit code 1 if differ.
python -m module_cal_backup.bincli diff before.bin after.bin

# Archive into a hashed, dated folder with a manifest; dedupes on content hash
python -m module_cal_backup.bincli archive eps.bin --out ./cal_archive \
    --module eps --vin 1G1... --note "pre-repair read"
```

Typical flow: read with your tool → `archive` it → later `diff` a fresh read
against the archived copy to prove nothing changed. The segment scan is a
heuristic (long 0x00/0xFF fill runs = boundaries), not a parsed segment table —
confirm a boundary against the module's documented memory map before trusting
it. The running checksums are sanity values for spotting a bad read, not the
module's own stored checksum.

## Project layout

| File | Purpose |
|------|---------|
| `j2534.py` | ctypes wrapper for the J2534 PassThru DLL (ISO15765 channel) |
| `uds.py` | UDS client: DID reads, security access, `ReadMemoryByAddress`, upload loop |
| `modules.py` | Module address registry + standardized ISO 14229 DIDs |
| `discover.py` | Read-only bus scan for responding addresses |
| `backup.py` | Backup engine: probe → read identification → dump → hashed manifest |
| `cli.py` | Command-line entry point (on-vehicle backup) |
| `bintools.py` | Offline .bin analysis: checksums, segment/string scan, diff, archive |
| `bincli.py` | Command-line entry point for `info` / `diff` / `archive` |
| `keys_example.py` | Pluggable security-access key hook examples |
| `tests/` | Off-vehicle tests (fake channel; no hardware needed) |

## Tests

```bash
python -m pytest module_cal_backup/tests -q
```

These exercise the parts that bite in the field — UDS block-sequence counting
on upload, `responsePending` (0x78) retries, and negative-response decoding —
with a scripted fake channel, so they run with no device attached.

## Scope / authorization

Intended for backing up modules on vehicles you own or are authorized to
service. You supply any OEM security keys; none are included.
