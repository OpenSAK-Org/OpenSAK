"""tests/unit-tests/test_sql_regex.py — GSAK's g_RegEx() in OpenSAK's SQL."""

import sqlite3

import pytest

from opensak.db.sql_functions import g_regex, register_geo_functions
from opensak.importer.gsak_filter_importer import translate_where


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    register_geo_functions(c)
    c.executescript("""
        CREATE TABLE caches (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO caches VALUES (1, 'one'), (2, 'two'), (3, NULL);
    """)
    yield c
    c.close()


@pytest.mark.parametrize("pattern, text, expected", [
    ("a|b", "xbx", 1),
    ("^B", "bcd", 1),      # ignores case
    ("^c", "bcd", 0),
    ("a", None, 0),
    (None, "a", 0),
    ("^12$", 12, 1),       # SQLite passes numeric columns as numbers
])
def test_g_regex(pattern, text, expected):
    assert g_regex(pattern, text) == expected


def test_invalid_pattern_is_an_error():
    with pytest.raises(ValueError):
        g_regex("(", "a")


def test_in_sql(conn):
    rows = conn.execute("SELECT id FROM caches WHERE g_RegEx('^o', name)").fetchall()
    assert rows == [(1,)]


def test_invalid_pattern_fails_the_query(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT g_RegEx('(', 'a')").fetchone()


def test_gsak_filter_import_keeps_the_function(conn):
    sql, warnings, _notes, verified = translate_where("not g_regex('one|three', name)")
    assert verified and not warnings
    rows = conn.execute(f"SELECT id FROM caches WHERE ({sql}) ORDER BY id").fetchall()
    assert rows == [(2,), (3,)]
