"""
Off-vehicle tests for the UDS layer using a scripted fake channel.

These validate the logic that actually breaks dumps in the field: block
sequence counting on upload, pending (0x78) handling, and negative-response
decoding. No hardware or DLL required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from module_cal_backup.uds import (NegativeResponse, UDSClient,
                                    SID_TRANSFER_DATA)


class FakeChannel:
    """Queues scripted (rx_id, payload) responses per outgoing request."""

    def __init__(self, script):
        # script: list of response lists; one list consumed per write()
        self._script = list(script)
        self._pending = []
        self.writes = []

    def set_flow_control_filter(self, tx_id, rx_id, extended=False):
        self.rx_id = rx_id

    def clear_filters(self):
        pass

    def write(self, tx_id, payload, extended=False, timeout_ms=1000):
        self.writes.append(bytes(payload))
        if payload[0] == 0x3E:  # tester present, suppressed response
            return
        self._pending = list(self._script.pop(0)) if self._script else []

    def read(self, timeout_ms=1000, max_msgs=8):
        while self._pending:
            yield self._pending.pop(0)


def test_read_did_ascii():
    ch = FakeChannel([[(0x7E8, bytes([0x62, 0xF1, 0x90]) + b"1HGCM82633A")]])
    uds = UDSClient(ch, 0x7E0, 0x7E8)
    data = uds.read_data_by_identifier(0xF190)
    assert data == b"1HGCM82633A"


def test_negative_response_raises():
    ch = FakeChannel([[(0x7E8, bytes([0x7F, 0x22, 0x31]))]])
    uds = UDSClient(ch, 0x7E0, 0x7E8)
    with pytest.raises(NegativeResponse) as ei:
        uds.read_data_by_identifier(0xF190)
    assert ei.value.nrc == 0x31


def test_pending_then_positive():
    ch = FakeChannel([[
        (0x7E8, bytes([0x7F, 0x22, 0x78])),       # response pending
        (0x7E8, bytes([0x62, 0xF1, 0x86, 0x03])),  # then the real answer
    ]])
    uds = UDSClient(ch, 0x7E0, 0x7E8)
    assert uds.read_data_by_identifier(0xF186) == bytes([0x03])


def test_upload_block_sequence_and_reassembly():
    # RequestUpload(0x35) -> maxBlockLength = 6 (so 4 data bytes/block),
    # then three TransferData blocks returning 4+4+2 = 10 bytes total.
    script = [
        [(0x7E8, bytes([0x75, 0x10, 0x06]))],                 # 0x35 reply
        [(0x7E8, bytes([0x76, 0x01]) + b"\xAA\xBB\xCC\xDD")],  # block 1
        [(0x7E8, bytes([0x76, 0x02]) + b"\xEE\xFF\x00\x11")],  # block 2
        [(0x7E8, bytes([0x76, 0x03]) + b"\x22\x33")],          # block 3
        [(0x7E8, bytes([0x77]))],                              # transfer exit
    ]
    ch = FakeChannel(script)
    uds = UDSClient(ch, 0x7E0, 0x7E8)
    image = uds.upload(0x8000, 10)
    assert image == b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11\x22\x33"

    # Confirm the block sequence counter incremented 1,2,3 on the wire.
    td = [w for w in ch.writes if w[0] == SID_TRANSFER_DATA]
    assert [w[1] for w in td] == [0x01, 0x02, 0x03]


def test_upload_rejects_bad_block_sequence():
    script = [
        [(0x7E8, bytes([0x75, 0x10, 0x06]))],
        [(0x7E8, bytes([0x76, 0x02]) + b"\xAA\xBB\xCC\xDD")],  # wrong BSC
    ]
    ch = FakeChannel(script)
    uds = UDSClient(ch, 0x7E0, 0x7E8)
    with pytest.raises(Exception):
        uds.upload(0x8000, 8)
