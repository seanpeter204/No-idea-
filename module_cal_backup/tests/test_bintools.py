"""Off-vehicle tests for the .bin analysis/archive helpers (synthetic data)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from module_cal_backup import bintools as bt


def _synth() -> bytes:
    # bootloader-ish block, big 0xFF gap, cal block with an embedded string,
    # another gap, app block. Data blocks avoid 0x00/0xFF so their edges
    # don't merge into the fill gaps.
    boot = bytes((i % 254) + 1 for i in range(256))
    gap = b"\xFF" * 512
    cal = b"CALID GM12345678" + bytes([7] * 200) + bytes([9] * 300)
    gap2 = b"\x00" * 512
    app = bytes([(i % 250) + 1 for i in range(1024)])
    return boot + gap + cal + gap2 + app


def test_checksums_stable_and_shaped():
    data = _synth()
    cs = bt.checksums(data)
    assert cs["len"] == len(data)
    assert len(cs["sha256"]) == 64
    assert len(cs["crc32"]) == 8
    # sum8 is the low byte of the total
    assert cs["sum8"] == sum(data) & 0xFF


def test_segment_scan_finds_three_regions():
    data = _synth()
    segs = bt.segment_scan(data, min_gap=256)
    assert len(segs) == 3
    # first region is the 256-byte bootloader block at offset 0
    assert segs[0].start == 0 and segs[0].length == 256


def test_find_strings_surfaces_calid():
    data = _synth()
    hits = [s for _, s in bt.find_strings(data, min_len=5)]
    assert any("CALID GM12345678" in s for s in hits)


def test_diff_identical():
    data = _synth()
    r = bt.diff(data, data)
    assert r.equal and r.changed_bytes == 0


def test_diff_detects_changed_range():
    a = bytearray(_synth())
    b = bytearray(a)
    for i in range(1000, 1004):
        b[i] ^= 0xFF
    r = bt.diff(bytes(a), bytes(b))
    assert not r.equal
    assert r.changed_bytes == 4
    assert r.ranges[0].start == 1000 and r.ranges[0].end == 1004


def test_diff_length_mismatch_flagged():
    a = _synth()
    b = a[:-100]
    r = bt.diff(a, b)
    assert not r.equal
    assert "LENGTH MISMATCH" in r.note


def test_archive_writes_and_dedupes(tmp_path):
    src = tmp_path / "eps.bin"
    src.write_bytes(_synth())
    out = tmp_path / "arch"

    e1 = bt.archive(src, out, module="eps", vin="1G1XX", note="first")
    assert (out / e1["stored_file"]).exists()
    assert (out / "archive_index.json").exists()
    assert not e1.get("already_archived")

    # re-archiving identical content must not make a second copy
    e2 = bt.archive(src, out, module="eps")
    assert e2.get("already_archived")
    assert e2["stored_file"] == e1["stored_file"]
    bins = list(out.glob("*.bin"))
    assert len(bins) == 1
