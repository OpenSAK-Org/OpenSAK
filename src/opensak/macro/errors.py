"""src/opensak/macro/errors.py — the exception every macro failure is reported as."""


class MacroError(Exception):
    """A macro failed — Lua syntax/runtime error or a bad API call."""
