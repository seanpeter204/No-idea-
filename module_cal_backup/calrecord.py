"""
Structured calibration-ID records for a module (e.g. the SPS2 / GM gateway
cal lookup for a controller like K43 PSCM).

This stores the *identification map* — segment part numbers + CVNs — not a
binary. It's the reference you keep to know what's loaded, match a
replacement, and verify integrity (part number + CVN together). Lives in the
same archive folder as any .bin read-backs so all records for a VIN are
together.

Records are keyed (VIN, controller_code) and updated in place, so re-ingesting
after you fill in CVNs replaces the old record rather than duplicating it.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
from dataclasses import asdict, dataclass, field


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# GM uses three flash segments per module; `segment` should be one of these.
SEGMENTS = ("operating_system", "calibration", "system")


@dataclass
class CalSegment:
    segment: str            # one of SEGMENTS
    part_number: str
    cvn: str = ""           # "" / "N/A" until pulled via GET CVN
    description: str = ""
    bulletin: str = ""
    current: bool = False   # True if this is the part number currently loaded


@dataclass
class CalRecord:
    vin: str
    controller_code: str            # e.g. "K43"
    controller_name: str            # e.g. "Power Steering Control Module"
    segments: list[CalSegment] = field(default_factory=list)
    programming_part_numbers: list[str] = field(default_factory=list)
    selected_part_number: str = ""
    vehicle: str = ""               # free-text, e.g. "2014 GMC Sierra 1500"
    source: str = "SPS2 gateway.ext.gm.com"
    notes: str = ""
    recorded_utc: str = ""

    def key(self) -> str:
        return f"{self.vin}_{self.controller_code}"


def _coerce(obj: dict) -> CalRecord:
    segs = [CalSegment(**s) for s in obj.get("segments", [])]
    fields = {k: v for k, v in obj.items() if k != "segments"}
    rec = CalRecord(segments=segs, **fields)
    _validate(rec)
    return rec


def _validate(rec: CalRecord) -> None:
    if not rec.vin or not rec.controller_code:
        raise ValueError("record needs both vin and controller_code")
    for s in rec.segments:
        if s.segment not in SEGMENTS:
            raise ValueError(
                f"segment '{s.segment}' not one of {SEGMENTS} "
                f"(part {s.part_number})")


def load_record(path: str | pathlib.Path) -> CalRecord:
    """Load a hand-filled record JSON (from the SPS2 screen) and validate it."""
    return _coerce(json.loads(pathlib.Path(path).read_text()))


def save_record(rec: CalRecord, out_dir: str | pathlib.Path) -> pathlib.Path:
    """Write the record under <out_dir>/cal_records/, updating in place on
    (VIN, controller) and refreshing the index."""
    _validate(rec)
    if not rec.recorded_utc:
        rec.recorded_utc = _utc_stamp()

    rec_dir = pathlib.Path(out_dir) / "cal_records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / f"{rec.key()}.json"
    path.write_text(json.dumps(asdict(rec), indent=2))
    _reindex(rec_dir)
    return path


def list_records(out_dir: str | pathlib.Path) -> list[dict]:
    rec_dir = pathlib.Path(out_dir) / "cal_records"
    if not rec_dir.exists():
        return []
    out = []
    for p in sorted(rec_dir.glob("*.json")):
        if p.name == "index.json":
            continue
        out.append(json.loads(p.read_text()))
    return out


def _reindex(rec_dir: pathlib.Path) -> None:
    index = []
    for rec in [json.loads(p.read_text())
                for p in sorted(rec_dir.glob("*.json"))
                if p.name != "index.json"]:
        missing_cvn = sum(1 for s in rec["segments"]
                          if not s["cvn"] or s["cvn"].upper() == "N/A")
        index.append({
            "key": f"{rec['vin']}_{rec['controller_code']}",
            "vin": rec["vin"],
            "controller": f"{rec['controller_code']} {rec['controller_name']}",
            "vehicle": rec.get("vehicle", ""),
            "segments": len(rec["segments"]),
            "segments_without_cvn": missing_cvn,
            "recorded_utc": rec.get("recorded_utc", ""),
        })
    (rec_dir / "index.json").write_text(json.dumps(index, indent=2))
