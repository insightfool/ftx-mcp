"""PID-aware single-writer deploy lock (see docs/architecture.md, Concurrency).

The lock file lives at `<state_dir>/deploy.lock` and contains JSON with
pid + started_at + caller + epoch + pid_create_time. Stale locks (dead
PID, recycled PID, or older than stale_seconds) are broken on acquire.
A live lock raises LockHeld so the HTTP/MCP layer can return a 409 with
the in-flight info.

Fencing: because the age cutoff can break a lock whose holder is still
alive (a hung deploy renews forever otherwise -> deadlock), eviction is
made fail-closed. Each acquire stamps a monotonically increasing `epoch`
(prev + 1, incremented even when breaking a stale lock) and the holder's
`pid_create_time` (psutil process start time, to catch PID reuse). A
holder re-validates ownership via `check_still_held()` immediately before
each irreversible deploy checkpoint; if its lock was broken and re-taken
by a concurrent acquirer, the on-disk epoch/create-time no longer match
and it raises DeployLockEvicted rather than corrupting a shared tree.
`_release()` only unlinks a lock whose epoch is still ours, so an evicted
holder never deletes its successor's lock.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class LockHeld(Exception):
    """Raised when the deploy lock is held by a live, non-stale process."""

    def __init__(self, lock_state: dict):
        self.lock_state = lock_state
        super().__init__(f"deploy lock held: {lock_state}")


class DeployLockEvicted(Exception):
    """Raised when a fencing re-check finds our own lock was broken/stolen
    by a concurrent acquirer between DeployLock.acquire() and an
    irreversible deploy checkpoint."""

    def __init__(self, lock_state: dict | None):
        self.lock_state = lock_state
        super().__init__(f"deploy lock evicted: {lock_state}")


# create_time comparison tolerance (seconds). psutil reports a stable
# create_time for a live process across calls, but a small epsilon guards
# against coarse-clock / platform rounding (mirrors the mtime-coarseness
# idiom around core.py::_atomic_swap). See U4 open question #1 — confirm
# the right value against the live Windows deploy box.
_CREATE_TIME_EPSILON = 2.0


def _pid_create_time(pid: int) -> float | None:
    """Process start time (epoch seconds) for `pid`, or None if unavailable.

    psutil is a hard dependency (pyproject.toml), so this normally resolves;
    ImportError / NoSuchProcess / AccessDenied all collapse to None ("epoch
    unavailable") rather than raising, mirroring `_pid_alive`'s defensive
    fallback so a broken/partial venv degrades to plain PID-alive semantics.
    """
    try:
        import psutil  # type: ignore
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    """Return True if `pid` is a live process. Cross-platform."""
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h == 0:
                return False
            kernel32.CloseHandle(h)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return True


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def _epoch_of(state: dict | None) -> int:
    """Epoch recorded in a lock state, or 0 for a missing / old-format lock."""
    if not state:
        return 0
    epoch = state.get("epoch")
    return epoch if isinstance(epoch, int) else 0


@dataclass
class DeployLock:
    path: Path
    caller: str = "unknown"
    stale_seconds: float = 600.0  # 10 min stale-lock threshold

    def __post_init__(self) -> None:
        # Identity of the lock WE wrote, captured in _write_self and compared
        # by check_still_held / _release. None until we have acquired.
        self._my_epoch: int | None = None
        self._my_create_time: float | None = None

    @contextmanager
    def acquire(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._try_acquire()
        try:
            yield
        finally:
            self._release()

    def read_state(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def is_stale(self, state: dict) -> bool:
        pid = state.get("pid")
        if not isinstance(pid, int):
            return True
        if not _pid_alive(pid):
            return True
        # PID-alive but recycled: the OS handed this PID to an unrelated
        # process after the original holder died. A recorded pid_create_time
        # that no longer matches the live process at that PID means the holder
        # is dead. Absent pid_create_time (old-format lock, or psutil failed
        # at write time) falls back to today's PID-alive-only semantics.
        recorded_ct = state.get("pid_create_time")
        if recorded_ct is not None:
            actual_ct = _pid_create_time(pid)
            if actual_ct is not None and abs(actual_ct - recorded_ct) > _CREATE_TIME_EPSILON:
                return True
        started_at = state.get("started_at")
        if isinstance(started_at, str):
            try:
                ts = _dt.datetime.fromisoformat(started_at).timestamp()
            except ValueError:
                return True
            if time.time() - ts > self.stale_seconds:
                return True
        return False

    def _try_acquire(self) -> None:
        # Try to win an exclusive create; if a lock file already exists, break
        # it only when it is NOT live (stale/dead/corrupt) and retry once. This
        # never unlinks a live lock (so a concurrent acquirer's valid lock is
        # safe) and never lets two acquirers both pass — the O_CREAT|O_EXCL in
        # _write_self is the single serialization point.
        prev_epoch = 0
        for _ in range(2):
            existing = self.read_state()
            if existing and not self.is_stale(existing):
                raise LockHeld(existing)
            prev_epoch = max(prev_epoch, _epoch_of(existing))
            try:
                self._write_self(prev_epoch + 1)
                return
            except FileExistsError:
                current = self.read_state()
                if current and not self.is_stale(current):
                    raise LockHeld(current) from None
                # Not live (stale/dead/corrupt/unparseable): break it, then the
                # loop retries the exclusive create. Carry the broken lock's
                # epoch forward so the successor's epoch stays monotonic
                # (prev + 1 on every acquire, including breaks).
                prev_epoch = max(prev_epoch, _epoch_of(current))
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        # Both create attempts lost to a live concurrent acquirer. Fail closed.
        current = self.read_state()
        raise LockHeld(
            current or {"pid": None, "note": "held by concurrent acquirer"}
        ) from None

    def check_still_held(self) -> None:
        """Re-validate that the on-disk lock still identifies THIS process
        instance. Call immediately before an irreversible side effect.

        Raises DeployLockEvicted if the lock was broken/stolen out from under
        us since acquire() — i.e. the on-disk epoch/pid/create-time no longer
        match the lock we wrote. This is the fail-closed half of the age-based
        eviction policy: a holder whose lock was age-broken and re-taken
        detects it before mutating the shared runtime tree.
        """
        current = self.read_state()
        if (current is None
                or current.get("pid") != os.getpid()
                or _epoch_of(current) != self._my_epoch
                or current.get("pid_create_time") != self._my_create_time):
            raise DeployLockEvicted(current)

    def _write_self(self, epoch: int = 1) -> None:
        """Atomically create the lock file, failing if it already exists.

        O_CREAT|O_EXCL makes the create the single serialization point: exactly
        one concurrent acquirer can create the file, the losers get
        FileExistsError. This replaces a check-then-os.replace(tmp, path) that
        unconditionally overwrote, letting two acquirers that both saw "no lock"
        each write and both proceed.

        Stamps the fencing identity (`epoch`, `pid_create_time`) and caches it
        on the instance so check_still_held / _release can compare against it.
        """
        create_time = _pid_create_time(os.getpid())
        state = {
            "pid": os.getpid(),
            "started_at": _now_iso(),
            "caller": self.caller,
            "epoch": epoch,
            "pid_create_time": create_time,
        }
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, json.dumps(state).encode("utf-8"))
        finally:
            os.close(fd)
        self._my_epoch = epoch
        self._my_create_time = create_time

    def _release(self) -> None:
        # Only unlink a lock that is still OURS (epoch match). If our lock was
        # age-broken and re-acquired by a successor, the on-disk epoch differs
        # and we must NOT delete their lock (that would let a third acquirer in
        # while the successor is mid-deploy).
        try:
            current = self.read_state()
            if current is not None and _epoch_of(current) != self._my_epoch:
                return
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
