"""
src/opensak/email/credentials.py — secure storage of the PQ e-mail
account's password via the OS keyring (issue #443).

The password is never written to opensak.json / plaintext config —
only the non-secret connection details (host, port, SSL, username)
live there, via AppSettings (see opensak.gui.settings). The password
itself lives wherever the OS considers "secure" for the current user:
Secret Service / KWallet on Linux, Credential Manager on Windows,
Keychain on macOS — whichever backend the `keyring` package resolves
to at runtime.

Kept free of any IMAP/GUI imports so it can be unit-tested in
isolation (a fake in-memory keyring backend is enough).
"""

from __future__ import annotations

import keyring
import keyring.errors

# Service name under which the credential is filed in the OS keyring.
# The "username" passed to keyring is the mailbox account's own
# username (from AppSettings.pq_email_username), not a fixed constant
# — so switching to a different mailbox account never accidentally
# reads or overwrites a different account's saved password.
_SERVICE_NAME = "OpenSAK PQ Email"


def get_password(username: str) -> str | None:
    """Hent det gemte kodeord for `username`, eller None hvis intet er
    gemt (eller username er tomt, eller keyring-backend'en fejler)."""
    if not username:
        return None
    try:
        return keyring.get_password(_SERVICE_NAME, username)
    except keyring.errors.KeyringError:
        return None


def set_password(username: str, password: str) -> bool:
    """Gem kodeordet for `username` i OS keyring. Returnerer True ved
    succes, False hvis username er tomt eller keyring-backend'en fejler."""
    if not username:
        return False
    try:
        keyring.set_password(_SERVICE_NAME, username, password)
        return True
    except keyring.errors.KeyringError:
        return False


def delete_password(username: str) -> bool:
    """Slet det gemte kodeord for `username`, hvis det findes.

    Returnerer True både når kodeordet blev slettet, og når der ikke
    var noget at slette — kalderen behøver kun bekymre sig om det
    reelt fejlede (f.eks. utilgængelig keyring-backend)."""
    if not username:
        return False
    try:
        keyring.delete_password(_SERVICE_NAME, username)
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # var ikke gemt i forvejen — ikke en fejl for kalderen
    except keyring.errors.KeyringError:
        return False
