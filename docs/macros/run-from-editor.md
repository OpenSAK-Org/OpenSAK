# Running macros from an editor

Write your macros in the editor you like (VS Code, Notepad++, UltraEdit,
IntelliJ, …) and run them in OpenSAK with a key or button there, the way
GSAK users are used to:

```bash
opensak --run-macro path/to/macro.lua
```

This sends the macro to the OpenSAK window that is already open and waits
until it has finished:

- `print()` output appears on **stdout**, `opensak.log()` output on
  **stderr** (and in OpenSAK's log file).
- A Lua error appears on stderr as `path/to/macro.lua:12: message` — the
  form most editors turn into a clickable link to the line.
- Dialogs the macro opens (`opensak.confirm()`, `opensak.choose_file()`,
  the question whether the macro may use a folder) come up in front of the
  OpenSAK window, exactly as when the macro runs in the macro window.
  Folder permissions are the same too.
- The exit code is the one the macro passes to `opensak.exit(code)`, `0`
  if it ends normally.

Lua macros are a beta feature: `--run-macro` only works while the
**Macros** menu is available in OpenSAK.

## Options and exit codes

| Option | Meaning |
|---|---|
| `--run-macro <file>` (or `--run-macro=<file>`) | The macro to run. A relative path is relative to the current folder. |
| `--timeout <seconds>` | Cancel the macro after this many seconds. Default: no timeout. |

| Exit code | Meaning |
|---|---|
| `0` | The macro finished (or called `opensak.exit()` / `opensak.exit(0)`). |
| `1` | Lua error, or the macro was cancelled. |
| `2` | Bad arguments, or the macro file is missing or unreadable. |
| `3` | OpenSAK is not running (or Lua macros are not enabled in it). |
| `4` | OpenSAK is busy: a dialog is open, or another macro is running. |
| `124` | `--timeout` was reached. |
| `130` | Cancelled with Ctrl+C. |
| other | The code passed to `opensak.exit(code)` (0–255). |

Codes your macro passes to `opensak.exit()` can clash with the codes above. Use
codes from 10 upwards for your own results if your editor setup needs to
tell them apart.

## How it behaves

- **OpenSAK must already be running.** `--run-macro` does not start it.
- **One thing at a time.** OpenSAK refuses the macro (exit code 4) while
  one of its dialogs is open or another macro is running, whether from the
  macro window or from an editor. Close the dialog and run it again.
- **Cancelling** — `--timeout`, Ctrl+C, or your editor stopping the
  command — closes a dialog the macro has open and ends the macro at its
  next `print()` or `opensak.*` call. A dialog you cancel yourself behaves
  as in the macro window: `opensak.confirm()` returns `false`,
  `opensak.choose_file()` returns `nil`, and a folder you deny fails the
  call that needed it. The native file dialog on Windows and macOS cannot
  be closed from outside; the macro ends when you close it.
- **Only your own user account can reach the connection.** OpenSAK
  listens on a named pipe (Windows) or local socket (macOS, Linux)
  restricted to the user running it.

### Windows: the command from a console

The Windows build of OpenSAK is a windowed program. Editors capture its
output and exit code as usual. In a Command Prompt or PowerShell window,
however, the prompt returns at once, and the output appears after it. Wait
for it with:

```bat
start /wait "" "C:\Program Files\OpenSAK\opensak.exe" --run-macro macro.lua
```

When running from source, use `python run.py --run-macro macro.lua`.

## Editor setups

In the examples below, replace `opensak` with the full path of the OpenSAK
program if it is not on your `PATH`. For example,
`C:\Program Files\OpenSAK\opensak.exe` on Windows, or
`/Applications/OpenSAK.app/Contents/MacOS/opensak` on macOS.

### VS Code

Add a task to `.vscode/tasks.json` in your macros folder. The problem
matcher turns errors into entries in the **Problems** panel and links to
the line:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run macro in OpenSAK",
      "type": "process",
      "command": "opensak",
      "args": ["--run-macro", "${file}"],
      "group": { "kind": "build", "isDefault": true },
      "presentation": { "reveal": "always", "clear": true },
      "problemMatcher": {
        "owner": "opensak",
        "fileLocation": "absolute",
        "pattern": {
          "regexp": "^(.*\\.lua):(\\d+):\\s+(.*)$",
          "file": 1,
          "line": 2,
          "message": 3
        }
      }
    }
  ]
}
```

**Ctrl+Shift+B** then runs the macro in the open editor. Turn on
**Files: Auto Save** or save first, because OpenSAK runs the file on disk.
Together with the [autocompletion setup](api.md#editor-support-vs-code)
this gives a complete macro workbench.

### Notepad++ (NppExec plugin)

Install **NppExec** (Plugins → Plugins Admin), then **Plugins → NppExec →
Execute NppExec Script…** and save this script as `Run in OpenSAK`:

```
NPP_SAVE
"C:\Program Files\OpenSAK\opensak.exe" --run-macro "$(FULL_CURRENT_PATH)"
```

Add it to the Macro menu under **NppExec → Advanced Options…** and give it
a shortcut under **Settings → Shortcut Mapper**. To make errors clickable
in the console, open **NppExec → Console Output Filters… → Highlight** and
add the mask `%ABSPATH%:%LINE%: *`.

### UltraEdit

**Advanced → Tool configuration → Insert**:

- Command line: `"C:\Program Files\OpenSAK\opensak.exe" --run-macro "%f"`
- Working directory: `%p`
- Options: **Save active file**
- Output: **Output to list box**, **Capture output**

The tool appears under **Advanced → User tools** and can get its own
toolbar button. Double-click an error line in the output window to jump to
it.

### IntelliJ IDEA / PyCharm and others

**Settings → Tools → External Tools → +**:

- Program: path of `opensak` / `opensak.exe`
- Arguments: `--run-macro "$FilePath$"`
- Working directory: `$FileDir$`

Under **Advanced Options → Output filters** add
`$FILE_PATH$:$LINE$: .*` so errors link to the line. Assign a key under
**Settings → Keymap → External Tools**.

Other editors work in the same way: run
`opensak --run-macro <current file>` after saving, and show its output.
Errors match the pattern `^(.*\.lua):(\d+): (.*)$`.
