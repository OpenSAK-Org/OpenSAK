"""
src/opensak/macro — Proof of concept: Lua macros for OpenSAK (roadmap item 2).

A macro is a small Lua script run in a sandboxed Lua interpreter (via lupa).
It talks to OpenSAK through the global `opensak` table; see runtime.py for
the available functions.
"""

from opensak.macro.permissions import FolderPermission, check_access
from opensak.macro.runtime import (
    FolderApproval, MacroBusy, MacroError, MacroHost, MacroRuntime, build_filterset,
    macro_running,
)

__all__ = [
    "FolderApproval", "FolderPermission", "MacroBusy", "MacroError", "MacroHost",
    "MacroRuntime", "build_filterset", "check_access", "macro_running",
]
