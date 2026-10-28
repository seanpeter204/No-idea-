"""
Bus discovery: find which diagnostic addresses actually answer.

Sweeps 11-bit request ids and watches for any UDS response, so you learn the
real module map of the car in front of you instead of trusting defaults.
Read-only: it only sends TesterPresent / a benign DID read.
"""
from __future__ import annotations

from dataclasses import dataclass

from .j2534 import J2534Channel
from .uds import NegativeResponse, UDSClient, UDSError


@dataclass
class Discovered:
    tx_id: int
    rx_id: int
    responded: bool


def scan(ch: J2534Channel, tx_start: int = 0x700, tx_end: int = 0x7E7,
         rx_offset: int = 0x08) -> list[Discovered]:
    """Probe each request id tx with the conventional response id tx+offset.

    offset 0x08 matches the 0x7E0/0x7E8 pairing and the common 0x7x0/0x7x8
    body-module convention. For odd layouts, scan with a couple of offsets.
    """
    found: list[Discovered] = []
    for tx in range(tx_start, tx_end + 1):
        rx = tx + rx_offset
        uds = UDSClient(ch, tx, rx, p2_ms=250)
        try:
            uds.read_data_by_identifier(0xF186)  # ActiveDiagnosticSession
            found.append(Discovered(tx, rx, True))
        except NegativeResponse:
            # Something is home — it rejected the request but it's there.
            found.append(Discovered(tx, rx, True))
        except UDSError:
            pass  # silence => nothing at this address
        finally:
            ch.clear_filters()
    return found
