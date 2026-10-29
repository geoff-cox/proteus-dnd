"""
The textbook registry (checklist item SP-1).

The ``textbooks`` table is a real registry now — one row per book carrying its
public ``git_repo_url`` and the local ``build_dir`` — populated through
:func:`add_textbook` (the ``proteus textbook add`` CLI and, later, the B4.13
Textbooks screen), not a hardcoded seed (the placeholder is retired in migration
0010). Books are identified at upload (the ``.tab`` carries no book id); the
upload flow has the user pick a registered book or register a new one.

``sync-sources`` (SP-2) is what actually clones the repo — a plain
``git clone`` of a *public* URL, no credentials — so registration refuses a URL
that embeds credentials or uses scp-style ssh.

One row per book is enforced here at the registration boundary: re-adding an
existing ``book_xml_id`` is refused rather than creating a second row.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from proteus.core.db import transaction

__all__ = [
    "add_textbook",
    "update_textbook",
    "get_textbook",
    "list_textbooks",
    "validate_book_xml_id",
    "DEFAULT_GIT_REF",
]

# Registered without a pinned ref: track the repo's default branch. sync-sources
# (SP-2) clones it and can record the resolved commit afterwards.
DEFAULT_GIT_REF = "HEAD"

# A book id must be a safe single path component: it names the clone directory
# (clone_root/<book>) and the source cache (question_sources/<book>), so a value
# with separators or '..' could escape those roots.
_SAFE_BOOK_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")


def validate_book_xml_id(book_xml_id: str) -> None:
    """Raise ValueError unless ``book_xml_id`` is a safe path component (letters,
    digits, ``-`` and ``_``; not empty)."""
    if not _SAFE_BOOK_ID.fullmatch(book_xml_id or ""):
        raise ValueError(
            f"invalid book_xml_id {book_xml_id!r}: use only letters, digits, "
            "'-' and '_' (it is used as a filesystem path component)"
        )

# Schemes a plain, credential-free `git clone` of a public repo uses. ssh:// /
# git:// (and scp-style user@host:repo) are refused — sync-sources clones with
# no credentials.
_ALLOWED_URL_SCHEMES = frozenset({"https", "http", "file"})


def _validate_public_url(git_repo_url: str) -> None:
    """
    Refuse anything but a public, credential-free clone URL: an https/http/file
    scheme with no embedded username or password. A scp-style ssh URL
    (``user@host:repo``) has no scheme and is rejected by the scheme check; a
    legitimate ``@`` elsewhere (e.g. in the path) is fine.
    """
    parts = urlsplit(git_repo_url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            "public repos only: use an https:// or file:// URL "
            f"(unsupported scheme {scheme or '(none — scp-style ssh?)'!r}); "
            "ssh/git URLs and scp-style 'user@host:repo' are not supported"
        )
    if parts.username or parts.password:
        raise ValueError(
            "public repos only: the git URL must not embed credentials "
            "(username/password)"
        )


def add_textbook(
    conn: sqlite3.Connection,
    *,
    book_xml_id: str,
    git_repo_url: str,
    build_dir: str,
    git_ref: str | None = None,
    source_dir: str | None = None,
    notes: str | None = None,
) -> int:
    """
    Register one public PreTeXt textbook; return its ``textbook_id``.

    Raises ValueError if a required field is missing, if the URL is not a
    public credential-free clone URL (:func:`_validate_public_url`), if the book
    is already registered (one row per book — edit it instead of re-adding), or
    if the repo+ref is already registered for another book (the table's
    ``UNIQUE(git_repo_url, git_ref)``). ``git_ref`` defaults to the repo's
    default branch (:data:`DEFAULT_GIT_REF`).
    """
    book_xml_id = (book_xml_id or "").strip()
    git_repo_url = (git_repo_url or "").strip()
    build_dir = (build_dir or "").strip()
    if not (book_xml_id and git_repo_url and build_dir):
        raise ValueError("book_xml_id, git_repo_url and build_dir are all required")
    validate_book_xml_id(book_xml_id)
    _validate_public_url(git_repo_url)
    if get_textbook(conn, book_xml_id) is not None:
        raise ValueError(
            f"textbook {book_xml_id!r} is already registered; edit it instead "
            "of re-adding"
        )

    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO textbooks (book_xml_id, git_repo_url, git_ref, "
                "source_dir, build_dir, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    book_xml_id,
                    git_repo_url,
                    git_ref or DEFAULT_GIT_REF,
                    source_dir,
                    build_dir,
                    notes,
                ),
            )
    except sqlite3.IntegrityError as exc:
        # The remaining DB constraint is UNIQUE(git_repo_url, git_ref); surface
        # it as a clean error rather than letting it escape as a traceback.
        raise ValueError(
            f"could not register {book_xml_id!r}: {exc}; is this repo and ref "
            "already registered for another book?"
        ) from exc
    return cur.lastrowid


def update_textbook(
    conn: sqlite3.Connection,
    book_xml_id: str,
    *,
    git_repo_url: str | None = None,
    build_dir: str | None = None,
    git_ref: str | None = None,
    notes: str | None = None,
) -> None:
    """
    Edit a registered book's fields (SP-1 / the B4.13 Textbooks screen).

    Only the fields passed are changed; ``None`` leaves a field as-is. A changed
    ``git_repo_url`` is re-validated as a public, credential-free URL. Raises
    ValueError if the book is not registered, if a required field would become
    blank, or if the new ``(git_repo_url, git_ref)`` collides with another book.
    """
    existing = get_textbook(conn, book_xml_id)
    if existing is None:
        raise ValueError(
            f"textbook {book_xml_id!r} is not registered; add it first"
        )
    new_url = existing["git_repo_url"] if git_repo_url is None else git_repo_url.strip()
    new_build = existing["build_dir"] if build_dir is None else build_dir.strip()
    new_ref = (
        existing["git_ref"] if git_ref is None
        else (git_ref.strip() or DEFAULT_GIT_REF)
    )
    new_notes = existing["notes"] if notes is None else (notes.strip() or None)
    if not (new_url and new_build):
        raise ValueError("git_repo_url and build_dir cannot be blank")
    if new_url != existing["git_repo_url"]:
        _validate_public_url(new_url)

    try:
        with transaction(conn):
            conn.execute(
                "UPDATE textbooks SET git_repo_url = ?, build_dir = ?, "
                "git_ref = ?, notes = ? WHERE book_xml_id = ?",
                (new_url, new_build, new_ref, new_notes, book_xml_id),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"could not update {book_xml_id!r}: {exc}; is this repo and ref "
            "already registered for another book?"
        ) from exc


def get_textbook(conn: sqlite3.Connection, book_xml_id: str) -> sqlite3.Row | None:
    """The registered textbook for ``book_xml_id``, or None."""
    return conn.execute(
        "SELECT * FROM textbooks WHERE book_xml_id = ?", (book_xml_id,)
    ).fetchone()


def list_textbooks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All registered textbooks, ordered by book id (for status / the UI)."""
    return conn.execute(
        "SELECT * FROM textbooks ORDER BY book_xml_id"
    ).fetchall()
