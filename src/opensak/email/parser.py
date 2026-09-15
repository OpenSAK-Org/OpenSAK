"""
src/opensak/email/parser.py — generic detection of Pocket-Query zip
attachments in an e-mail (issue #443, session 2).

Session 1 assumed Geocaching.com's own "Pocket Query ready" e-mail
would have the zip attached directly — it doesn't, and hasn't since
around 2014 (it now only contains a login-required download link).
That approach was reverted.

This is the corrected, generic replacement: instead of matching a
specific sender or subject line, we treat ANY e-mail with a `.zip`
attachment as a candidate — this is exactly what PQ-club/PQ-service
robots (e.g. a Danish club's automated mailer) actually send. The
attachment is handed to the existing `opensak.importer.import_zip()`
pipeline unchanged, which already validates that it contains real GPX
data and reports a clear error if it doesn't — so this module only
needs to find candidate attachments, not validate their contents.

Kept free of imaplib/GUI imports so it can be unit-tested against a
plain `email.message.Message` built in-memory, with no real mailbox.
"""

from __future__ import annotations

import re
from email.message import Message
from typing import Iterator


def iter_zip_attachments(msg: Message) -> Iterator[tuple[str, bytes]]:
    """
    Yield (filename, bytes) for every attachment in `msg` whose
    filename ends in ``.zip`` (case-insensitive).

    Walks the whole MIME tree so it also finds zips nested inside a
    multipart/mixed wrapper, which is how most mail clients and
    mailing-list robots attach files.
    """
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename or not filename.lower().endswith(".zip"):
            continue
        payload = part.get_payload(decode=True)
        if not payload or not isinstance(payload, bytes):
            continue
        yield filename, payload


def has_zip_attachment(msg: Message) -> bool:
    """Hurtig ja/nej-tjek uden at skulle afkode selve payload'en."""
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename and filename.lower().endswith(".zip"):
            return True
    return False


# Matcher et førende Pocket-Query-ID efterfulgt af underscore, fx
# "26021717_Sommerhus.zip" → ID'et er kun geocaching.com/PQ-service-
# internt og ikke nyttigt til database-matching, kun selve navnet er.
_LEADING_PQ_ID_RE = re.compile(r"^\d+_")


def pq_name_from_filename(zip_filename: str) -> str:
    """
    Udled et "PQ-navn" fra zip-filnavnet til brug for database-matching
    (issue #443's "match til korrekt DB baseret på navn, fald tilbage
    til aktiv DB"-idé).

    "26021717_Sommerhus.zip" → "Sommerhus" (standard GSAK-stil navn,
    kendt fra både Geocaching.com og PQ-klub-robotter).
    "MyList.zip" → "MyList" (intet ID-præfiks — bruges som det er).
    """
    stem = zip_filename.rsplit(".", 1)[0]
    return _LEADING_PQ_ID_RE.sub("", stem, count=1)
