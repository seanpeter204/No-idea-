"""
Calibration/identification backup engine.

Pulls a verifiable archive off each addressed module:
  * full identification fingerprint (always, if the module answers)
  * raw memory image (only where the module permits a read/upload)

Every artifact is hashed (SHA-256) and recorded in a JSON manifest so a
restore/compare later is trustworthy. Nothing here writes to a module.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Optional

from .j2534 import J2534Device, PROTOCOL_ISO15765
from .modules import (DEFAULT_REGISTRY, STANDARD_DIDS, ModuleDef)
from .uds import (KeyFn, NegativeResponse, SESSION_EXTENDED, UDSClient,
                  UDSError)


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _printable(data: bytes) -> str | None:
    """Most identification DIDs are ASCII; return a clean string when it is."""
    if data and all(0x20 <= b < 0x7F or b in (0x00,) for b in data):
        return data.rstrip(b"\x00").decode("ascii", "replace")
    return None


@dataclass
class DidResult:
    did: int
    name: str
    hex: str
    ascii: Optional[str]


@dataclass
class ModuleResult:
    key: str
    name: str
    tx_id: int
    rx_id: int
    responded: bool = False
    dids: list[DidResult] = field(default_factory=list)
    memory_file: Optional[str] = None
    memory_sha256: Optional[str] = None
    memory_bytes: int = 0
    notes: list[str] = field(default_factory=list)


class CalBackup:
    def __init__(self, dll_path: str, out_dir: str | pathlib.Path,
                 baud: int = 500000, registry: dict[str, ModuleDef] | None = None,
                 key_fn: Optional[KeyFn] = None):
        self.dll_path = dll_path
        self.baud = baud
        self.registry = registry or DEFAULT_REGISTRY
        self.key_fn = key_fn
        self.session_dir = pathlib.Path(out_dir) / f"backup_{_utc_stamp()}"

    # -- public ----------------------------------------------------------
    def run(self, module_keys: Optional[list[str]] = None) -> dict:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        keys = module_keys or list(self.registry)
        results: list[ModuleResult] = []

        with J2534Device(self.dll_path) as dev:
            ch = dev.connect(PROTOCOL_ISO15765, baud=self.baud)
            for key in keys:
                mod = self.registry.get(key)
                if mod is None:
                    continue
                results.append(self._backup_module(ch, mod))
                ch.clear_filters()

        return self._write_manifest(results)

    # -- per-module ------------------------------------------------------
    def _backup_module(self, ch, mod: ModuleDef) -> ModuleResult:
        res = ModuleResult(mod.key, mod.name, mod.tx_id, mod.rx_id)
        uds = UDSClient(ch, mod.tx_id, mod.rx_id, extended=mod.extended)

        # Probe with a cheap, universally-supported DID (ActiveDiagnosticSession
        # 0xF186) to confirm the module is actually there before hammering it.
        try:
            uds.read_data_by_identifier(0xF186)
            res.responded = True
        except (UDSError, NegativeResponse) as e:
            # A negative response still means something answered on rx_id.
            res.responded = isinstance(e, NegativeResponse)
            if not res.responded:
                res.notes.append(f"no response: {e}")
                return res

        # Try an extended session so more DIDs become readable; non-fatal.
        try:
            uds.diagnostic_session(SESSION_EXTENDED)
        except (UDSError, NegativeResponse) as e:
            res.notes.append(f"extended session unavailable: {e}")

        self._read_identification(uds, res)

        if mod.mem_region is not None:
            self._dump_memory(uds, mod, res)

        return res

    def _read_identification(self, uds: UDSClient, res: ModuleResult) -> None:
        for did, name in STANDARD_DIDS.items():
            try:
                data = uds.read_data_by_identifier(did)
            except NegativeResponse:
                continue  # DID simply not supported by this module
            except UDSError as e:
                res.notes.append(f"DID 0x{did:04X} read error: {e}")
                continue
            res.dids.append(DidResult(did, name, data.hex(), _printable(data)))

    def _dump_memory(self, uds: UDSClient, mod: ModuleDef,
                     res: ModuleResult) -> None:
        addr, size = mod.mem_region  # type: ignore[misc]
        # Memory read almost always needs security access first.
        if self.key_fn is not None:
            try:
                uds.security_access(0x01, self.key_fn)
            except (UDSError, NegativeResponse) as e:
                res.notes.append(f"security access failed: {e}")
        try:
            image = uds.upload(addr, size,
                               progress=lambda d, t: None)
        except (UDSError, NegativeResponse) as e:
            res.notes.append(
                f"memory read of 0x{addr:X}+{size} not permitted: {e}")
            return

        fname = f"{mod.key}_mem_{addr:08X}_{size:08X}.bin"
        path = self.session_dir / fname
        path.write_bytes(image)
        res.memory_file = fname
        res.memory_bytes = len(image)
        res.memory_sha256 = hashlib.sha256(image).hexdigest()

    # -- manifest --------------------------------------------------------
    def _write_manifest(self, results: list[ModuleResult]) -> dict:
        manifest = {
            "tool": "module_cal_backup",
            "created_utc": _utc_stamp(),
            "baud": self.baud,
            "modules": [asdict(r) for r in results],
        }
        # VIN, if any module reported it, is useful at the top level.
        for r in results:
            for d in r.dids:
                if d.did == 0xF190 and d.ascii:
                    manifest["vin"] = d.ascii
        path = self.session_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2))
        manifest["_manifest_path"] = str(path)
        return manifest
