"""Live per-window read of FT Optix Studio's selected deploy target via UI
Automation.

Windows-ONLY. The read walks the native Qt toolbar of a specific Studio window
(by PID) and returns the Name of the currently-selected deployment target — the
thing an F5 keystroke will actually run. It is a READ-ONLY, background-safe
pre-check (no foreground grab, no cursor, no keystroke); proven on Studio
1.7.1.46 to catch the stale-file divergence where Configuration.xml still claimed
Emulator while the live toolbar was on a hardware panel.

Off Windows (Linux host, CI) and on installs without the `uiautomation` package
or an interactive desktop, every entry point degrades to None. None is the
signal to the caller: "I cannot read the live selection — fall back to the
Configuration.xml advisory." The config-file path in core.py stays the fallback,
so behavior is unchanged wherever this read is unavailable.
"""
from __future__ import annotations


def read_selected_target_name(pid: int, target_names: set[str]) -> str | None:
    """Return the live selected deploy-target Name for Studio window `pid`.

    Proven per-window read (background-safe, no foreground/cursor): find the
    top-level window whose ProcessId == pid, then walk its descendants for the
    first ButtonControl whose ClassName starts with 'WindowToolbarButton' and
    whose Name is one of `target_names` and does not end with 'Output' (the
    status-bar toggles). The QMLTYPE_### suffix on the ClassName is volatile and
    is deliberately NOT keyed on.

    Returns the target name on success, or None on ANY failure — ImportError
    (uiautomation absent / off-Windows), no interactive desktop, no matching
    window, or a walk error. None means "fall back to the config file".
    """
    try:
        import uiautomation as auto  # lazy: Windows-only, may be absent

        win = None
        for w in auto.GetRootControl().GetChildren():
            try:
                if w.ProcessId == pid:
                    win = w
                    break
            except Exception:
                continue
        if win is None:
            return None

        found: list[str] = []

        def walk(c, depth: int, ctr: list[int]) -> None:
            if found or ctr[0] > 20000 or depth > 40:
                return
            ctr[0] += 1
            try:
                cls = getattr(c, "ClassName", "") or ""
                if (c.ControlTypeName == "ButtonControl"
                        and cls.startswith("WindowToolbarButton")):
                    nm = c.Name or ""
                    if nm in target_names and not nm.endswith("Output"):
                        found.append(nm)
                        return
            except Exception:
                pass
            try:
                kids = c.GetChildren()
            except Exception:
                kids = []
            for k in kids:
                try:
                    if k.ProcessId != pid:
                        continue
                except Exception:
                    pass
                walk(k, depth + 1, ctr)

        walk(win, 0, [0])
        return found[0] if found else None
    except Exception:
        return None
