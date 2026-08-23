"""Deploy verification must not lose to clock granularity.

Root cause of a rare flake that roamed across every deploy test on the Windows
VM and never reproduced on Linux: verify_export_mtime compared a filesystem
mtime against a time.time() sample with a strict `>`. Measured on that box over
2000 samples, a file written immediately AFTER the sample reported
`mtime <= started` **75.35%** of the time (median delta +0.000 ms).

It is unrecoverable by polling — mtimes do not change after the export — so the
loop burned its whole budget and reported a SUCCESSFUL deploy as failed.
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from service import core


def test_confirms_when_mtime_ties_the_start_sample(cfg: core.Config, tmp_path: Path) -> None:
    """The dominant real case: export lands in the SAME clock tick as start."""
    tree = tmp_path / "rtree"
    tree.mkdir()
    (tree / "f.yaml").write_text("x", encoding="utf-8")
    started = core._project_tree_max_mtime(tree)  # exact tie
    out = core.verify_export_mtime(cfg, tree, started)
    assert out["confirmed_at"] is not None, out


def test_confirms_when_mtime_is_a_hair_below_start(cfg: core.Config, tmp_path: Path) -> None:
    """min delta measured -0.000 ms: mtime can round just under the sample."""
    tree = tmp_path / "rtree"
    tree.mkdir()
    (tree / "f.yaml").write_text("x", encoding="utf-8")
    started = core._project_tree_max_mtime(tree) + 0.01   # inside tolerance
    assert core.verify_export_mtime(cfg, tree, started)["confirmed_at"] is not None


def test_still_refuses_a_genuinely_stale_tree(cfg: core.Config, tmp_path: Path) -> None:
    """The tolerance absorbs clock granularity ONLY. A tree that did not change
    during the deploy is seconds old and must still fail, or the check is
    decorative."""
    tree = tmp_path / "rtree"
    tree.mkdir()
    (tree / "f.yaml").write_text("x", encoding="utf-8")
    started = core._project_tree_max_mtime(tree) + 0.5   # 10x the tolerance
    # deadline runs from `started`, so keep the offset small or this polls for it
    fast = dataclasses.replace(cfg, verify_timeout_seconds=0, verify_poll_seconds=0.01)
    assert core.verify_export_mtime(fast, tree, started)["confirmed_at"] is None


def test_poll_probes_at_least_once_past_the_deadline(cfg: core.Config) -> None:
    """The deadline runs from DEPLOY start, not verify start, so a deploy whose
    export ate the budget must not report 'not confirmed' having never looked."""
    calls: list[int] = []

    def probe():
        calls.append(1)
        return True, "2026-01-01T00:00:00+00:00"

    out = core._poll_until(cfg, time.time() - 3600, "export_mtime", probe)
    assert calls, "probe never ran — verification reported on a check it skipped"
    assert out["confirmed_at"] is not None
