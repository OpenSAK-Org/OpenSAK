"""
src/opensak/utils/run_macro.py — `opensak --run-macro`: run a Lua macro in
the running OpenSAK (#1013).

GSAK users write macros in an external editor and run them from there with
a button. `opensak --run-macro path/to/macro.lua` sends the macro to the
OpenSAK instance that is already open, over a local socket (a named pipe on
Windows) that only the current user can reach, and waits for it:

  * print() output is written to stdout, opensak.log() output to stderr;
  * a Lua error is written to stderr as "path:line: message", so editors
    can turn it into a clickable link;
  * the exit code is the one passed to opensak.exit(), 0 otherwise — see
    the EXIT_* codes below for the others.

Dialogs the macro opens (confirm, folder approval, file picker) appear in
the OpenSAK window while the caller waits. OpenSAK refuses a run while a
dialog is open or another macro is running. With --timeout the caller asks
OpenSAK to cancel the macro after that many seconds.

This module holds the wire protocol and the client; the server lives in
gui/macro_server.py. It is imported before QApplication is created, so it
keeps its imports light (no opensak.macro, which pulls in the database).

Protocol: one JSON object per line (UTF-8), "op" says what it is.
  client → server: {"op": "run", "path": ...}, {"op": "cancel"}
  server → client: {"op": "hello", "pid": ...}, {"op": "out", "text": ...},
                   {"op": "err", "text": ...}, {"op": "exit", "code": ...}
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional, TextIO

# Exit codes of `opensak --run-macro` other than the macro's own.
EXIT_OK = 0
EXIT_MACRO_ERROR = 1        # Lua error, or the macro was cancelled
EXIT_USAGE = 2              # bad arguments, macro file missing or unreadable
EXIT_NOT_RUNNING = 3        # no OpenSAK instance to talk to
EXIT_BUSY = 4               # a dialog is open or another macro is running
EXIT_TIMEOUT = 124          # --timeout reached (like coreutils `timeout`)
EXIT_INTERRUPTED = 130      # Ctrl+C

CONNECT_TIMEOUT_MS = 2000
POLL_MS = 200
# After a timeout or Ctrl+C: how long to wait for OpenSAK to confirm the cancel.
CANCEL_GRACE_S = 5.0

USAGE = "usage: opensak --run-macro <macro.lua> [--timeout <seconds>]"


def server_name() -> str:
    """Name of the local socket / named pipe, one per user. Hashed so that
    any user name gives a valid name."""
    try:
        user = getpass.getuser()
    except Exception:  # no user name in the environment
        user = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return "opensak-macro-" + hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]


def encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def decode_lines(buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Split complete lines off *buffer*; returns (messages, rest). Lines
    that are not a JSON object are skipped."""
    *lines, rest = buffer.split(b"\n")
    messages = []
    for line in lines:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(message, dict):
            messages.append(message)
    return messages, rest


def format_error(message: str, chunk_name: str, path: Path) -> str:
    """Rewrite a macro error for editors: "macro.lua:12: msg" becomes
    "<full path>:12: msg". Lua names a script by its chunk name, which it
    shortens to ~60 characters, so the macro runs under its file name and
    the full path is put in here. An error without a line still starts with
    the path."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(chunk_name)}:(\d+):")
    text, found = pattern.subn(lambda m: f"{path}:{m.group(1)}:", message)
    if found and text.startswith(f"{path}:"):
        return text
    return f"{path}: {text}"


def parse_args(argv: list[str]) -> Optional[tuple[str, Optional[float]]]:
    """(macro path, timeout in seconds or None) if *argv* asks for
    --run-macro, None otherwise. Raises ValueError for bad arguments."""
    path: Optional[str] = None
    timeout: Optional[str] = None
    wanted = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        for name in ("--run-macro", "--timeout"):
            if arg == name:
                if i + 1 >= len(argv):
                    raise ValueError(f"{name} needs a value")
                value, i = argv[i + 1], i + 2
                break
            if arg.startswith(name + "="):
                value, i = arg[len(name) + 1:], i + 1
                break
        else:
            i += 1
            continue
        if name == "--run-macro":
            wanted, path = True, value
        else:
            timeout = value
    if not wanted:
        return None
    if not path:
        raise ValueError("--run-macro needs the path of a .lua file")
    seconds: Optional[float] = None
    if timeout is not None:
        try:
            seconds = float(timeout)
        except ValueError:
            raise ValueError(f"--timeout expects seconds, got {timeout!r}") from None
        if not seconds > 0:
            raise ValueError(f"--timeout expects seconds > 0, got {timeout!r}")
    return path, seconds


def _attach_windows_stdio() -> None:
    """The Windows release build is a GUI program, so Python starts it
    without sys.stdout/sys.stderr. Use the handles the caller passed (an
    editor capturing the output), else the console of the shell it was
    started from."""
    if sys.platform != "win32" or (sys.stdout is not None and sys.stderr is not None):
        return
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    invalid = ctypes.c_void_p(-1).value
    console_attached = False
    for attr, std in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, attr) is not None:
            continue
        stream: Optional[TextIO] = None
        handle = kernel32.GetStdHandle(std)
        if handle and handle != invalid:
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
                stream = open(fd, "w", encoding="utf-8", errors="replace", buffering=1)
            except OSError:
                stream = None
        if stream is None:
            if not console_attached:
                console_attached = bool(kernel32.AttachConsole(-1))  # ATTACH_PARENT_PROCESS
            if console_attached:
                try:
                    stream = open("CONOUT$", "w", encoding="utf-8", errors="replace",
                                  buffering=1)
                except OSError:
                    stream = None
        if stream is not None:
            setattr(sys, attr, stream)


def _allow_foreground(pid: Any) -> None:
    """Windows only lets the foreground program bring another window to the
    front. The editor that started us is in front, so pass that right on to
    OpenSAK — otherwise a macro's dialog would only flash in the taskbar."""
    if sys.platform != "win32" or not isinstance(pid, int):
        return
    import ctypes
    ctypes.windll.user32.AllowSetForegroundWindow(pid)  # type: ignore[attr-defined]


def run_remote(path: Path, timeout: Optional[float], out: TextIO, err: TextIO) -> int:
    """Run the macro at *path* in the running OpenSAK; returns the exit code."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(CONNECT_TIMEOUT_MS):
        err.write("opensak: OpenSAK is not running (or Lua macros are not enabled "
                  "in it) — start OpenSAK first\n")
        return EXIT_NOT_RUNNING

    def send(message: dict[str, Any]) -> None:
        sock.write(encode(message))
        sock.waitForBytesWritten(1000)

    send({"op": "run", "path": str(path)})
    deadline = time.monotonic() + timeout if timeout else None
    give_up: Optional[float] = None
    result: Optional[int] = None
    buffer = b""
    while True:
        try:
            now = time.monotonic()
            if give_up is None and deadline is not None and now >= deadline:
                err.write(f"opensak: timeout after {timeout:g} s — cancelling the macro\n")
                send({"op": "cancel"})
                result, give_up = EXIT_TIMEOUT, now + CANCEL_GRACE_S
            if give_up is not None and now >= give_up:
                break
            if not sock.bytesAvailable() and not sock.waitForReadyRead(POLL_MS):
                if sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
                    break
                continue
            messages, buffer = decode_lines(buffer + sock.readAll().data())
            for message in messages:
                op = message.get("op")
                if op == "hello":
                    _allow_foreground(message.get("pid"))
                elif op == "out":
                    out.write(f"{message.get('text', '')}\n")
                    out.flush()
                elif op == "err":
                    err.write(f"{message.get('text', '')}\n")
                    err.flush()
                elif op == "exit":
                    code = message.get("code")
                    sock.disconnectFromServer()
                    if result is not None:
                        return result
                    return code if isinstance(code, int) else EXIT_MACRO_ERROR
        except KeyboardInterrupt:
            if give_up is not None:
                break
            send({"op": "cancel"})
            result, give_up = EXIT_INTERRUPTED, time.monotonic() + CANCEL_GRACE_S
    if result is None:
        err.write("opensak: lost the connection to OpenSAK\n")
        return EXIT_MACRO_ERROR
    return result


def main(argv: list[str]) -> Optional[int]:
    """Handle --run-macro in *argv*: the exit code, or None if not asked
    for (start the GUI as usual)."""
    try:
        parsed = parse_args(argv)
    except ValueError as exc:
        _attach_windows_stdio()
        if sys.stderr is not None:
            sys.stderr.write(f"opensak: {exc}\n{USAGE}\n")
        return EXIT_USAGE
    if parsed is None:
        return None
    _attach_windows_stdio()
    out = sys.stdout or open(os.devnull, "w")
    err = sys.stderr or open(os.devnull, "w")
    # Editors read the output through a pipe and expect UTF-8 (Python would
    # use the ANSI code page there on Windows); a console gets Unicode anyway.
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            if stream.isatty():
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
    raw_path, timeout = parsed
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        err.write(f"opensak: macro not found: {path}\n")
        return EXIT_USAGE
    return run_remote(path, timeout, out, err)
