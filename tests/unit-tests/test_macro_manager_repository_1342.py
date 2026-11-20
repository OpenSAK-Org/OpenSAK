"""tests/unit-tests/test_macro_manager_repository_1342.py — OpenSAK-Macros in the
Macro manager (#1342).

Covers the OpenSAK-Macros column, Check for updates (with the offer to
remove withdrawn macros), Update with its confirmation, Browse and install,
Remove, Stop tracking, the settings check box and the once-a-day check at
startup. Downloads run at once through a fake fetcher; no network.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import opensak.gui.dialogs.macro_manager as mm
import opensak.gui.dialogs.macro_repository as mr
from opensak.macro import repository as repo

from tests.macro_index_data import GONE, PNG, README, V1, V2, FakeFetch, _index_data


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Downloads from the fake repository; background tasks run at once."""
    fetch = FakeFetch(_index_data())
    monkeypatch.setattr(repo, "_download", fetch)

    def run_now(parent, fn, on_done, on_error):
        try:
            result = fn()
        except repo.RepositoryError as exc:
            on_error(str(exc))
        else:
            on_done(result)

    monkeypatch.setattr(mr, "start_task", run_now)
    repo.forget_index()
    yield fetch
    repo.forget_index()


@pytest.fixture
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d.resolve()


@pytest.fixture
def answers(monkeypatch):
    """What the confirmations answer (and what they were asked)."""
    state = {"install": True, "remove": None, "installs": [], "removes": []}

    def install(parent, macro, targets, replaced, **kw):
        state["installs"].append((macro.id, targets, replaced, kw))
        return state["install"]

    def remove(parent, name, lua, withdrawn=False):
        state["removes"].append((name, lua, withdrawn))
        return state["remove"]

    monkeypatch.setattr(mr, "confirm_install", install)
    monkeypatch.setattr(mr, "confirm_remove", remove)
    monkeypatch.setattr(mm.QMessageBox, "warning", MagicMock())
    return state


@pytest.fixture
def make_manager(qtbot, macros_dir):
    def make():
        d = mm.MacroManagerDialog(host=MagicMock())
        qtbot.addWidget(d)
        return d
    return make


def _put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _repo_column(d) -> dict[str, str]:
    tree = d._tree
    return {Path(tree.topLevelItem(i).data(mm.COL_NAME, mm._PATH_ROLE)).name:
            tree.topLevelItem(i).text(mm.COL_REPO) for i in range(tree.topLevelItemCount())}


def test_no_column_text_before_an_index_was_fetched(make_manager, macros_dir):
    _put(macros_dir / "roadsheet" / "roadsheet.lua", V1)
    d = make_manager()
    assert _repo_column(d) == {"roadsheet.lua": ""}
    assert repo.install_records() == {}


def test_check_for_updates_shows_statuses(make_manager, macros_dir, answers, no_network):
    _put(macros_dir / "roadsheet" / "roadsheet.lua", V1)
    _put(macros_dir / "mine.lua", b"-- Name: Mine\nprint('mine')\n")
    d = make_manager()
    d.check_updates()
    assert no_network.urls == [repo.INDEX_URL]
    assert _repo_column(d) == {"roadsheet.lua": "Update: 1.0 → 1.1", "mine.lua": ""}
    assert "Updates available for 1 macro(s)." in d._output.toPlainText()
    assert not repo.startup_check_due()            # counts as today's check
    d._update_repo_menu()
    assert d._act_update_all.isEnabled() and "(1)" in d._act_update_all.text()


def test_check_lists_each_update_and_withdrawn_macro(make_manager, macros_dir, answers):
    """The column may be off screen: the log names each macro too."""
    _put(macros_dir / "roadsheet" / "roadsheet.lua", V1)
    _put(macros_dir / "old.lua", GONE)
    d = make_manager()
    d.check_updates()
    lines = d._output.toPlainText().splitlines()
    assert "  • Roadsheet: Update: 1.0 → 1.1" in lines
    assert "  • Old one: Withdrawn" in lines


def test_repo_button_shows_update_count(make_manager, macros_dir, answers):
    _put(macros_dir / "roadsheet" / "roadsheet.lua", V1)
    d = make_manager()
    assert d._btn_repo.text() == "OpenSAK-Macros"
    d.check_updates()
    assert d._btn_repo.text() == "OpenSAK-Macros (1)"
    assert d._btn_repo.font().bold()


def test_repo_column_is_shown_next_to_version(make_manager):
    header = make_manager()._tree.header()
    assert header.visualIndex(mm.COL_REPO) == header.visualIndex(mm.COL_VERSION) + 1


def test_check_offers_to_remove_withdrawn_macros(make_manager, macros_dir, answers):
    gone = _put(macros_dir / "old.lua", GONE)
    d = make_manager()
    d.check_updates()                               # answers["remove"] None: keep it
    assert answers["removes"] == [("Old one", gone, True)]
    assert gone.exists() and _repo_column(d)["old.lua"] == "Withdrawn"
    answers["remove"] = False
    assert d.remove_macro(gone, withdrawn=True)
    assert not gone.exists()


def test_check_failure_is_reported(make_manager, macros_dir, answers, monkeypatch):
    def offline(url, limit):
        raise repo.RepositoryError("no network")
    monkeypatch.setattr(repo, "_download", offline)
    d = make_manager()
    d.check_updates()
    assert "no network" in d._output.toPlainText()
    assert mm.QMessageBox.warning.called and repo.last_index() is None


def test_update_asks_and_keeps_previous(make_manager, macros_dir, answers):
    lua = _put(macros_dir / "roadsheet" / "roadsheet.lua", V1)
    d = make_manager()
    d.check_updates()
    answers["install"] = False
    d.update_macros([lua])
    assert lua.read_bytes() == V1                   # declined: nothing written
    answers["install"] = True
    d.update_all()
    macro_id, targets, replaced, kw = answers["installs"][-1]
    assert macro_id == "roadsheet" and kw["from_version"] == "1.0" and not kw["changed"]
    assert lua in replaced and kw["local"].path == lua
    assert lua.read_bytes() == V2
    assert (macros_dir / "roadsheet" / "README.md").read_bytes() == README
    assert (macros_dir / "roadsheet" / "img" / "shot.png").read_bytes() == PNG
    assert (macros_dir / "roadsheet" / repo.PREVIOUS_DIR / "roadsheet.lua").read_bytes() == V1
    assert _repo_column(d) == {"roadsheet.lua": "Up to date (1.1)"}


def test_changed_copy_is_flagged_in_the_question(make_manager, macros_dir, answers):
    lua = _put(macros_dir / "roadsheet.lua", V1)
    d = make_manager()
    d.check_updates()                               # adopted as 1.0
    lua.write_bytes(V1 + b"-- mine\n")
    d.reload()
    assert _repo_column(d) == {"roadsheet.lua": "Changed locally (1.0)"}
    d.update_macros([lua])
    assert answers["installs"][-1][3]["changed"] is True
    assert lua.read_bytes() == V2 and not (macros_dir / "README.md").exists()


def test_browse_and_install(make_manager, macros_dir, answers, qtbot):
    d = make_manager()
    d.open_browse()                                 # fetches the index first
    browse = d._browse
    qtbot.addWidget(browse)
    assert browse.select("roadsheet") and browse._btn_install.isEnabled()
    assert browse._tree.topLevelItem(0).text(mr.B_STATUS) == "Not installed"
    browse._install_selected()
    lua = macros_dir / "roadsheet" / "roadsheet.lua"
    assert lua.read_bytes() == V2
    assert answers["installs"][-1][3].get("foreign_folder") is None
    assert browse._tree.topLevelItem(0).text(mr.B_STATUS) == "Up to date (1.1)"
    assert not browse._btn_install.isEnabled()
    assert "Installed Roadsheet 1.1" in d._output.toPlainText()


def test_install_into_existing_own_folder_warns(make_manager, macros_dir, answers):
    _put(macros_dir / "roadsheet" / "notes.txt", b"mine")
    d = make_manager()
    d.check_updates()
    answers["install"] = False
    assert not d.install_macro("roadsheet")
    assert answers["installs"][-1][3]["foreign_folder"] == macros_dir / "roadsheet"


def test_browse_refuses_macros_for_a_newer_api(make_manager, macros_dir, answers, qtbot,
                                               monkeypatch):
    monkeypatch.setattr(repo, "_download", FakeFetch(_index_data(latest_api=99)))
    d = make_manager()
    d.open_browse()
    qtbot.addWidget(d._browse)
    d._browse.select("roadsheet")
    assert not d._browse._btn_install.isEnabled()
    assert d._browse._tree.topLevelItem(0).text(mr.B_STATUS) == "Needs API 99"


def test_stop_tracking_and_track_again(make_manager, macros_dir, answers):
    lua = _put(macros_dir / "roadsheet.lua", V1)
    d = make_manager()
    d.check_updates()
    d.set_tracking(lua, False)
    assert _repo_column(d) == {"roadsheet.lua": "Not tracked"}
    assert repo.install_records() == {}
    d.set_tracking(lua, True)
    assert _repo_column(d) == {"roadsheet.lua": "Update: 1.0 → 1.1"}


# ── Confirmation texts ────────────────────────────────────────────────────────

@pytest.fixture
def shown(monkeypatch):
    """The text of the next message box, which answers with its default."""
    seen = {}

    def fake_exec(box):
        seen["text"] = box.text()
        seen["info"] = box.informativeText()
        seen["default"] = box.defaultButton().text()
        box.defaultButton().click()
        return 0

    monkeypatch.setattr(mr.QMessageBox, "exec", fake_exec)
    return seen


def test_update_question_lists_changes_and_new_permissions(macros_dir, shown, qtbot):
    from opensak.macro.metadata import read_metadata
    index = repo.parse_index(_index_data())
    macro = index.macros["roadsheet"]
    lua = _put(macros_dir / "roadsheet" / "roadsheet.lua",
               b"-- Name: Roadsheet\n-- Permissions: write\nprint(1)\n")
    assert mr.confirm_install(None, macro, [lua], [lua], local=read_metadata(lua),
                              from_version="1.0")
    assert shown["text"] == "Update “Roadsheet” from 1.0 to 1.1?"
    assert "1.1: prints 2" in shown["info"] and "first version" not in shown["info"]
    assert "network — contacts example.com" in shown["info"]
    assert "write — changes data" not in shown["info"]      # declared before
    assert "roadsheet/roadsheet.lua (replaced)" in shown["info"]
    assert shown["default"] == "Update"


def test_changed_copy_defaults_to_cancel(macros_dir, shown):
    index = repo.parse_index(_index_data())
    macro = index.macros["roadsheet"]
    lua = _put(macros_dir / "roadsheet.lua", b"print(1)\n")
    from opensak.macro.metadata import read_metadata
    assert not mr.confirm_install(None, macro, [lua], [lua], local=read_metadata(lua),
                                  from_version="1.0", changed=True)
    assert shown["default"] == "Cancel" and ".previous" in shown["info"]


def test_remove_question_has_unticked_data_box(macros_dir, monkeypatch):
    lua = _put(macros_dir / "m.lua", b"print(1)\n")
    _put(macros_dir / "m.db3", b"data")
    seen = {}

    def fake_exec(box):
        seen["check"] = box.checkBox().isChecked()
        next(b for b in box.buttons() if b.text() == "Remove").click()
        return 0

    monkeypatch.setattr(mr.QMessageBox, "exec", fake_exec)
    assert mr.confirm_remove(None, "M", lua) is False and seen["check"] is False


def test_status_texts():
    index = repo.parse_index(_index_data(latest_api=99))
    macro = index.macros["roadsheet"]
    status = repo.MacroStatus(repo.NEEDS_NEWER_API, "roadsheet", "1.0", macro)
    assert mr.status_text(status) == "1.1 needs a newer OpenSAK (API 99)"
    assert "prints 2" in mr.status_tooltip(status)
    assert mr.status_text(repo.MacroStatus(repo.LOCAL)) == ""


# ── Settings and startup ──────────────────────────────────────────────────────

def test_settings_check_box(qtbot):
    from opensak.gui.dialogs.settings_dialog import SettingsDialog
    d = SettingsDialog()
    qtbot.addWidget(d)
    assert d._macro_update_cb.isChecked()           # on by default
    d._macro_update_cb.setChecked(False)
    d._save()
    assert not repo.check_at_startup()


def test_startup_check_once_a_day_and_notice(macros_dir, monkeypatch):
    from opensak.gui import mainwindow as mw
    from opensak import net
    monkeypatch.setattr(net, "offline_detected", lambda: False)
    _put(macros_dir / "roadsheet.lua", V1)
    _put(macros_dir / "old.lua", GONE)
    win = MagicMock()
    win._on_macro_index = lambda index: mw.MainWindow._on_macro_index(win, index)
    win._macro_manager = None
    mw.MainWindow._check_macro_updates_background(win)
    message = win._statusbar.showMessage.call_args.args[0]
    assert "1 macro update(s), 1 withdrawn" in message
    assert (macros_dir / "roadsheet.lua").read_bytes() == V1     # nothing installed
    win._statusbar.showMessage.reset_mock()
    mw.MainWindow._check_macro_updates_background(win)          # again today: skipped
    assert not win._statusbar.showMessage.called
    repo.note_checked(datetime_days_ago(2))
    repo.set_check_at_startup(False)
    mw.MainWindow._check_macro_updates_background(win)          # off: only by hand
    assert not win._statusbar.showMessage.called


def datetime_days_ago(days: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(days=days)
