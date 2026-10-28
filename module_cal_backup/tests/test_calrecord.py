"""Tests for the structured cal-ID record store (offline, synthetic/example)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

from module_cal_backup import calrecord as cr

EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "examples", "sierra_k43_calrecord.json")


def test_load_example_record():
    rec = cr.load_record(EXAMPLE)
    assert rec.vin == "3GTU2VEC4EG204931"
    assert rec.controller_code == "K43"
    # 5 OS + 6 calibration + 1 system = 12 segments
    assert len(rec.segments) == 12
    assert any(s.part_number == "84391470" for s in rec.segments)
    assert rec.key() == "3GTU2VEC4EG204931_K43"


def test_save_list_and_update_in_place(tmp_path):
    rec = cr.load_record(EXAMPLE)
    cr.save_record(rec, tmp_path)

    recs = cr.list_records(tmp_path)
    assert len(recs) == 1
    assert recs[0]["controller_code"] == "K43"

    # re-saving the same (VIN, controller) updates, never duplicates
    rec.segments[0].cvn = "AABBCCDD"
    cr.save_record(rec, tmp_path)
    recs = cr.list_records(tmp_path)
    assert len(recs) == 1

    index = json.loads(
        (tmp_path / "cal_records" / "index.json").read_text())
    # one CVN now filled -> missing count drops from 12 to 11
    assert index[0]["segments_without_cvn"] == 11


def test_rejects_bad_segment_name(tmp_path):
    rec = cr.CalRecord(vin="V", controller_code="K43", controller_name="x",
                       segments=[cr.CalSegment("bogus", "123")])
    with pytest.raises(ValueError):
        cr.save_record(rec, tmp_path)


def test_rejects_missing_keys(tmp_path):
    rec = cr.CalRecord(vin="", controller_code="", controller_name="x")
    with pytest.raises(ValueError):
        cr.save_record(rec, tmp_path)
