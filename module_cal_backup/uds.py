"""
UDS (ISO 14229) client running over a J2534 ISO15765 channel.

The J2534 device handles ISO-TP segmentation in hardware once a flow-control
filter is armed, so here we deal in whole UDS service payloads. Only the
services needed for reading/backing up calibration data are implemented.

Negative-response handling, pending (0x78) retries, and session keep-alive
are the parts that actually bite in the field, so they're handled explicitly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .j2534 import J2534Channel

# --- services -----------------------------------------------------------
SID_DIAGNOSTIC_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_SECURITY_ACCESS = 0x27
SID_TESTER_PRESENT = 0x3E
SID_READ_DATA_BY_IDENTIFIER = 0x22
SID_READ_MEMORY_BY_ADDRESS = 0x23
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37

NEGATIVE_RESPONSE = 0x7F
RESPONSE_PENDING = 0x78  # "requestCorrectlyReceived-ResponsePending"

# Sessions
SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

NRC_TEXT = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


class NegativeResponse(Exception):
    def __init__(self, sid: int, nrc: int):
        self.sid = sid
        self.nrc = nrc
        super().__init__(
            f"NRC 0x{nrc:02X} ({NRC_TEXT.get(nrc, 'unknown')}) "
            f"to service 0x{sid:02X}")


class UDSError(Exception):
    pass


# KeyFn: given (level, seed_bytes) return the key bytes. OEM-specific and
# supplied by the caller — the project ships no keys.
KeyFn = Callable[[int, bytes], bytes]


@dataclass
class UDSClient:
    channel: J2534Channel
    tx_id: int
    rx_id: int
    p2_ms: int = 2000          # normal response timeout
    p2_star_ms: int = 5000     # extended timeout after a 0x78 pending
    extended: bool = False

    def __post_init__(self) -> None:
        self.channel.set_flow_control_filter(self.tx_id, self.rx_id,
                                             self.extended)

    # -- core request/response -----------------------------------------
    def request(self, payload: bytes, timeout_ms: Optional[int] = None
                ) -> bytes:
        self.channel.write(self.tx_id, payload, self.extended)
        return self._await_response(payload[0], timeout_ms or self.p2_ms)

    def _await_response(self, req_sid: int, timeout_ms: int) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            for can_id, data in self.channel.read(timeout_ms=200):
                if can_id != self.rx_id or not data:
                    continue
                if data[0] == NEGATIVE_RESPONSE and len(data) >= 3:
                    nrc = data[2]
                    if nrc == RESPONSE_PENDING:
                        # ECU is busy (e.g. erasing/reading flash). Reset the
                        # clock to P2* and keep waiting rather than bailing.
                        deadline = time.monotonic() + self.p2_star_ms / 1000.0
                        continue
                    raise NegativeResponse(data[1], nrc)
                if data[0] == req_sid + 0x40:
                    return data
        raise UDSError(f"timeout waiting for response to 0x{req_sid:02X}")

    # -- services -------------------------------------------------------
    def diagnostic_session(self, session: int) -> bytes:
        return self.request(bytes([SID_DIAGNOSTIC_SESSION_CONTROL, session]))

    def tester_present(self) -> None:
        # subfunction 0x80 = suppress positive response
        self.channel.write(self.tx_id, bytes([SID_TESTER_PRESENT, 0x80]),
                           self.extended)

    def read_data_by_identifier(self, did: int) -> bytes:
        resp = self.request(bytes([SID_READ_DATA_BY_IDENTIFIER,
                                   did >> 8, did & 0xFF]))
        # resp = 62 <did_hi> <did_lo> <data...>
        if len(resp) < 3 or (resp[1] << 8 | resp[2]) != did:
            raise UDSError(f"DID echo mismatch for 0x{did:04X}")
        return resp[3:]

    def security_access(self, level: int, key_fn: KeyFn) -> None:
        """Unlock a security level. `level` is the request-seed subfunction
        (odd: 0x01, 0x03, ...). key_fn computes the key from the seed."""
        seed_resp = self.request(bytes([SID_SECURITY_ACCESS, level]))
        seed = seed_resp[2:]
        if not any(seed):
            return  # all-zero seed => already unlocked
        key = key_fn(level, seed)
        self.request(bytes([SID_SECURITY_ACCESS, level + 1]) + key)

    def read_memory_by_address(self, address: int, size: int,
                               addr_bytes: int = 4, size_bytes: int = 4
                               ) -> bytes:
        """Single-shot ReadMemoryByAddress (0x23).

        addressAndLengthFormatIdentifier is packed as (size_bytes<<4 | addr_bytes)
        per ISO 14229. Large reads should go through upload() instead.
        """
        alfid = (size_bytes << 4) | addr_bytes
        req = bytes([SID_READ_MEMORY_BY_ADDRESS, alfid])
        req += address.to_bytes(addr_bytes, "big")
        req += size.to_bytes(size_bytes, "big")
        resp = self.request(req, timeout_ms=self.p2_star_ms)
        return resp[1:]

    def upload(self, address: int, size: int, addr_bytes: int = 4,
               size_bytes: int = 4,
               progress: Optional[Callable[[int, int], None]] = None
               ) -> bytes:
        """RequestUpload (0x35) + TransferData (0x36) loop + TransferExit (0x37).

        Returns the concatenated memory image. The ECU dictates the max block
        length in the 0x35 response; we honour it and track the block sequence
        counter exactly, which is where most home-grown dumps go wrong.
        """
        # 0x35: dataFormatIdentifier=00 (no compression/encryption)
        alfid = (size_bytes << 4) | addr_bytes
        req = bytes([SID_REQUEST_UPLOAD, 0x00, alfid])
        req += address.to_bytes(addr_bytes, "big")
        req += size.to_bytes(size_bytes, "big")
        resp = self.request(req, timeout_ms=self.p2_star_ms)

        # resp: 75 <lengthFormatId> <maxNumberOfBlockLength...>
        len_fmt = (resp[1] >> 4) & 0x0F
        max_block = int.from_bytes(resp[2:2 + len_fmt], "big")
        # max_block includes the SID+BSC overhead (2 bytes) per spec.
        chunk = max(1, max_block - 2)

        out = bytearray()
        bsc = 0x01
        while len(out) < size:
            resp = self.request(bytes([SID_TRANSFER_DATA, bsc]),
                                timeout_ms=self.p2_star_ms)
            if resp[1] != bsc:
                raise UDSError(
                    f"block sequence mismatch: got {resp[1]:#x} want {bsc:#x}")
            out.extend(resp[2:])
            bsc = (bsc + 1) & 0xFF
            if bsc == 0:
                bsc = 0x01  # BSC wraps 0xFF -> 0x01, never 0x00
            if progress:
                progress(min(len(out), size), size)
            if len(resp) <= 2:
                break  # ECU sent an empty block; nothing more coming
            _ = chunk  # chunk size is informational for our read loop

        self.request(bytes([SID_REQUEST_TRANSFER_EXIT]),
                     timeout_ms=self.p2_star_ms)
        return bytes(out[:size])
