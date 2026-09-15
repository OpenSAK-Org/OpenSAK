# tests/unit-tests/test_pq_email_parser.py — generic PQ zip-attachment
# detection (issue #443, session 2). Uses synthetic e-mails built
# in-memory; no real mailbox or Geocaching.com-specific assumptions.

from email.message import EmailMessage

import pytest

from opensak.email.parser import (
    has_zip_attachment,
    iter_zip_attachments,
    pq_name_from_filename,
)


def _make_email(attachments: list[tuple[str, bytes, str]]) -> EmailMessage:
    """Build a multipart e-mail with the given (filename, content, maintype/subtype) attachments."""
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "Test"
    msg.set_content("body text")
    for filename, content, ctype in attachments:
        maintype, subtype = ctype.split("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg


class TestIterZipAttachments:
    def test_finds_single_zip_attachment(self):
        msg = _make_email([("26021717_Sommerhus.zip", b"PK\x03\x04fake", "application/zip")])
        found = list(iter_zip_attachments(msg))
        assert len(found) == 1
        filename, payload = found[0]
        assert filename == "26021717_Sommerhus.zip"
        assert payload == b"PK\x03\x04fake"

    def test_finds_multiple_zip_attachments(self):
        msg = _make_email([
            ("North.zip", b"PK-north", "application/zip"),
            ("South.zip", b"PK-south", "application/zip"),
        ])
        found = list(iter_zip_attachments(msg))
        assert {f for f, _ in found} == {"North.zip", "South.zip"}

    def test_ignores_non_zip_attachments(self):
        msg = _make_email([("readme.pdf", b"%PDF-fake", "application/pdf")])
        assert list(iter_zip_attachments(msg)) == []

    def test_zip_extension_is_case_insensitive(self):
        msg = _make_email([("List.ZIP", b"PK-upper", "application/zip")])
        found = list(iter_zip_attachments(msg))
        assert len(found) == 1
        assert found[0][0] == "List.ZIP"

    def test_email_with_no_attachments_yields_nothing(self):
        msg = EmailMessage()
        msg["From"] = "someone@example.com"
        msg.set_content("just text, no attachment")
        assert list(iter_zip_attachments(msg)) == []

    def test_mixed_attachments_only_zip_returned(self):
        msg = _make_email([
            ("photo.jpg", b"\xff\xd8fake", "image/jpeg"),
            ("List.zip", b"PK-list", "application/zip"),
        ])
        found = list(iter_zip_attachments(msg))
        assert len(found) == 1
        assert found[0][0] == "List.zip"


class TestHasZipAttachment:
    def test_true_when_zip_present(self):
        msg = _make_email([("List.zip", b"PK-list", "application/zip")])
        assert has_zip_attachment(msg) is True

    def test_false_when_no_zip_present(self):
        msg = _make_email([("readme.pdf", b"%PDF-fake", "application/pdf")])
        assert has_zip_attachment(msg) is False

    def test_false_for_plain_text_email(self):
        msg = EmailMessage()
        msg.set_content("no attachments here")
        assert has_zip_attachment(msg) is False


class TestPqNameFromFilename:
    def test_strips_leading_numeric_id(self):
        assert pq_name_from_filename("26021717_Sommerhus.zip") == "Sommerhus"

    def test_no_id_prefix_returns_stem_unchanged(self):
        assert pq_name_from_filename("MyList.zip") == "MyList"

    def test_case_insensitive_extension_still_stripped(self):
        assert pq_name_from_filename("North.ZIP") == "North"

    def test_only_strips_one_leading_numeric_group(self):
        # A name that itself starts with digits after the ID prefix
        # should only have the ID stripped once, not repeatedly.
        assert pq_name_from_filename("123_456_Region.zip") == "456_Region"

    def test_name_without_extension_returns_as_is(self):
        assert pq_name_from_filename("NoExtension") == "NoExtension"
