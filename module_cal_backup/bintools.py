"""
Offline analysis for calibration/firmware .bin images read off a module
you own (e.g. via your DPS read tool).

Pure local file work: verify integrity, see where the data/cal regions sit,
diff two read-backs to catch drift or corruption, and archive a copy with a
hash + manifest. Nothing here talks to a vehicle or to any OEM service.
"""
from __future__ import annotations

import binascii
import datetime as _dt
import hashlib
import json
import pathlib
import shutil
from dataclasses import asdict, dataclass, field


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --- integrity ----------------------------------------------------------
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crc32(data: bytes) -> str:
    return f"{binascii.crc32(data) & 0xFFFFFFFF:08X}"


def checksums(data: bytes) -> dict:
    """Common ECU-style running sums. These are *sanity* values for spotting
    a truncated/garbled read, not a substitute for the module's own stored
    checksum (which lives at a module-specific offset you'd have to know).
    """
    total = sum(data)
    return {
        "len": len(data),
        "sum8": total & 0xFF,
        "sum16": total & 0xFFFF,
        "sum32": total & 0xFFFFFFFF,
        "twos_comp16": (-total) & 0xFFFF,  # many cals store sum so total==0
        "sha256": sha256(data),
        "crc32": crc32(data),
    }


# --- structure ----------------------------------------------------------
@dataclass
class Segment:
    start: int
    end: int           # exclusive
    length: int
    entropy: float     # 0..8 bits/byte; low => tables/text, high => code/packed


def _entropy(chunk: bytes) -> float:
    if not chunk:
        return 0.0
    counts = [0] * 256
    for b in chunk:
        counts[b] += 1
    import math
    n = len(chunk)
    h = 0.0
    for c in counts:
        if c:
            p = c / n
            h -= p * math.log2(p)
    return round(h, 3)


def segment_scan(data: bytes, fill_bytes: tuple[int, ...] = (0x00, 0xFF),
                 min_gap: int = 256) -> list[Segment]:
    """Split the image into data regions separated by long fill runs.

    Modules leave big 0x00/0xFF gaps between bootloader/app/cal blocks, so a
    run of >= min_gap identical fill bytes is treated as a boundary. It's a
    heuristic map, not a parsed segment table — confirm against the module's
    documented memory layout before trusting a boundary.
    """
    segs: list[Segment] = []
    n = len(data)
    i = 0
    while i < n:
        # skip a fill run
        if data[i] in fill_bytes:
            j = i
            while j < n and data[j] == data[i]:
                j += 1
            if j - i >= min_gap:
                i = j
                continue
        # start of a data region: extend until a long fill run appears
        start = i
        k = i
        while k < n:
            if data[k] in fill_bytes:
                m = k
                while m < n and data[m] == data[k]:
                    m += 1
                if m - k >= min_gap:
                    break
                k = m
            else:
                k += 1
        segs.append(Segment(start, k, k - start, _entropy(data[start:k])))
        i = k
    return segs


def find_strings(data: bytes, min_len: int = 5, limit: int = 100
                 ) -> list[tuple[int, str]]:
    """Printable-ASCII runs — surfaces embedded part numbers, CALID, dates,
    supplier tags. offset is where the run starts in the image."""
    out: list[tuple[int, str]] = []
    run = bytearray()
    start = 0
    for idx, b in enumerate(data):
        if 0x20 <= b < 0x7F:
            if not run:
                start = idx
            run.append(b)
        else:
            if len(run) >= min_len:
                out.append((start, run.decode("ascii")))
                if len(out) >= limit:
                    return out
            run = bytearray()
    if len(run) >= min_len and len(out) < limit:
        out.append((start, run.decode("ascii")))
    return out


# --- diff ---------------------------------------------------------------
@dataclass
class DiffRange:
    start: int
    end: int           # exclusive
    length: int


@dataclass
class DiffResult:
    equal: bool
    len_a: int
    len_b: int
    changed_bytes: int = 0
    ranges: list[DiffRange] = field(default_factory=list)
    note: str = ""


def diff(a: bytes, b: bytes, coalesce: int = 16) -> DiffResult:
    """Byte-range diff of two images. Gaps of <= `coalesce` equal bytes are
    merged into one range so a scattered field reads as a single change.

    Different lengths are reported but still diffed over the common prefix —
    a length mismatch almost always means a short/failed read, so that's
    flagged loudly rather than silently truncated.
    """
    if a == b:
        return DiffResult(True, len(a), len(b), 0, [], "identical")

    res = DiffResult(False, len(a), len(b))
    if len(a) != len(b):
        res.note = (f"LENGTH MISMATCH ({len(a)} vs {len(b)} bytes) — "
                    f"likely a truncated or failed read; diffing common prefix")

    n = min(len(a), len(b))
    ranges: list[list[int]] = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            start = i
            gap = 0
            end = i
            while i < n:
                if a[i] != b[i]:
                    end = i + 1
                    gap = 0
                else:
                    gap += 1
                    if gap > coalesce:
                        break
                i += 1
            ranges.append([start, end])
            res.changed_bytes += sum(
                1 for k in range(start, end) if a[k] != b[k])
        else:
            i += 1
    res.ranges = [DiffRange(s, e, e - s) for s, e in ranges]
    return res


# --- archive ------------------------------------------------------------
def archive(src: str | pathlib.Path, out_dir: str | pathlib.Path,
            module: str = "", vin: str = "", note: str = "") -> dict:
    """Copy a .bin into a hashed, dated archive with a manifest.

    Dedupes on content hash: re-archiving an identical image returns the
    existing entry instead of making a second copy. Returns the manifest.
    """
    src = pathlib.Path(src)
    data = src.read_bytes()
    digest = sha256(data)

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "archive_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []

    for entry in index:
        if entry["sha256"] == digest:
            entry["already_archived"] = True
            return entry

    short = digest[:12]
    stored_name = f"{(module or src.stem)}_{_utc_stamp()}_{short}.bin"
    (out_dir / stored_name).write_bytes(data)

    entry = {
        "stored_file": stored_name,
        "original_name": src.name,
        "archived_utc": _utc_stamp(),
        "module": module,
        "vin": vin,
        "note": note,
        **checksums(data),
    }
    index.append(entry)
    index_path.write_text(json.dumps(index, indent=2))
    return entry
