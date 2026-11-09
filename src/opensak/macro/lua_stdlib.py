"""
src/opensak/macro/lua_stdlib.py — short signatures of the standard Lua
functions a macro can use, for editors that do not read the LuaLS stub
(Notepad++ calltips, see api_docs.render_npp_autocomplete).

Only what the sandbox keeps (runtime._SANDBOX_SETUP): no io, debug,
package, require, load, ... and only os.time/date/clock. Types use the
Lua Language Server notation, like the API registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from opensak.macro.runtime import Param


@dataclass(frozen=True)
class LuaFunction:
    """One standard Lua function: the fields render_npp_autocomplete()
    reads from an ApiFunction."""

    name: str
    description: str
    params: tuple[Param, ...] = ()
    returns: Optional[tuple[str, str]] = None
    overloads: tuple[tuple[Param, ...], ...] = ()


def _p(name: str, type_: str, optional: bool = False) -> Param:
    return Param(name, type_, name, optional)


_S = _p("s", "string")
_I = _p("i", "integer", True)
_J = _p("j", "integer", True)
_X = _p("x", "number")
_T = _p("t", "table")
_ARGS = _p("...", "any")
_CO = _p("co", "thread")
_F = _p("f", "function")

LUA_FUNCTIONS = (
    # ── Basic functions ──
    LuaFunction("assert", "Raise an error with message if v is false or nil; "
                "otherwise return all arguments.",
                (_p("v", "any"), _p("message", "any", True)), ("any", "All arguments.")),
    LuaFunction("error", "Raise an error with message. level 1 (default) "
                "adds the position of the caller of the current function, 0 none.",
                (_p("message", "any"), _p("level", "integer", True))),
    LuaFunction("getmetatable", "The metatable of object (its __metatable "
                "field if set).", (_p("object", "any"),), ("table?", "The metatable.")),
    LuaFunction("ipairs", "Iterate over t[1], t[2], ... up to the first nil: "
                "`for i, v in ipairs(t) do`.", (_T,),
                ("fun(): integer, any", "Iterator for a generic `for`.")),
    LuaFunction("next", "The key after key in t and its value; nil at the end. "
                "next(t) == nil means t is empty.", (_T, _p("key", "any", True)),
                ("any, any", "Next key and value.")),
    LuaFunction("pairs", "Iterate over all keys of t, in no particular order: "
                "`for k, v in pairs(t) do`.", (_T,),
                ("fun(): any, any", "Iterator for a generic `for`.")),
    LuaFunction("pcall", "Call f in protected mode: errors are caught.",
                (_F, _ARGS), ("boolean, any", "true and the results, or false and the error.")),
    LuaFunction("print", "Write the arguments, separated by tabs, to the macro "
                "output pane.", (_ARGS,)),
    LuaFunction("rawequal", "Whether a and b are equal, without calling __eq.",
                (_p("a", "any"), _p("b", "any")), ("boolean", "true if equal.")),
    LuaFunction("rawget", "t[k] without calling __index.", (_T, _p("k", "any")),
                ("any", "The value.")),
    LuaFunction("rawlen", "Length of a table or string without calling __len.",
                (_p("v", "table|string"),), ("integer", "The length.")),
    LuaFunction("rawset", "Set t[k] = v without calling __newindex.",
                (_T, _p("k", "any"), _p("v", "any")), ("table", "t.")),
    LuaFunction("select", "With a number n: the arguments from the n-th on "
                "(negative counts from the end). With \"#\": the number of arguments.",
                (_p("n", "integer|\"#\""), _ARGS), ("any", "Arguments or their count.")),
    LuaFunction("setmetatable", "Set the metatable of t (nil removes it). "
                "__gc is not allowed in macros.", (_T, _p("mt", "table?")),
                ("table", "t.")),
    LuaFunction("tonumber", "Convert v to a number; with base (2-36) parse an "
                "integer numeral in that base.", (_p("v", "any"), _p("base", "integer", True)),
                ("number?", "The number, or nil if v cannot be converted.")),
    LuaFunction("tostring", "Convert v to a string (uses __tostring if set).",
                (_p("v", "any"),), ("string", "The text.")),
    LuaFunction("type", "The type of v: \"nil\", \"number\", \"string\", "
                "\"boolean\", \"table\", \"function\", \"thread\" or \"userdata\".",
                (_p("v", "any"),), ("string", "The type name.")),
    LuaFunction("warn", "Emit a warning built from the arguments (all strings).",
                (_p("msg", "string"), _p("...", "string"))),
    LuaFunction("xpcall", "Like pcall, but on error handler is called with the "
                "error and its result is returned.",
                (_F, _p("handler", "function"), _ARGS),
                ("boolean, any", "true and the results, or false and the handler's result.")),
    # ── coroutine ──
    LuaFunction("coroutine.close", "Close a suspended or dead coroutine.", (_CO,),
                ("boolean, any", "true, or false and the error.")),
    LuaFunction("coroutine.create", "A new coroutine running f.", (_F,),
                ("thread", "The coroutine.")),
    LuaFunction("coroutine.isyieldable", "Whether the running code can yield.",
                (_p("co", "thread", True),), ("boolean", "true if it can yield.")),
    LuaFunction("coroutine.resume", "Start or continue co; the arguments are "
                "passed to its function or returned by its yield.", (_CO, _ARGS),
                ("boolean, any", "true and the yielded values, or false and the error.")),
    LuaFunction("coroutine.running", "The running coroutine.", (),
                ("thread, boolean", "The coroutine and whether it is the main one.")),
    LuaFunction("coroutine.status", "\"running\", \"suspended\", \"normal\" or \"dead\".",
                (_CO,), ("string", "The status.")),
    LuaFunction("coroutine.wrap", "A function that resumes a new coroutine running "
                "f on each call; errors are raised.", (_F,),
                ("function", "The resuming function.")),
    LuaFunction("coroutine.yield", "Suspend the running coroutine; the arguments are "
                "returned by resume.", (_ARGS,), ("any", "The values passed to the next resume.")),
    # ── math ──
    LuaFunction("math.abs", "Absolute value.", (_X,), ("number", "|x|.")),
    LuaFunction("math.acos", "Arc cosine, in radians.", (_X,), ("number", "The angle.")),
    LuaFunction("math.asin", "Arc sine, in radians.", (_X,), ("number", "The angle.")),
    LuaFunction("math.atan", "Arc tangent of y/x, in radians, using the signs of "
                "both for the quadrant (x defaults to 1).",
                (_p("y", "number"), _p("x", "number", True)), ("number", "The angle.")),
    LuaFunction("math.ceil", "Smallest integer >= x.", (_X,), ("integer", "Rounded up.")),
    LuaFunction("math.cos", "Cosine of an angle in radians.", (_X,), ("number", "cos(x).")),
    LuaFunction("math.deg", "Radians to degrees.", (_X,), ("number", "Degrees.")),
    LuaFunction("math.exp", "e to the power x.", (_X,), ("number", "e^x.")),
    LuaFunction("math.floor", "Largest integer <= x.", (_X,), ("integer", "Rounded down.")),
    LuaFunction("math.fmod", "Remainder of x / y, rounded towards zero.",
                (_X, _p("y", "number")), ("number", "The remainder.")),
    LuaFunction("math.log", "Logarithm of x to base (default e).",
                (_X, _p("base", "number", True)), ("number", "The logarithm.")),
    LuaFunction("math.max", "The largest argument.", (_X, _p("...", "number")),
                ("number", "The maximum.")),
    LuaFunction("math.min", "The smallest argument.", (_X, _p("...", "number")),
                ("number", "The minimum.")),
    LuaFunction("math.modf", "Integral and fractional part of x.", (_X,),
                ("number, number", "Integral part and fraction.")),
    LuaFunction("math.rad", "Degrees to radians.", (_X,), ("number", "Radians.")),
    LuaFunction("math.random", "Without arguments a float in [0, 1); with m an "
                "integer in [1, m]; with m and n an integer in [m, n].",
                (_p("m", "integer", True), _p("n", "integer", True)),
                ("number", "The random number.")),
    LuaFunction("math.randomseed", "Seed the random generator (without arguments "
                "with a random seed).", (_X, _p("y", "number", True)), overloads=((),)),
    LuaFunction("math.sin", "Sine of an angle in radians.", (_X,), ("number", "sin(x).")),
    LuaFunction("math.sqrt", "Square root.", (_X,), ("number", "The root.")),
    LuaFunction("math.tan", "Tangent of an angle in radians.", (_X,), ("number", "tan(x).")),
    LuaFunction("math.tointeger", "x as an integer if it has an exact integer value.",
                (_X,), ("integer?", "The integer, or nil.")),
    LuaFunction("math.type", "\"integer\", \"float\", or nil if x is no number.",
                (_p("x", "any"),), ("string?", "The number type.")),
    LuaFunction("math.ult", "Whether m < n as unsigned integers.",
                (_p("m", "integer"), _p("n", "integer")), ("boolean", "true if m < n.")),
    # ── os ──
    LuaFunction("os.clock", "CPU time used by OpenSAK, in seconds.", (),
                ("number", "Seconds.")),
    LuaFunction("os.date", "Format a time (default now) with a strftime format; "
                "\"*t\" returns a table, a leading \"!\" uses UTC.",
                (_p("format", "string", True), _p("time", "integer", True)),
                ("string|table", "The formatted date.")),
    LuaFunction("os.time", "Seconds since the epoch, now or for a table "
                "{year, month, day, hour, min, sec}.", (_p("t", "table", True),),
                ("integer", "Seconds since the epoch.")),
    # ── string (also as methods: s:upper()) ──
    LuaFunction("string.byte", "Byte values of s[i] to s[j] (default i = 1, j = i).",
                (_S, _I, _J), ("integer...", "The byte values.")),
    LuaFunction("string.char", "A string from byte values.", (_p("...", "integer"),),
                ("string", "The string.")),
    LuaFunction("string.dump", "The binary chunk of a Lua function.", (_F,),
                ("string", "The bytecode.")),
    LuaFunction("string.find", "First match of pattern in s from init; plain "
                "turns off pattern matching.",
                (_S, _p("pattern", "string"), _p("init", "integer", True),
                 _p("plain", "boolean", True)),
                ("integer?, integer?, string?...", "Start, end and the captures, or nil.")),
    LuaFunction("string.format", "Format the arguments like C printf: %d %i %u %c "
                "%x %X %o %e %f %g %q %s %a %%, with flags, width and precision.",
                (_p("fmt", "string"), _ARGS), ("string", "The formatted text.")),
    LuaFunction("string.gmatch", "Iterate over the matches of pattern in s: "
                "`for word in s:gmatch(\"%a+\") do`.",
                (_S, _p("pattern", "string"), _p("init", "integer", True)),
                ("fun(): string?...", "Iterator returning the captures.")),
    LuaFunction("string.gsub", "Replace matches of pattern; repl is a string "
                "(%1 = capture), a table or a function.",
                (_S, _p("pattern", "string"), _p("repl", "string|table|function"),
                 _p("n", "integer", True)),
                ("string, integer", "The new text and the number of replacements.")),
    LuaFunction("string.len", "Length in bytes (same as #s).", (_S,),
                ("integer", "The length.")),
    LuaFunction("string.lower", "s in lower case.", (_S,), ("string", "The text.")),
    LuaFunction("string.match", "The captures of the first match of pattern "
                "(the whole match without captures).",
                (_S, _p("pattern", "string"), _p("init", "integer", True)),
                ("string?...", "The captures, or nil.")),
    LuaFunction("string.pack", "Pack values into a binary string by fmt.",
                (_p("fmt", "string"), _ARGS), ("string", "The binary string.")),
    LuaFunction("string.packsize", "Size of a string.pack result for fmt.",
                (_p("fmt", "string"),), ("integer", "Size in bytes.")),
    LuaFunction("string.rep", "s repeated n times, separated by sep.",
                (_S, _p("n", "integer"), _p("sep", "string", True)),
                ("string", "The text.")),
    LuaFunction("string.reverse", "s reversed (byte by byte).", (_S,),
                ("string", "The text.")),
    LuaFunction("string.sub", "The part of s from i to j (default end); negative "
                "positions count from the end.", (_S, _p("i", "integer"), _J),
                ("string", "The substring.")),
    LuaFunction("string.unpack", "Unpack values from a binary string by fmt.",
                (_p("fmt", "string"), _S, _p("pos", "integer", True)),
                ("any...", "The values and the next position.")),
    LuaFunction("string.upper", "s in upper case.", (_S,), ("string", "The text.")),
    # ── table ──
    LuaFunction("table.concat", "Join t[i] to t[j] (strings or numbers) with sep.",
                (_T, _p("sep", "string", True), _I, _J), ("string", "The joined text.")),
    LuaFunction("table.insert", "Append value to t, or insert it at pos and "
                "move the following elements up.",
                (_T, _p("value", "any")), overloads=((_T, _p("pos", "integer"), _p("value", "any")),)),
    LuaFunction("table.move", "Copy a1[f..e] to a2[t..] (default a2 = a1).",
                (_p("a1", "table"), _p("f", "integer"), _p("e", "integer"),
                 _p("t", "integer"), _p("a2", "table", True)), ("table", "a2.")),
    LuaFunction("table.pack", "A table with the arguments and n = their count.",
                (_ARGS,), ("table", "{..., n = count}.")),
    LuaFunction("table.remove", "Remove and return t[pos] (default the last "
                "element), moving the following elements down.",
                (_T, _p("pos", "integer", True)), ("any", "The removed value.")),
    LuaFunction("table.sort", "Sort t in place; comp(a, b) returns true when a "
                "comes first (default <).",
                (_T, _p("comp", "fun(a: any, b: any): boolean", True))),
    LuaFunction("table.unpack", "t[i], ..., t[j] as separate values (default 1 to #t).",
                (_T, _I, _J), ("any...", "The values.")),
    # ── utf8 ──
    LuaFunction("utf8.char", "A UTF-8 string from code points.",
                (_p("...", "integer"),), ("string", "The string.")),
    LuaFunction("utf8.codepoint", "Code points of the characters from byte i to j.",
                (_S, _I, _J), ("integer...", "The code points.")),
    LuaFunction("utf8.codes", "Iterate over the characters: "
                "`for pos, code in utf8.codes(s) do`.", (_S,),
                ("fun(): integer, integer", "Iterator: byte position and code point.")),
    LuaFunction("utf8.len", "Number of UTF-8 characters from byte i to j.",
                (_S, _I, _J), ("integer?", "The count, or nil and the position of an invalid byte.")),
    LuaFunction("utf8.offset", "Byte position of the n-th character (counted from i).",
                (_S, _p("n", "integer"), _I), ("integer?", "The byte position.")),
)
