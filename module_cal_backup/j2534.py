"""
Minimal J2534-1 PassThru API wrapper (ctypes).

Loads a vendor PassThru DLL and exposes the subset needed to run a
raw ISO15765 (CAN + ISO-TP) channel. Windows-only at runtime because the
PassThru API is a Windows DLL contract; the wrapper itself imports cleanly
anywhere so the rest of the project can be unit-tested off-vehicle.

Typical flow:
    dev = J2534Device(dll_path)
    dev.open()
    ch = dev.connect(PROTOCOL_ISO15765, baud=500000)
    ch.set_flow_control_filter(tx_id=0x7E0, rx_id=0x7E8)
    ch.write(0x7E0, bytes([0x22, 0xF1, 0x90]))
    for rx_id, payload in ch.read(timeout_ms=1000): ...
"""
from __future__ import annotations

import ctypes as C
import platform
from typing import Iterator

# --- Protocol / flag constants from the J2534-1 spec --------------------
PROTOCOL_CAN = 5
PROTOCOL_ISO15765 = 6

# Connect flags
CAN_29BIT_ID = 0x00000100

# TxFlags
ISO15765_FRAME_PAD = 0x00000040
CAN_29BIT_ID_TX = 0x00000100

# RxStatus bits (used to drop echoes / indications)
TX_MSG_TYPE = 0x00000001          # loopback echo of our own tx
START_OF_MESSAGE = 0x00000002     # ISO-TP first-frame indication, no data yet

# Filter types
PASS_FILTER = 0x00000001
FLOW_CONTROL_FILTER = 0x00000003

# Ioctl IDs
CLEAR_MSG_FILTERS = 0x0A
SET_CONFIG = 0x05
GET_CONFIG = 0x04

# SCONFIG parameter IDs
DATA_RATE = 0x01
LOOPBACK = 0x03

# Error codes worth naming; everything else surfaces via GetLastError text.
STATUS_NOERROR = 0x00
ERR_BUFFER_EMPTY = 0x10
ERR_TIMEOUT = 0x11


class PASSTHRU_MSG(C.Structure):
    _fields_ = [
        ("ProtocolID", C.c_ulong),
        ("RxStatus", C.c_ulong),
        ("TxFlags", C.c_ulong),
        ("Timestamp", C.c_ulong),
        ("DataSize", C.c_ulong),
        ("ExtraDataIndex", C.c_ulong),
        ("Data", C.c_ubyte * 4128),
    ]


class SCONFIG(C.Structure):
    _fields_ = [("Parameter", C.c_ulong), ("Value", C.c_ulong)]


class SCONFIG_LIST(C.Structure):
    _fields_ = [("NumOfParams", C.c_ulong), ("ConfigPtr", C.POINTER(SCONFIG))]


class J2534Error(RuntimeError):
    pass


class J2534Device:
    def __init__(self, dll_path: str):
        if platform.system() != "Windows":
            # The API only exists on Windows. Fail loudly on open(), not import,
            # so analysis/tests can still import this module on Linux/CI.
            self._windows = False
        else:
            self._windows = True
        self._dll_path = dll_path
        self._dll: C.CDLL | None = None
        self._device_id = C.c_ulong(0)

    # -- lifecycle -------------------------------------------------------
    def open(self) -> None:
        if not self._windows:
            raise J2534Error(
                "J2534 PassThru DLLs are Windows-only; run the backup on a "
                "Windows host with the device's vendor driver installed."
            )
        self._dll = C.WinDLL(self._dll_path)  # type: ignore[attr-defined]
        self._bind_prototypes()
        self._check(self._dll.PassThruOpen(None, C.byref(self._device_id)),
                    "PassThruOpen")

    def close(self) -> None:
        if self._dll is not None:
            self._dll.PassThruClose(self._device_id)
            self._dll = None

    def __enter__(self) -> "J2534Device":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- channel ---------------------------------------------------------
    def connect(self, protocol: int, baud: int = 500000,
                flags: int = 0) -> "J2534Channel":
        assert self._dll is not None
        channel_id = C.c_ulong(0)
        self._check(
            self._dll.PassThruConnect(self._device_id, protocol, flags, baud,
                                      C.byref(channel_id)),
            "PassThruConnect")
        return J2534Channel(self, channel_id.value, protocol)

    # -- internals -------------------------------------------------------
    def _bind_prototypes(self) -> None:
        d = self._dll
        ul = C.c_ulong
        p = C.POINTER
        d.PassThruOpen.argtypes = [C.c_void_p, p(ul)]
        d.PassThruClose.argtypes = [ul]
        d.PassThruConnect.argtypes = [ul, ul, ul, ul, p(ul)]
        d.PassThruDisconnect.argtypes = [ul]
        d.PassThruReadMsgs.argtypes = [ul, p(PASSTHRU_MSG), p(ul), ul]
        d.PassThruWriteMsgs.argtypes = [ul, p(PASSTHRU_MSG), p(ul), ul]
        d.PassThruStartMsgFilter.argtypes = [
            ul, ul, p(PASSTHRU_MSG), p(PASSTHRU_MSG), p(PASSTHRU_MSG), p(ul)]
        d.PassThruIoctl.argtypes = [ul, ul, C.c_void_p, C.c_void_p]
        d.PassThruGetLastError.argtypes = [C.c_char_p]

    def _last_error(self) -> str:
        buf = C.create_string_buffer(80)
        if self._dll is not None:
            self._dll.PassThruGetLastError(buf)
        return buf.value.decode(errors="replace")

    def _check(self, rc: int, where: str) -> None:
        if rc != STATUS_NOERROR:
            raise J2534Error(f"{where} failed (0x{rc:02X}): {self._last_error()}")


class J2534Channel:
    def __init__(self, dev: J2534Device, channel_id: int, protocol: int):
        self._dev = dev
        self._dll = dev._dll
        self._id = channel_id
        self._protocol = protocol

    def set_flow_control_filter(self, tx_id: int, rx_id: int,
                                extended: bool = False) -> None:
        """Arm the ISO-TP flow-control filter so the device auto-segments.

        mask/pattern match the ECU response id; flow-control carries our
        request id so multi-frame uploads are ACKed in hardware.
        """
        mask = self._msg(b"\xff\xff\xff\xff", extended)
        pattern = self._msg(self._id4(rx_id), extended)
        flow = self._msg(self._id4(tx_id), extended)
        fid = C.c_ulong(0)
        self._dev._check(
            self._dll.PassThruStartMsgFilter(
                self._id, FLOW_CONTROL_FILTER,
                C.byref(mask), C.byref(pattern), C.byref(flow), C.byref(fid)),
            "PassThruStartMsgFilter")

    def clear_filters(self) -> None:
        self._dll.PassThruIoctl(self._id, CLEAR_MSG_FILTERS, None, None)

    def write(self, tx_id: int, payload: bytes, extended: bool = False,
              timeout_ms: int = 1000) -> None:
        msg = self._msg(self._id4(tx_id) + payload, extended)
        msg.TxFlags = ISO15765_FRAME_PAD | (CAN_29BIT_ID_TX if extended else 0)
        count = C.c_ulong(1)
        self._dev._check(
            self._dll.PassThruWriteMsgs(self._id, C.byref(msg),
                                        C.byref(count), timeout_ms),
            "PassThruWriteMsgs")

    def read(self, timeout_ms: int = 1000, max_msgs: int = 8
             ) -> Iterator[tuple[int, bytes]]:
        """Yield (can_id, payload) for real data frames, dropping echoes
        and ISO-TP first-frame indications."""
        buf = (PASSTHRU_MSG * max_msgs)()
        count = C.c_ulong(max_msgs)
        rc = self._dll.PassThruReadMsgs(self._id, buf, C.byref(count),
                                        timeout_ms)
        if rc in (ERR_BUFFER_EMPTY, ERR_TIMEOUT):
            return
        self._dev._check(rc, "PassThruReadMsgs")
        for i in range(count.value):
            m = buf[i]
            if m.RxStatus & (TX_MSG_TYPE | START_OF_MESSAGE):
                continue
            if m.DataSize < 4:
                continue
            data = bytes(m.Data[:m.DataSize])
            can_id = int.from_bytes(data[:4], "big")
            yield can_id, data[4:]

    def disconnect(self) -> None:
        self._dll.PassThruDisconnect(self._id)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _id4(can_id: int) -> bytes:
        return can_id.to_bytes(4, "big")

    def _msg(self, data: bytes, extended: bool) -> PASSTHRU_MSG:
        m = PASSTHRU_MSG()
        m.ProtocolID = self._protocol
        m.TxFlags = CAN_29BIT_ID_TX if extended else 0
        m.DataSize = len(data)
        for i, b in enumerate(data):
            m.Data[i] = b
        return m
