"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser

from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout
import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)
    monkeypatch.setattr(arxiv_retriever, "_fetch_arxiv_rss_feed", lambda query: mock_feedparser)

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]
    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in new_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            primary_category="cs.AI",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            return iter(fake_results)

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    extraction_calls = []
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: extraction_calls.append("html"))
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: extraction_calls.append("pdf"))
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: extraction_calls.append("tar"))

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert set(p.title for p in papers) == set(e.title for e in new_entries)
    assert all(p.full_text is None for p in papers)
    assert all(p.venue == "arXiv (cs.AI)" for p in papers)
    assert extraction_calls == []


def test_arxiv_populate_full_text_runs_after_retrieval(config, monkeypatch):
    paper = SimpleNamespace(
        source="arxiv",
        title="Selected Paper",
        authors=["Test Author"],
        abstract="Test abstract",
        url="http://arxiv.org/abs/2606.00001v1",
        pdf_url="http://arxiv.org/pdf/2606.00001v1",
        full_text=None,
    )
    calls = []
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda p: calls.append(("tar", p.url)) or None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda p: calls.append(("html", p.url)) or "full text")
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda p: calls.append(("pdf", p.url)) or None)

    retriever = ArxivRetriever(config)
    populated = retriever.populate_full_text(paper)

    assert populated.full_text == "full text"
    assert calls == [
        ("tar", "http://arxiv.org/abs/2606.00001v1"),
        ("html", "http://arxiv.org/abs/2606.00001v1"),
    ]


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]
