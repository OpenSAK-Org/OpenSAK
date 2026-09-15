"""
src/opensak/email/ — Pocket Query e-mail retrieval (issue #443).

v1 scope: plain IMAP login only (host/port/SSL/username/password).
Gmail and Outlook.com/Live.com require OAuth2 and are tracked
separately as #697 and #698. Scheduled/background checking is #445.

Modules:
    credentials.py — secure password storage via the OS keyring.
    connection.py  — IMAP connect/login/test-connection helpers.
"""
