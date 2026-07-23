"""Tests for core.cdp_ocr_runtime — the opt-in tesseract read-back fallback.

Offline: the screenshot capture (cdp_screenshot_runtime) is stubbed, shutil.which
and the tesseract subprocess are faked. The real tesseract is validated on the
Windows box; these cover the wrapper's routing + failure interpretation."""
from __future__ import annotations

import shutil

import pytest

from service import core
from service.tests.conftest import FakeProc, make_fake_runner


def _stub_shot(monkeypatch, **over) -> None:
    base = {"state": "succeeded", "size_bytes": 42, "navigated": True}
    base.update(over)
    monkeypatch.setattr(core, "cdp_screenshot_runtime",
                        lambda *a, **k: base)


_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
    "\tleft\ttop\twidth\theight\tconf\ttext"
)


def _tsv(*words: tuple[str, float], line_step: int = 0) -> str:
    """Build a minimal level-5 tesseract TSV from (text, conf) pairs.

    Words share one (block, par) group; each gets a distinct line_num when
    line_step>0 (so _reconstruct_ocr_text puts them on separate lines) or a
    shared line with incrementing word_num when line_step==0."""
    rows = [_TSV_HEADER]
    for i, (text, conf) in enumerate(words):
        line_num = 1 + (i if line_step else 0)
        word_num = 1 if line_step else 1 + i
        rows.append(
            f"5\t1\t1\t1\t{line_num}\t{word_num}"
            f"\t{10 + i * 90}\t10\t80\t30\t{conf}\t{text}")
    return "\n".join(rows) + "\n"


def test_ocr_returns_recognized_text(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    # TSV mode: "Hello Optix" on line 1 (word_num 1,2), "Start" on line 2 ->
    # reconstructed text preserves the line break.
    tsv = (
        _TSV_HEADER + "\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t80\t30\t95.0\tHello\n"
        "5\t1\t1\t1\t1\t2\t95\t10\t100\t30\t93.0\tOptix\n"
        "5\t1\t1\t1\t2\t1\t10\t50\t60\t30\t90.0\tStart\n"
    )
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "succeeded"
    assert "Hello Optix" in out["text"]
    # reconstruction preserves per-line grouping (words space-joined, lines
    # newline-joined) — not byte-identical to tesseract's text renderer.
    assert out["text"] == "Hello Optix\nStart"
    assert out["size_bytes"] == 42 and out["navigated"] is True
    assert "low_confidence" not in out  # all words high-conf
    # invoked tesseract in TSV mode with a psm and stdout target
    cmd = runner.calls[0][0]
    assert cmd[0] == "/usr/bin/tesseract"
    assert "stdout" in cmd and "--psm" in cmd and "tsv" in cmd


def test_ocr_missing_binary_is_soft_failure(cfg, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # screenshot must NOT even be attempted when tesseract is absent
    monkeypatch.setattr(core, "cdp_screenshot_runtime",
                        lambda *a, **k: pytest.fail("should not capture"))
    out = core.cdp_ocr_runtime(cfg)
    assert out["state"] == "failed"
    assert out["error"] == "tesseract_not_installed"
    assert "PATH" in out["hint"]


def test_ocr_propagates_screenshot_failure(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch, state="failed", error="cdp_unavailable")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: pytest.fail("tesseract should not run"))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "failed" and out["error"] == "cdp_unavailable"


def test_ocr_reports_tesseract_nonzero(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: FakeProc(1, "", "leptonica error"))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "failed" and "leptonica" in out["error"]


# ---- read_text (core.cdp_read_text_runtime — region-clipped OCR, S4 feature 2) --

def test_read_text_returns_recognized_text(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch, region=[10.0, 20.0, 30.0, 40.0])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, _tsv(("SP-101", 92.0))))
    out = core.cdp_read_text_runtime(cfg, region=[0.1, 0.1, 0.2, 0.2], runner=runner)
    assert out["state"] == "succeeded"
    assert out["text"] == "SP-101"
    assert out["region"] == [10.0, 20.0, 30.0, 40.0]
    assert out["confidence"] == {"mean": 0.92, "min": 0.92}
    cmd = runner.calls[0][0]
    assert cmd[0] == "/usr/bin/tesseract"
    assert "stdout" in cmd and "--psm" in cmd and "tsv" in cmd


def test_read_text_forwards_region_to_screenshot(cfg, monkeypatch) -> None:
    seen = {}

    def fake_shot(cfg_, save_path=None, navigate_url=None, settle_seconds=None,
                  region=None, **kw):
        seen["region"] = region
        return {"state": "succeeded", "size_bytes": 1, "navigated": False,
                "region": region}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_shot)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, "x"))
    core.cdp_read_text_runtime(cfg, region=[0.0, 0.0, 0.5, 0.5], runner=runner)
    assert seen["region"] == [0.0, 0.0, 0.5, 0.5]


def test_read_text_missing_binary_is_soft_failure(cfg, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    # screenshot must NOT even be attempted when tesseract is absent
    monkeypatch.setattr(core, "cdp_screenshot_runtime",
                        lambda *a, **k: pytest.fail("should not capture"))
    out = core.cdp_read_text_runtime(cfg)
    assert out["state"] == "failed"
    assert out["error"] == "tesseract_not_installed"
    assert "PATH" in out["hint"]


def test_read_text_propagates_screenshot_failure(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch, state="failed", error="bad_region", region=None)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: pytest.fail("tesseract should not run"))
    out = core.cdp_read_text_runtime(cfg, runner=runner)
    assert out["state"] == "failed" and out["error"] == "bad_region"
    assert out["region"] is None


# ---- OCR TSV confidence signal (U8 Part B) -------------------------------

def test_ocr_confidence_reported_when_high(cfg, monkeypatch) -> None:
    """A high-confidence pass surfaces confidence:{mean,min} as [0,1] fractions
    with NO low_confidence/next_step marker."""
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    tsv = _tsv(("Alarm", 96.0), ("Active", 90.0))
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "succeeded"
    assert out["text"] == "Alarm Active"
    assert out["confidence"] == {"mean": 0.93, "min": 0.90}
    assert out["confidence"]["mean"] >= cfg.ocr_conf_threshold
    assert "low_confidence" not in out and "next_step" not in out


def test_ocr_low_confidence_nudge_when_below_threshold(cfg, monkeypatch) -> None:
    """A below-threshold mean flags low_confidence=True + a next_step nudge that
    points at ground-truth reads."""
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    tsv = _tsv(("Blur", 15.0), ("Noise", 20.0))
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "succeeded"
    assert out["confidence"]["mean"] < cfg.ocr_conf_threshold
    assert out["low_confidence"] is True
    assert "optix_describe_node" in out["next_step"]
    assert "return_image=true" in out["next_step"]


def test_ocr_no_words_has_no_confidence_field(cfg, monkeypatch) -> None:
    """An empty frame (no word rows) yields empty text and NO confidence key —
    there is no aggregate to report."""
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, _TSV_HEADER + "\n"))
    out = core.cdp_ocr_runtime(cfg, runner=runner)
    assert out["state"] == "succeeded"
    assert out["text"] == "" and "confidence" not in out
    assert "low_confidence" not in out


def test_read_text_confidence_reported_when_high(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch, region=[1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    tsv = _tsv(("Setpoint", 88.0), ("42", 94.0))
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_read_text_runtime(cfg, region=[0.1, 0.1, 0.2, 0.2], runner=runner)
    assert out["state"] == "succeeded"
    assert out["text"] == "Setpoint 42"
    assert out["confidence"] == {"mean": 0.91, "min": 0.88}
    assert out["region"] == [1.0, 2.0, 3.0, 4.0]
    assert "low_confidence" not in out


def test_read_text_low_confidence_nudge_when_below_threshold(cfg, monkeypatch) -> None:
    _stub_shot(monkeypatch, region=[1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    tsv = _tsv(("smudge", 12.0), ("blur", 18.0))
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_read_text_runtime(cfg, region=[0.1, 0.1, 0.2, 0.2], runner=runner)
    assert out["state"] == "succeeded"
    assert out["low_confidence"] is True
    assert "optix_describe_node" in out["next_step"]
    assert out["region"] == [1.0, 2.0, 3.0, 4.0]


def test_ocr_confidence_threshold_honors_config(cfg, monkeypatch) -> None:
    """The gate reads cfg.ocr_conf_threshold — a raised threshold flips an
    otherwise-fine pass to low_confidence."""
    import dataclasses
    strict = dataclasses.replace(cfg, ocr_conf_threshold=0.95)
    _stub_shot(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    tsv = _tsv(("Ready", 90.0), ("Go", 88.0))  # mean 0.89, below 0.95
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, tsv))
    out = core.cdp_ocr_runtime(strict, runner=runner)
    assert out["low_confidence"] is True


# ---- find_text tesseract TSV behaviors (S4 feature 3) --------------------

# A realistic tesseract `--psm 6 tsv` fixture:
#  - "Start Button" on line 1, two adjacent words (word_num 1, 2) -> joins
#  - "Foo Button" on line 2 -> a second, unrelated "Button" that must NOT
#    join with line 1's "Start" (different line_num)
#  - "Exit Now Confirm" on line 3, where "Now" is low-confidence (< 40) and
#    must be dropped, which breaks the Exit/Confirm adjacency too
_TSV_FIXTURE = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t1000\t800\t-1\t\n"
    "4\t1\t1\t1\t1\t0\t10\t10\t300\t30\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t10\t10\t80\t30\t95.5\tStart\n"
    "5\t1\t1\t1\t1\t2\t95\t10\t100\t30\t92.0\tButton\n"
    "4\t1\t1\t1\t2\t0\t10\t50\t300\t30\t-1\t\n"
    "5\t1\t1\t1\t2\t1\t10\t50\t60\t30\t88.0\tFoo\n"
    "5\t1\t1\t1\t2\t2\t75\t50\t100\t30\t90.0\tButton\n"
    "4\t1\t1\t1\t3\t0\t10\t90\t300\t30\t-1\t\n"
    "5\t1\t1\t1\t3\t1\t10\t90\t60\t30\t91.0\tExit\n"
    "5\t1\t1\t1\t3\t2\t75\t90\t70\t30\t15.0\tNow\n"
    "5\t1\t1\t1\t3\t3\t150\t90\t90\t30\t93.0\tConfirm\n"
)


def test_parse_tsv_keeps_only_word_level_rows() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    # 7 word-level (level==5) rows; the level 1/4 aggregate rows are dropped
    assert len(words) == 7
    assert all(w["text"] for w in words)
    assert {w["text"] for w in words} == {
        "Start", "Button", "Foo", "Exit", "Now", "Confirm"}


def test_match_multiword_joins_adjacent_words_same_line() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    matches = core._match_tsv_words(words, "Start Button")
    assert len(matches) == 1
    assert matches[0]["text"] == "Start Button"
    assert matches[0]["bbox_px"] == [10.0, 10.0, 185.0, 30.0]
    assert matches[0]["confidence"] == 92.0  # min() of the two joined words


def test_match_is_case_insensitive_and_finds_all_occurrences() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    matches = core._match_tsv_words(words, "button")
    assert len(matches) == 2  # line 1's "Button" and line 2's "Button"


def test_match_does_not_join_words_across_lines() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    # "Button" (line 1, word 2) followed by "Foo" (line 2, word 1) are
    # adjacent in reading order but on different lines -> must not join
    assert core._match_tsv_words(words, "Button Foo") == []


def test_match_skips_low_confidence_word_breaking_multiword_join() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    # "Now" has conf 15 (< 40) -> filtered out entirely, which breaks the
    # word_num adjacency between Exit (1) and Confirm (3)
    assert core._match_tsv_words(words, "Exit Now Confirm") == []
    assert core._match_tsv_words(words, "Now") == []  # low-conf word never matches on its own
    assert len(core._match_tsv_words(words, "Exit")) == 1  # neighboring high-conf words still match


def test_match_no_match_returns_empty_list() -> None:
    words = core._parse_tesseract_tsv(_TSV_FIXTURE)
    assert core._match_tsv_words(words, "Nonexistent Label") == []


def test_find_text_missing_binary_is_soft_failure(cfg, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    out = core.cdp_find_text_runtime(cfg, "Start")
    assert out["state"] == "failed"
    assert out["error"] == "tesseract_not_installed"
    assert out["found"] is False and out["matches"] == []
    assert "PATH" in out["hint"]
