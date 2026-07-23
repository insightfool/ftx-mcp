"""Tests for service.deploy_lock — PID-aware stale-lock handling."""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

import pytest

from service.deploy_lock import (
    DeployLock,
    DeployLockEvicted,
    LockHeld,
    _pid_create_time,
)


def test_lock_acquires_when_no_existing(state_dir: Path) -> None:
    lock = DeployLock(state_dir / "deploy.lock", caller="test")
    with lock.acquire():
        state = lock.read_state()
        assert state is not None
        assert state["pid"] == os.getpid()
        assert state["caller"] == "test"
    # released
    assert not (state_dir / "deploy.lock").exists()


def test_written_state_includes_fencing_fields(state_dir: Path) -> None:
    """The lock we write carries an epoch (>=1) and pid_create_time (a float,
    since psutil is a hard dependency) so successors can fence on it."""
    lock = DeployLock(state_dir / "deploy.lock", caller="test")
    with lock.acquire():
        state = lock.read_state()
        assert state is not None
        assert state["epoch"] == 1
        assert isinstance(state["pid_create_time"], float)


def test_lock_blocks_when_held_by_live_pid(state_dir: Path) -> None:
    other_lock = state_dir / "deploy.lock"
    state_dir.mkdir(exist_ok=True)
    state = {
        "pid": os.getpid(),  # we are alive
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "elsewhere",
    }
    other_lock.write_text(json.dumps(state), encoding="utf-8")

    lock = DeployLock(other_lock, caller="newcomer")
    with pytest.raises(LockHeld) as excinfo:
        with lock.acquire():
            pass
    assert excinfo.value.lock_state["caller"] == "elsewhere"


def test_lock_breaks_when_pid_is_dead(state_dir: Path) -> None:
    dead_pid = 999_999_999  # unreachable
    state = {
        "pid": dead_pid,
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "ghost",
    }
    lock_path = state_dir / "deploy.lock"
    lock_path.write_text(json.dumps(state), encoding="utf-8")

    lock = DeployLock(lock_path, caller="newcomer")
    with lock.acquire():
        st = lock.read_state()
        assert st is not None
        assert st["pid"] == os.getpid()
        assert st["caller"] == "newcomer"


def test_lock_breaks_when_stale_by_age(state_dir: Path) -> None:
    state = {
        "pid": os.getpid(),
        "started_at": (_dt.datetime.now(_dt.UTC)
                       - _dt.timedelta(hours=2)).isoformat(timespec="seconds"),
        "caller": "ancient",
    }
    lock_path = state_dir / "deploy.lock"
    lock_path.write_text(json.dumps(state), encoding="utf-8")

    lock = DeployLock(lock_path, caller="newcomer", stale_seconds=60)
    with lock.acquire():
        st = lock.read_state()
        assert st is not None
        assert st["caller"] == "newcomer"


def test_lock_breaks_on_corrupt_state(state_dir: Path) -> None:
    lock_path = state_dir / "deploy.lock"
    lock_path.write_text("not-json", encoding="utf-8")
    lock = DeployLock(lock_path, caller="newcomer")
    with lock.acquire():
        st = lock.read_state()
        assert st is not None
        assert st["caller"] == "newcomer"


def test_write_self_is_atomic_exclusive(state_dir: Path) -> None:
    """_write_self creates the lock exclusively (O_CREAT|O_EXCL): a second
    writer that saw 'no lock' cannot overwrite the first and proceed. This is
    the serialization point that prevents two concurrent deploys from both
    holding the lock."""
    state_dir.mkdir(exist_ok=True)
    lock_path = state_dir / "deploy.lock"
    DeployLock(lock_path, caller="first")._write_self()  # first writer wins
    with pytest.raises(FileExistsError):
        DeployLock(lock_path, caller="second")._write_self()


def test_acquire_fails_closed_when_live_lock_appears_after_check(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a live lock is created between our staleness check and our exclusive
    create (the classic TOCTOU), acquisition must fail closed with LockHeld,
    not clobber the other holder."""
    state_dir.mkdir(exist_ok=True)
    lock_path = state_dir / "deploy.lock"
    lock = DeployLock(lock_path, caller="racer")

    live = {
        "pid": os.getpid(),  # alive + fresh -> not stale
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "winner",
    }
    original_read = lock.read_state
    calls = {"n": 0}

    def read_state_racing():
        # First read (staleness check) sees no lock; a concurrent winner then
        # creates it, so the exclusive create fails and the re-read sees it.
        calls["n"] += 1
        if calls["n"] == 1:
            lock_path.write_text(json.dumps(live), encoding="utf-8")
            return None
        return original_read()

    monkeypatch.setattr(lock, "read_state", read_state_racing)
    with pytest.raises(LockHeld) as excinfo:
        lock._try_acquire()
    assert excinfo.value.lock_state["caller"] == "winner"


# ---- fencing: epoch + pid create-time (U4) ---------------------------


def test_old_format_lock_without_epoch_parses(state_dir: Path) -> None:
    """A pre-fencing lock (pid/started_at/caller only, no epoch or
    pid_create_time) held by a live pid is still respected as a live hold —
    old-format lockfiles are NOT treated as corrupt/stale-by-default."""
    lock_path = state_dir / "deploy.lock"
    state = {
        "pid": os.getpid(),  # alive
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "legacy",
    }
    lock_path.write_text(json.dumps(state), encoding="utf-8")

    lock = DeployLock(lock_path, caller="newcomer")
    assert lock.is_stale(state) is False
    with pytest.raises(LockHeld) as excinfo:
        with lock.acquire():
            pass
    assert excinfo.value.lock_state["caller"] == "legacy"


def test_is_stale_detects_pid_reuse_via_create_time(state_dir: Path) -> None:
    """A live pid whose recorded pid_create_time no longer matches the actual
    process start time = the PID was recycled -> stale, even though the pid is
    alive and the lock is fresh (no age-based staleness)."""
    lock_path = state_dir / "deploy.lock"
    real_ct = _pid_create_time(os.getpid())
    assert real_ct is not None  # psutil is a hard dep
    state = {
        "pid": os.getpid(),  # alive
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "recycled",
        "epoch": 1,
        "pid_create_time": real_ct - 99999.0,  # deliberately wrong
    }
    lock_path.write_text(json.dumps(state), encoding="utf-8")

    lock = DeployLock(lock_path, caller="newcomer")
    assert lock.is_stale(state) is True
    # ...and a newcomer therefore takes it over.
    with lock.acquire():
        st = lock.read_state()
        assert st is not None
        assert st["caller"] == "newcomer"


def test_acquire_bumps_epoch_when_breaking_stale_lock(state_dir: Path) -> None:
    """Breaking a stale lock still increments the epoch (prev + 1), so a
    successor's epoch stays monotonic across a break."""
    lock_path = state_dir / "deploy.lock"
    stale = {
        "pid": 999_999_999,  # dead
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "caller": "ghost",
        "epoch": 5,
        "pid_create_time": None,
    }
    lock_path.write_text(json.dumps(stale), encoding="utf-8")

    lock = DeployLock(lock_path, caller="newcomer")
    with lock.acquire():
        st = lock.read_state()
        assert st is not None
        assert st["epoch"] == 6  # 5 + 1
        assert st["caller"] == "newcomer"


def test_check_still_held_passes_when_uncontested(state_dir: Path) -> None:
    lock = DeployLock(state_dir / "deploy.lock", caller="test")
    with lock.acquire():
        lock.check_still_held()  # must not raise


def test_check_still_held_raises_when_lock_file_missing(state_dir: Path) -> None:
    lock = DeployLock(state_dir / "deploy.lock", caller="test")
    with lock.acquire():
        lock.path.unlink()
        with pytest.raises(DeployLockEvicted) as excinfo:
            lock.check_still_held()
        assert excinfo.value.lock_state is None


def test_check_still_held_raises_when_stolen_by_concurrent_acquirer(
    state_dir: Path,
) -> None:
    """If another process breaks our lock and re-acquires it while we are
    mid-deploy, check_still_held raises DeployLockEvicted carrying the NEW
    (stolen) state, not ours."""
    lock = DeployLock(state_dir / "deploy.lock", caller="ours")
    with lock.acquire():
        stolen = {
            "pid": os.getpid(),  # same pid...
            "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            "caller": "thief",
            "epoch": 99,  # ...but a different epoch
            "pid_create_time": (_pid_create_time(os.getpid()) or 0.0) - 12345.0,
        }
        lock.path.write_text(json.dumps(stolen), encoding="utf-8")
        with pytest.raises(DeployLockEvicted) as excinfo:
            lock.check_still_held()
        assert excinfo.value.lock_state["caller"] == "thief"
        assert excinfo.value.lock_state["epoch"] == 99


def test_release_leaves_successors_lock_intact(state_dir: Path) -> None:
    """_release only unlinks a lock whose epoch is still ours. If our lock was
    broken + re-acquired by a successor, releasing must NOT delete the
    successor's lock."""
    lock_path = state_dir / "deploy.lock"
    lock = DeployLock(lock_path, caller="ours")
    with lock.acquire():
        successor = {
            "pid": os.getpid(),
            "started_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            "caller": "successor",
            "epoch": 42,
            "pid_create_time": _pid_create_time(os.getpid()),
        }
        lock_path.write_text(json.dumps(successor), encoding="utf-8")
    # our __exit__/_release ran; the successor's lock survives.
    assert lock_path.exists()
    survived = json.loads(lock_path.read_text(encoding="utf-8"))
    assert survived["caller"] == "successor"
    assert survived["epoch"] == 42
