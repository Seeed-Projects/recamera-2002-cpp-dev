from __future__ import annotations

import random
import time

import pytest

from devkit import live


def test_fragment_and_reassemble_out_of_order():
    payload = bytes(range(256)) * 20  # 5120 bytes -> 5 fragments at 1200
    packets = live.fragment(
        live.VIDEO, payload, seq=7, ts_ms=123456, width=1920, height=1080,
        flags=live.FLAG_KEYFRAME, max_payload=1200,
    )
    assert len(packets) == 5
    for index, packet in enumerate(packets):
        header = live.parse_header(packet)
        assert header["magic"] == live.MAGIC
        assert header["type"] == "video"
        assert header["seq"] == 7
        assert header["frag_idx"] == index
        assert header["frag_count"] == 5
        assert header["keyframe"] is True

    reassembler = live.Reassembler()
    shuffled = packets[:]
    random.Random(1).shuffle(shuffled)
    completed = None
    for packet in shuffled:
        completed = reassembler.add(packet, ("127.0.0.1", 5000))
    assert completed is not None
    assert completed.payload == payload
    assert completed.keyframe is True
    assert reassembler.stats.completed == 1
    assert reassembler.pending == 0


def test_missing_fragment_is_dropped_after_expiry():
    payload = b"x" * 3000
    packets = live.fragment(live.VIDEO, payload, seq=9, max_payload=1000)
    reassembler = live.Reassembler(timeout_s=0.01)
    assert reassembler.add(packets[0], ("a", 1)) is None
    assert reassembler.pending == 1
    assert reassembler.expire(now=time.monotonic() + 1) == 1
    assert reassembler.pending == 0
    assert reassembler.stats.dropped >= 1


def test_duplicate_fragment_is_ignored():
    packets = live.fragment(live.VIDEO, b"x" * 2000, seq=1, max_payload=1000)
    reassembler = live.Reassembler()
    assert reassembler.add(packets[0], ("a", 1)) is None
    assert reassembler.add(packets[0], ("a", 1)) is None
    assert reassembler.stats.duplicates == 1
    assert reassembler.add(packets[1], ("a", 1)) is not None


def test_bad_magic_is_rejected():
    with pytest.raises(ValueError):
        live.parse_header(b"\x00" * 32)
    reassembler = live.Reassembler()
    assert reassembler.add(b"\x00" * 32, ("a", 1)) is None
    assert reassembler.stats.malformed == 1


def test_control_roundtrip():
    line = live.format_control("set", 12, {"confidence": 0.7, "force_idr": True})
    assert line == b"RCC1 SET seq=12 confidence=0.7 force_idr=1\n"
    parsed = live.parse_control(line)
    assert parsed["command"] == "SET"
    assert parsed["params"]["seq"] == 12
    assert parsed["params"]["confidence"] == 0.7
    assert parsed["params"]["force_idr"] == 1

    ack = live.parse_control("RCC1 ACK seq=12 ok=1 applied=confidence\n")
    assert ack["command"] == "ACK"
    assert ack["params"]["ok"] == 1
    assert ack["params"]["applied"] == "confidence"


def test_transform_box_stretch():
    box = live.transform_box(
        {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.2},
        frame_w=1920, frame_h=1080, model_w=640, model_h=640,
        preprocess="stretch",
    )
    assert box["x1"] == pytest.approx(864)
    assert box["y1"] == pytest.approx(432)
    assert box["x2"] == pytest.approx(1056)
    assert box["y2"] == pytest.approx(648)


def test_transform_box_letterbox():
    box = live.transform_box(
        {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.2},
        frame_w=1920, frame_h=1080, model_w=640, model_h=640,
        preprocess="letterbox", scale=1 / 3, pad_y=140,
    )
    assert box["cx"] == pytest.approx(960)
    assert box["cy"] == pytest.approx(540)
    assert box["w"] == pytest.approx(192)
    assert box["h"] == pytest.approx(384)
    assert box["y1"] == pytest.approx(348)
    assert box["y2"] == pytest.approx(732)


def test_h264_nal_helpers():
    idr = b"\x00\x00\x00\x01\x65\x88\x84"
    sps = b"\x00\x00\x00\x01\x67\x42"
    p_frame = b"\x00\x00\x01\x41\x9a"
    assert live.h264_contains_idr(idr) is True
    assert live.h264_contains_idr(p_frame) is False
    assert live.h264_contains_parameter_set(sps) is True
    assert live.h264_contains_parameter_set(p_frame) is False


def test_transform_point_frame_space():
    point = live.transform_point(
        0.5, 0.25, space="frame", frame_w=1920, frame_h=1080,
        model_w=640, model_h=640,
    )
    assert point == {"x": 960.0, "y": 270.0}


def test_transform_point_matches_box_center():
    det = {"x": 0.37, "y": 0.61, "w": 0.1, "h": 0.2}
    fixtures = [
        ("stretch", 1.0, 0, 0),
        ("letterbox", 1 / 3, 0, 140),
        ("crop", 640 / 1080, 0, 0),
    ]
    for preprocess, scale, pad_x, pad_y in fixtures:
        box = live.transform_box(
            det, frame_w=1920, frame_h=1080, model_w=640, model_h=640,
            preprocess=preprocess, pad_x=pad_x, pad_y=pad_y, scale=scale,
        )
        point = live.transform_point(
            det["x"], det["y"], space="model", frame_w=1920, frame_h=1080,
            model_w=640, model_h=640, preprocess=preprocess,
            pad_x=pad_x, pad_y=pad_y, scale=scale,
        )
        assert point["x"] == pytest.approx(box["cx"])
        assert point["y"] == pytest.approx(box["cy"])
