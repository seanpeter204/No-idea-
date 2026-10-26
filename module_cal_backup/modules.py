"""
Module address registry and standardized calibration DIDs.

CAN diagnostic addressing: a request goes to tx_id, the module answers on
rx_id. 11-bit ISO15765 convention pairs them as 0x7E0/0x7E8, 0x7E1/0x7E9 ...
Everything outside the powertrain 0x7E0..0x7E7 range is OEM-assigned, so the
addresses below are *common* defaults, not guarantees. Always confirm with a
discovery scan before trusting them.
"""
from __future__ import annotations

from dataclasses import dataclass

# ISO 14229 standardized identification DIDs (F1xx range). These are the
# backup that works even when memory read is locked: part numbers, software
# and calibration versions, CVN, serial, VIN.
STANDARD_DIDS: dict[int, str] = {
    0xF180: "BootSoftwareIdentification",
    0xF181: "ApplicationSoftwareIdentification",
    0xF182: "ApplicationDataIdentification",
    0xF183: "BootSoftwareFingerprint",
    0xF184: "ApplicationSoftwareFingerprint",
    0xF185: "ApplicationDataFingerprint",
    0xF186: "ActiveDiagnosticSession",
    0xF187: "VehicleManufacturerSparePartNumber",
    0xF188: "VehicleManufacturerECUSoftwareNumber",
    0xF189: "VehicleManufacturerECUSoftwareVersionNumber",
    0xF18A: "SystemSupplierIdentifier",
    0xF18B: "ECUManufacturingDate",
    0xF18C: "ECUSerialNumber",
    0xF190: "VIN",
    0xF191: "VehicleManufacturerECUHardwareNumber",
    0xF192: "SystemSupplierECUHardwareNumber",
    0xF193: "SystemSupplierECUHardwareVersionNumber",
    0xF194: "SystemSupplierECUSoftwareNumber",
    0xF195: "SystemSupplierECUSoftwareVersionNumber",
    0xF197: "SystemNameOrEngineType",
    0xF19E: "ASAMODXFileIdentifier",
    0xF1A2: "RepairShopCodeOrTesterSerialNumber",
    # Commonly carry CAL ID / CVN on OBD-regulated modules:
    0xF806: "CalibrationIdentifications(CALID)",
    0xF80B: "CalibrationVerificationNumbers(CVN)",
}


@dataclass(frozen=True)
class ModuleDef:
    key: str
    name: str
    tx_id: int
    rx_id: int
    extended: bool = False
    # Optional raw memory region to dump IF the module permits it.
    # (address, size). None => identification DIDs only.
    mem_region: tuple[int, int] | None = None


# Common 11-bit diagnostic addresses. EPS/SAS/BCM addresses vary widely by
# OEM; override these from a JSON config for your specific vehicle.
DEFAULT_REGISTRY: dict[str, ModuleDef] = {
    m.key: m
    for m in [
        ModuleDef("ecu", "Engine Control Module", 0x7E0, 0x7E8),
        ModuleDef("tcu", "Transmission Control Module", 0x7E1, 0x7E9),
        ModuleDef("eps", "Electric Power Steering", 0x730, 0x738),
        ModuleDef("abs", "ABS / Brake Control", 0x760, 0x768),
        ModuleDef("srs", "Airbag / SRS", 0x780, 0x788),
        ModuleDef("bcm", "Body Control Module", 0x720, 0x728),
        ModuleDef("sas", "Steering Angle Sensor", 0x732, 0x73A),
        ModuleDef("ipc", "Instrument Cluster", 0x720, 0x728),
        ModuleDef("hvac", "Climate Control", 0x7C0, 0x7C8),
    ]
}

# Functional (broadcast) request address; every module that supports OBD
# diagnostics answers a tester-present / DID request sent here on its own id.
FUNCTIONAL_REQUEST_ID = 0x7DF
