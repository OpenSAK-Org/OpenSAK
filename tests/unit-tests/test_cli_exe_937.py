"""
tests/unit-tests/test_cli_exe_937.py — the Windows console program
opensak-cli.exe and its MSIX App Execution Alias (#937): opensak.spec builds
it next to opensak.exe, and the manifest that build_msix_local.ps1 fills in
is valid XML with the alias. The real build and the alias itself need
Windows and the SDK tools (packaging/msix/README.md, sideload checklist).
"""

import re
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "opensak.spec"
MSIX = ROOT / "packaging" / "msix"
TEMPLATE = MSIX / "AppxManifest.xml.template"
BUILD_SCRIPT = MSIX / "build_msix_local.ps1"

NS = {
    "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "uap5": "http://schemas.microsoft.com/appx/manifest/uap/windows10/5",
    "desktop4": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/4",
}
_SUBSYSTEM = f"{{{NS['desktop4']}}}Subsystem"


class _Target:
    """Stands in for a PyInstaller build target; keeps its arguments."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def __getattr__(self, name: str) -> str:
        # Analysis outputs (a.pure, a.scripts, a.binaries, a.datas)
        if name.startswith("__"):
            raise AttributeError(name)
        return f"<{type(self).__name__}.{name}>"


def _run_spec(monkeypatch: pytest.MonkeyPatch, platform: str) -> dict[str, Any]:
    """Execute opensak.spec as PyInstaller would, with stub targets."""
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    setattr(hooks, "collect_data_files", lambda *a, **k: [])
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.chdir(ROOT)
    namespace: dict[str, Any] = {
        name: type(name, (_Target,), {})
        for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")
    }
    exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
    return namespace


def _executables(namespace: dict[str, Any]) -> list[_Target]:
    exe_type = namespace["EXE"]
    return [a for a in namespace["coll"].args if isinstance(a, exe_type)]


def test_spec_builds_console_twin_on_windows(monkeypatch):
    ns = _run_spec(monkeypatch, "win32")
    gui, cli = _executables(ns)
    assert gui.kwargs["name"] == "opensak" and gui.kwargs["console"] is False
    assert cli.kwargs["name"] == "opensak-cli" and cli.kwargs["console"] is True
    # The same program: same Python archive and scripts, in the same folder.
    assert cli.args == gui.args
    assert cli.kwargs["exclude_binaries"] is True
    assert ns["coll"].kwargs["name"] == "opensak"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_spec_builds_one_executable_elsewhere(monkeypatch, platform):
    ns = _run_spec(monkeypatch, platform)
    assert [e.kwargs["name"] for e in _executables(ns)] == ["opensak"]


def _script_replacements() -> dict[str, str]:
    """The placeholders build_msix_local.ps1 fills in, with what it puts
    there: a literal string, or the name of the variable it uses."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    return dict(re.findall(r'\$manifest\.Replace\("(__\w+__)",\s*"?([^")]+)"?\)', text))


def _filled_manifest(alias: str) -> str:
    values = {
        "$IdentityName": "OpenSAKDevTest.OpenSAK",
        "$PublisherCN": "CN=OpenSAK Dev Test",
        "$PublisherDisplayName": "OpenSAK Dev Test",
        "$DisplayName": "OpenSAK Beta",
        "$Version": "1.18.1.0",
        "$CliAlias": alias,
    }
    manifest = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in _script_replacements().items():
        manifest = manifest.replace(placeholder, values.get(value, value))
    return manifest


def test_build_script_fills_every_placeholder():
    assert set(re.findall(r"__[A-Z_]+__", TEMPLATE.read_text(encoding="utf-8"))) \
        == set(_script_replacements())
    assert "__" not in _filled_manifest("opensak-beta.exe")


def test_manifest_has_console_app_with_alias():
    root = ET.fromstring(_filled_manifest("opensak-beta.exe"))
    apps = {a.get("Id"): a for a in root.iterfind("m:Applications/m:Application", NS)}
    assert apps["OpenSAK"].get("Executable") == r"OpenSAK\opensak.exe"

    cli = apps["OpenSAKCLI"]
    assert cli.get("Executable") == r"OpenSAK\opensak-cli.exe"
    assert cli.get(_SUBSYSTEM) == "console"
    visual = cli.find("uap:VisualElements", NS)
    assert visual is not None and visual.get("AppListEntry") == "none"
    # Partner Center only accepts reserved display names.
    assert visual.get("DisplayName") == "OpenSAK Beta"

    ext = cli.find("m:Extensions/uap5:Extension", NS)
    assert ext is not None
    assert ext.get("Category") == "windows.appExecutionAlias"
    assert ext.get("Executable") == cli.get("Executable")
    alias = ext.find("uap5:AppExecutionAlias", NS)
    assert alias is not None and alias.get(_SUBSYSTEM) == "console"
    names = [e.get("Alias") for e in alias.iterfind("uap5:ExecutionAlias", NS)]
    assert names == ["opensak-beta.exe"]
    # The GUI app keeps its Start Menu entry and gets no alias.
    assert apps["OpenSAK"].find("m:Extensions", NS) is None


def test_msix_workflow_passes_alias_for_beta_and_stable():
    text = (ROOT / ".github" / "workflows" / "build-msix.yml").read_text(encoding="utf-8")
    assert '$cliAlias = "opensak-beta.exe"' in text
    assert '$cliAlias = "opensak.exe"' in text
    assert "-CliAlias" in text
