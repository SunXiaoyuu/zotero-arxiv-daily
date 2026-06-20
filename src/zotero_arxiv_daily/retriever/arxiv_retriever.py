from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
from datetime import date, datetime
import feedparser
from tqdm import tqdm
import multiprocessing
import os
from queue import Empty
from time import sleep
from typing import Any, Callable, TypeVar
from loguru import logger
import requests

T = TypeVar("T")

DOWNLOAD_TIMEOUT = (10, 60)
RSS_FETCH_TIMEOUT = (10, 30)
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180


def _download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def _fetch_arxiv_rss_feed(query: str) -> Any:
    url = f"https://rss.arxiv.org/atom/{query}"
    try:
        response = requests.get(url, timeout=RSS_FETCH_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to retrieve arXiv RSS feed for query {query}: {exc}") from exc

    feed = feedparser.parse(response.content)
    title = feed.feed.get("title", "")
    if "Feed error for query" in title:
        raise ValueError(f"Invalid ARXIV_QUERY: {query}.")
    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(
            f"Failed to parse arXiv RSS feed for query {query}: "
            f"{feed.get('bozo_exception', 'unknown parser error')}"
        )
    if not feed.feed:
        raise RuntimeError(f"Failed to parse arXiv RSS feed for query {query}: missing feed metadata")
    return feed


def _as_https(url: str | None) -> str | None:
    if url is None:
        return None
    return url.replace("http://arxiv.org/", "https://arxiv.org/", 1)


def _format_arxiv_venue(raw_paper: ArxivResult) -> str:
    journal_ref = getattr(raw_paper, "journal_ref", None)
    if journal_ref:
        return journal_ref

    primary_category = getattr(raw_paper, "primary_category", None)
    if primary_category:
        return f"arXiv ({primary_category})"

    categories = getattr(raw_paper, "categories", None) or []
    if categories:
        return f"arXiv ({', '.join(categories)})"

    return "arXiv"


def _published_date_from_arxiv(raw_paper: ArxivResult) -> date | None:
    published = getattr(raw_paper, "published", None)
    if isinstance(published, datetime):
        return published.date()
    if isinstance(published, date):
        return published
    if isinstance(published, str):
        try:
            return datetime.fromisoformat(published.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _run_in_subprocess(
    result_queue: Any,
    func: Callable[..., T | None],
    args: tuple[Any, ...],
) -> None:
    try:
        result_queue.put(("ok", func(*args)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_hard_timeout(
    func: Callable[..., T | None],
    args: tuple[Any, ...],
    *,
    timeout: float,
    operation: str,
    paper_title: str,
) -> T | None:
    start_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in start_methods else start_methods[0])
    result_queue = context.Queue()
    process = context.Process(target=_run_in_subprocess, args=(result_queue, func, args))
    process.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except Empty:
        if process.is_alive():
            process.kill()
        process.join(5)
        result_queue.close()
        result_queue.join_thread()
        logger.warning(f"{operation} timed out for {paper_title} after {timeout} seconds")
        return None

    process.join(5)
    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload

    logger.warning(f"{operation} failed for {paper_title}: {payload}")
    return None


def _extract_text_from_pdf_worker(pdf_url: str) -> str:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        _download_file(pdf_url, path)
        return extract_markdown_from_pdf(path)


def _extract_text_from_html_worker(html_url: str) -> str | None:
    import trafilatura

    downloaded = trafilatura.fetch_url(html_url)
    if downloaded is None:
        raise ValueError(f"Failed to download HTML from {html_url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No text extracted from {html_url}")
    return text


def _extract_text_from_tar_worker(source_url: str, paper_id: str, paper_title: str | None = None) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        _download_file(source_url, path)
        file_contents = extract_tex_code_from_tar(path, paper_id, paper_title=paper_title)
        if not file_contents or "all" not in file_contents:
            raise ValueError("Main tex file not found.")
        return file_contents["all"]


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")

    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10, delay_seconds=10)
        query = '+'.join(self.config.source.arxiv.category)
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        # Get the latest paper from arxiv rss feed
        feed = _fetch_arxiv_rss_feed(query)
        raw_papers = []
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        all_paper_ids = [
            i.id.removeprefix("oai:arXiv.org:")
            for i in feed.entries
            if i.get("arxiv_announce_type", "new") in allowed_announce_types
        ]
        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        max_batch_retries = 5
        batch_retry_delay = 30
        for i in range(0, len(all_paper_ids), 20):
            search = arxiv.Search(id_list=all_paper_ids[i:i + 20])
            for attempt in range(max_batch_retries):
                try:
                    batch = list(client.results(search))
                    bar.update(len(batch))
                    raw_papers.extend(batch)
                    break
                except arxiv.HTTPError as exc:
                    if exc.status == 429 and attempt < max_batch_retries - 1:
                        wait = batch_retry_delay * (attempt + 1)
                        logger.warning(f"arXiv API 429 on batch {i // 20}, retry {attempt + 1}/{max_batch_retries} in {wait}s")
                        sleep(wait)
                    else:
                        raise
            if i + 20 < len(all_paper_ids):
                sleep(3)
        bar.close()

        from_publication_date = _parse_date(self.config.source.arxiv.get("from_publication_date"))
        if from_publication_date is not None:
            raw_papers = [
                paper for paper in raw_papers
                if (_published_date_from_arxiv(paper) or date.min) >= from_publication_date
            ]

        return raw_papers

    def convert_to_paper(self, raw_paper: ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=_as_https(raw_paper.entry_id),
            venue=_format_arxiv_venue(raw_paper),
            published_date=_published_date_from_arxiv(raw_paper),
            pdf_url=_as_https(pdf_url),
            full_text=None,
        )

    def populate_full_text(self, paper: Paper) -> Paper:
        if paper.full_text is not None:
            return paper
        full_text = extract_text_from_tar(paper)
        if full_text is None:
            full_text = extract_text_from_html(paper)
        if full_text is None:
            full_text = extract_text_from_pdf(paper)
        paper.full_text = full_text
        return paper


def _get_paper_title(paper: ArxivResult | Paper) -> str:
    return paper.title


def _get_paper_abs_url(paper: ArxivResult | Paper) -> str:
    return _as_https(getattr(paper, "entry_id", None) or paper.url)


def _get_paper_source_url(paper: ArxivResult | Paper) -> str | None:
    if hasattr(paper, "source_url"):
        return _as_https(paper.source_url())
    return _get_paper_abs_url(paper).replace("/abs/", "/e-print/")


def extract_text_from_html(paper: ArxivResult | Paper) -> str | None:
    html_url = _get_paper_abs_url(paper).replace("/abs/", "/html/")
    try:
        return _extract_text_from_html_worker(html_url)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {_get_paper_title(paper)}: {exc}")
        return None


def extract_text_from_pdf(paper: ArxivResult | Paper) -> str | None:
    if paper.pdf_url is None:
        logger.warning(f"No PDF URL available for {_get_paper_title(paper)}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_pdf_worker,
        (paper.pdf_url,),
        timeout=PDF_EXTRACT_TIMEOUT,
        operation="PDF extraction",
        paper_title=_get_paper_title(paper),
    )


def extract_text_from_tar(paper: ArxivResult | Paper) -> str | None:
    source_url = _get_paper_source_url(paper)
    if source_url is None:
        logger.warning(f"No source URL available for {_get_paper_title(paper)}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_tar_worker,
        (source_url, _get_paper_abs_url(paper), _get_paper_title(paper)),
        timeout=TAR_EXTRACT_TIMEOUT,
        operation="Tar extraction",
        paper_title=_get_paper_title(paper),
    )
