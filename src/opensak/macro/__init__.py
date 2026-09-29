"""
src/opensak/macro — Proof of concept: Lua macros for OpenSAK (roadmap item 2).

A macro is a small Lua script run in a sandboxed Lua interpreter (via lupa).
It talks to OpenSAK through the global `opensak` table; see runtime.py for
the available functions.
"""

from opensak.macro.runtime import MacroError, MacroHost, MacroRuntime, build_filterset

__all__ = ["MacroError", "MacroHost", "MacroRuntime", "build_filterset"]
