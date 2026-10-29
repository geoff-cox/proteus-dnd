"""
Tests for the textbook registry (checklist item SP-1).

`add_textbook` registers one public PreTeXt book per row; the placeholder seed
is retired (migration 0010), so a fresh registry is empty.
"""

from __future__ import annotations

import pytest

from proteus.core import textbooks


class TestAddTextbook:
    def test_registers_and_returns_id(self, db_conn):
        tid = textbooks.add_textbook(
            db_conn, book_xml_id="ula-proteus",
            git_repo_url="https://example.test/ula.git", build_dir="/build/ula",
        )
        row = textbooks.get_textbook(db_conn, "ula-proteus")
        assert row["textbook_id"] == tid
        assert row["git_repo_url"] == "https://example.test/ula.git"
        assert row["build_dir"] == "/build/ula"
        assert row["git_ref"] == textbooks.DEFAULT_GIT_REF  # default branch
        assert row["source_dir"] is None  # set later by sync-sources

    def test_ref_and_notes_recorded_when_given(self, db_conn):
        textbooks.add_textbook(
            db_conn, book_xml_id="b", git_repo_url="https://x/y.git",
            build_dir="/b", git_ref="v1.2", notes="pinned",
        )
        row = textbooks.get_textbook(db_conn, "b")
        assert row["git_ref"] == "v1.2"
        assert row["notes"] == "pinned"

    def test_duplicate_book_is_refused(self, db_conn):
        textbooks.add_textbook(
            db_conn, book_xml_id="b", git_repo_url="https://x/y.git", build_dir="/b",
        )
        with pytest.raises(ValueError, match="already registered"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b", git_repo_url="https://x/z.git", build_dir="/c",
            )
        # The first registration is intact (no second row).
        assert len(textbooks.list_textbooks(db_conn)) == 1

    def test_credentialed_or_ssh_url_is_refused(self, db_conn):
        with pytest.raises(ValueError, match="public repos only"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b1",
                git_repo_url="git@github.com:x/y.git", build_dir="/b",
            )
        with pytest.raises(ValueError, match="public repos only"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b2",
                git_repo_url="https://user:pass@host/y.git", build_dir="/b",
            )
        with pytest.raises(ValueError, match="public repos only"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b3",
                git_repo_url="ssh://git@host/y.git", build_dir="/b",
            )
        with pytest.raises(ValueError, match="public repos only"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b4",
                git_repo_url="git://host/y.git", build_dir="/b",
            )

    def test_accepts_https_with_at_in_path_and_file_urls(self, db_conn):
        # A legitimate '@' outside the netloc must not be rejected.
        textbooks.add_textbook(
            db_conn, book_xml_id="at-in-path",
            git_repo_url="https://host/org/repo@v1.git", build_dir="/b",
        )
        # file:// URLs are accepted (local clones / tests).
        textbooks.add_textbook(
            db_conn, book_xml_id="local",
            git_repo_url="file:///srv/repos/book.git", build_dir="/b",
        )
        assert textbooks.get_textbook(db_conn, "at-in-path") is not None
        assert textbooks.get_textbook(db_conn, "local") is not None

    def test_same_repo_ref_for_another_book_is_clean_error(self, db_conn):
        # The table's UNIQUE(git_repo_url, git_ref) is surfaced as a ValueError,
        # not an escaping IntegrityError.
        textbooks.add_textbook(
            db_conn, book_xml_id="book-a",
            git_repo_url="https://x/shared.git", build_dir="/a",
        )
        with pytest.raises(ValueError, match="already registered for another book"):
            textbooks.add_textbook(
                db_conn, book_xml_id="book-b",
                git_repo_url="https://x/shared.git", build_dir="/b",
            )

    def test_path_unsafe_book_id_is_refused(self, db_conn):
        # book_xml_id names a clone/cache directory; reject separators and '..'.
        for bad in ("../evil", "a/b", "..", "a b", "book.id"):
            with pytest.raises(ValueError, match="invalid book_xml_id"):
                textbooks.add_textbook(
                    db_conn, book_xml_id=bad,
                    git_repo_url="https://x/y.git", build_dir="/b",
                )

    def test_missing_required_fields_refused(self, db_conn):
        with pytest.raises(ValueError, match="required"):
            textbooks.add_textbook(
                db_conn, book_xml_id="", git_repo_url="https://x/y.git", build_dir="/b",
            )
        with pytest.raises(ValueError, match="required"):
            textbooks.add_textbook(
                db_conn, book_xml_id="b", git_repo_url="https://x/y.git", build_dir="  ",
            )


class TestListTextbooks:
    def test_fresh_registry_is_empty(self, db_conn):
        # SP-1 (migration 0010): the TODO(maintainer) placeholder seed is retired.
        assert textbooks.list_textbooks(db_conn) == []

    def test_lists_books_sorted_by_id(self, db_conn):
        textbooks.add_textbook(db_conn, book_xml_id="zebra",
                               git_repo_url="https://x/z.git", build_dir="/b")
        textbooks.add_textbook(db_conn, book_xml_id="alpha",
                               git_repo_url="https://x/a.git", build_dir="/b")
        assert [r["book_xml_id"] for r in textbooks.list_textbooks(db_conn)] == [
            "alpha", "zebra",
        ]
