# AInsightfool: new file. The scheduled task previously ran
# `python.exe -m service` directly, which kept a visible console window open
# (the user found the "watch its status" console more clutter than help).
# Switching straight to pythonw.exe broke silently because pythonw has no
# stdout/stderr to write to -- this launcher exists solely to redirect those
# to log files before the service module loads, so the switch to windowless
# actually works instead of crashing on the first log line.
"""Windowless launcher for the ftx-mcp scheduled task.

Registered by setup.ps1 as: pythonw.exe bootstrap\\run_hidden.py

Why this exists: pythonw.exe (the windowless Python build Task Scheduler
launches so no console pops up) starts with sys.stdout/sys.stderr set to
None -- there is no console to attach them to. The very first print or
log line the service emits then crashes with an AttributeError, with
nowhere to show the traceback, so the service dies silently within a
second or two of starting. Redirecting stdout/stderr to real files here,
before the service module is imported, avoids that entirely.

Log dir resolution mirrors service/core.py's Config.from_env(): honor
OPTIX_STATE_DIR if the operator set it (e.g. a redirected install or an
install-smoke run), otherwise the default %LOCALAPPDATA%\\ftx-mcp. Do not
hardcode a path -- this file ships as part of the distro and must work
unmodified on any PC/username setup.ps1 installs onto.

AInsightfool: pythonw.exe itself never allocates a console, so this
launcher (and the service it starts) never triggers the console-popup bug
this way. The companion fix — service/core.py's Runner defaulting every
subprocess call to creationflags=CREATE_NO_WINDOW — is what stops the
service's own shell-outs (PowerShell/taskkill/etc.) from popping a NEW
console once running windowless under this launcher; the two fixes solve
the same symptom at two different layers (this file makes the service
itself windowless, the Runner fix keeps things the *service spawns* from
un-hiding it).
"""
import os
import runpy
import sys
from pathlib import Path

state_dir = os.environ.get("OPTIX_STATE_DIR")
if not state_dir:
    state_dir = str(Path(os.environ["LOCALAPPDATA"]) / "ftx-mcp")
log_dir = Path(state_dir) / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

sys.stdout = open(log_dir / "service-stdout.log", "a", buffering=1, encoding="utf-8")
sys.stderr = open(log_dir / "service-stderr.log", "a", buffering=1, encoding="utf-8")

runpy.run_module("service", run_name="__main__")
