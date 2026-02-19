"""Unit tests for docs_checker module."""

import pytest
from unittest.mock import patch, MagicMock

from azure_local_deploy.docs_checker import (
    Recommendation,
    DocsReport,
    DOC_PAGES,
    KNOWN_REQUIREMENTS,
    _extract_text,
    sentence_overlap,
    check_docs,
)


# ---- Recommendation dataclass -------------------------------------------

def test_recommendation_creation():
    rec = Recommendation(
        category="Hardware",
        requirement="Minimum 32 GB RAM",
        severity="required",
        source_page="system_requirements",
        source_url="https://example.com",
    )
    assert rec.category == "Hardware"
    assert rec.severity == "required"


# ---- DocsReport ---------------------------------------------------------

def test_docs_report_add():
    report = DocsReport()
    rec = Recommendation("HW", "test", "required", "sys_req")
    report.add(rec)
    assert len(report.recommendations) == 1


def test_docs_report_required_items():
    report = DocsReport()
    report.add(Recommendation("HW", "a", "required", "p1"))
    report.add(Recommendation("HW", "b", "recommended", "p1"))
    report.add(Recommendation("HW", "c", "required", "p2"))
    assert len(report.required_items) == 2
    assert len(report.recommended_items) == 1


def test_docs_report_empty():
    report = DocsReport()
    assert len(report.required_items) == 0
    assert len(report.recommended_items) == 0
    assert report.pages_fetched == 0


# ---- DOC_PAGES and KNOWN_REQUIREMENTS -----------------------------------

def test_doc_pages_not_empty():
    assert len(DOC_PAGES) > 0


def test_doc_pages_urls_are_https():
    for name, url in DOC_PAGES.items():
        assert url.startswith("https://"), f"{name} URL must be HTTPS"


def test_known_requirements_not_empty():
    assert len(KNOWN_REQUIREMENTS) > 0


def test_known_requirements_have_required_fields():
    for req in KNOWN_REQUIREMENTS:
        assert "category" in req, "Missing 'category'"
        assert "requirement" in req, "Missing 'requirement'"
        assert "severity" in req, "Missing 'severity'"
        assert req["severity"] in ("required", "recommended", "optional")


# ---- _extract_text -------------------------------------------------------

def test_extract_text_strips_tags():
    html = "<html><body><p>Hello <b>world</b></p></body></html>"
    text = _extract_text(html)
    assert "Hello" in text
    assert "world" in text
    assert "<b>" not in text


def test_extract_text_empty():
    assert _extract_text("") == ""


def test_extract_text_removes_scripts():
    html = "<html><body><script>var x=1;</script><p>Content</p></body></html>"
    text = _extract_text(html)
    assert "Content" in text
    assert "var x" not in text


# ---- sentence_overlap ----------------------------------------------------

def test_sentence_overlap_identical():
    score = sentence_overlap("hello world", "hello world")
    assert score == 1.0


def test_sentence_overlap_no_match():
    score = sentence_overlap("apple banana cherry", "delta epsilon phi")
    assert score == 0.0


def test_sentence_overlap_partial():
    score = sentence_overlap("the quick brown fox", "the slow brown dog")
    assert 0.0 < score < 1.0


# ---- check_docs (with mocked HTTP) --------------------------------------

@patch("azure_local_deploy.docs_checker._fetch_page")
def test_check_docs_offline(mock_fetch):
    """check_docs should return known requirements even when all fetches fail."""
    mock_fetch.return_value = ""  # simulate failed fetches
    report = check_docs()
    # At minimum, known requirements should be present
    assert len(report.recommendations) > 0
    assert any(r.severity == "required" for r in report.recommendations)


@patch("azure_local_deploy.docs_checker._fetch_page")
def test_check_docs_with_content(mock_fetch):
    """check_docs with some HTML content should add known reqs + potentially more."""
    mock_fetch.return_value = "<html><body><p>Minimum 32 GB RAM required. TPM 2.0 must be enabled.</p></body></html>"
    report = check_docs()
    assert len(report.recommendations) >= len(KNOWN_REQUIREMENTS)
