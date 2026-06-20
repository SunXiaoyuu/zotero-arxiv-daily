from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from time import sleep
from typing import Any

from loguru import logger
from tqdm import tqdm
import requests

from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from ..venues import Venue, load_venues_from_excel, resolve_project_path, venue_search_names


OPENALEX_API = "https://api.openalex.org"
REQUEST_TIMEOUT = (10, 30)


@register_retriever("openalex")
class OpenAlexRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        venues_path = self.retriever_config.get("venues_path")
        if not venues_path:
            raise ValueError("source.openalex.venues_path must be specified.")

        self.venues = load_venues_from_excel(venues_path)
        max_venues = self.retriever_config.get("max_venues")
        if max_venues:
            self.venues = self.venues[: int(max_venues)]
        if not self.venues:
            raise ValueError(f"No venues found in {venues_path}.")

        self.cache_path = resolve_project_path(
            self.retriever_config.get("cache_path", "data/openalex_sources_cache.json")
        )
        self.source_cache = self._load_source_cache()
        self.cache_dirty = False

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        per_venue_limit = int(self.retriever_config.get("per_venue_limit", 5))
        request_delay = float(self.retriever_config.get("request_delay_seconds", 0.2))
        from_publication_date = self._from_publication_date()

        raw_papers = []
        seen_work_ids = set()
        logger.info(
            f"Retrieving OpenAlex papers from {len(self.venues)} venues since {from_publication_date}"
        )
        for venue in tqdm(self.venues, desc="OpenAlex venues"):
            source = self._resolve_source(venue)
            if not source:
                continue

            works = self._fetch_works(source["id"], from_publication_date, per_venue_limit)
            for work in works:
                work_id = work.get("id")
                if work_id and work_id in seen_work_ids:
                    continue
                if work_id:
                    seen_work_ids.add(work_id)
                raw_papers.append({"work": work, "venue": venue, "source": source})

            if request_delay > 0:
                sleep(request_delay)

        if self.cache_dirty:
            self._save_source_cache()
        return raw_papers

    def retrieve_papers(self) -> list[Paper]:
        raw_papers = self._retrieve_raw_papers()
        logger.info("Processing OpenAlex papers...")
        papers = []
        for raw_paper in tqdm(raw_papers, total=len(raw_papers), desc="Converting OpenAlex papers"):
            try:
                paper = self.convert_to_paper(raw_paper)
            except Exception as exc:
                title = raw_paper.get("work", {}).get("title", raw_paper)
                logger.warning(f"Skipping OpenAlex paper {title}: {exc}")
                continue
            if paper is not None:
                papers.append(paper)
        return papers

    def convert_to_paper(self, raw_paper: dict[str, Any]) -> Paper | None:
        work = raw_paper["work"]
        title = work.get("title")
        if not title:
            return None

        authors = [
            author_name
            for authorship in work.get("authorships", [])
            if (author_name := authorship.get("author", {}).get("display_name"))
        ]
        abstract = _abstract_from_inverted_index(work.get("abstract_inverted_index"))

        primary_location = work.get("primary_location") or {}
        best_oa_location = work.get("best_oa_location") or {}
        open_access = work.get("open_access") or {}
        url = (
            primary_location.get("landing_page_url")
            or work.get("doi")
            or work.get("id")
        )
        pdf_url = (
            primary_location.get("pdf_url")
            or best_oa_location.get("pdf_url")
            or open_access.get("oa_url")
            or url
        )

        source = raw_paper.get("source") or {}
        venue = raw_paper["venue"]
        source_display_name = source.get("display_name") or _source_display_name(work)

        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            venue=_format_venue(venue, source_display_name),
            pdf_url=pdf_url,
            full_text=None,
        )

    def _resolve_source(self, venue: Venue) -> dict[str, str] | None:
        cached = self.source_cache.get(venue.name)
        if cached is not None:
            return cached if cached.get("id") else None

        for query in venue_search_names(venue.name):
            source = self._search_source(query)
            if source:
                self.source_cache[venue.name] = source
                self.cache_dirty = True
                logger.debug(f"Mapped venue {venue.name} to OpenAlex source {source['display_name']}")
                return source

        logger.warning(f"Could not map venue to OpenAlex source: {venue.name}")
        self.source_cache[venue.name] = {"id": "", "display_name": ""}
        self.cache_dirty = True
        return None

    def _search_source(self, query: str) -> dict[str, str] | None:
        params = {"search": query, "per-page": 5}
        self._add_mailto(params)
        response = requests.get(f"{OPENALEX_API}/sources", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None

        source = _best_source_match(query, results)
        source_id = source.get("id")
        display_name = source.get("display_name")
        if not source_id or not display_name:
            return None

        return {
            "id": source_id.rsplit("/", 1)[-1],
            "display_name": display_name,
            "type": source.get("type") or "",
        }

    def _fetch_works(self, source_id: str, from_publication_date: str, per_venue_limit: int) -> list[dict[str, Any]]:
        params = {
            "filter": f"primary_location.source.id:{source_id},from_publication_date:{from_publication_date}",
            "sort": "publication_date:desc",
            "per-page": per_venue_limit,
        }
        self._add_mailto(params)
        response = requests.get(f"{OPENALEX_API}/works", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json().get("results", [])

    def _from_publication_date(self) -> str:
        configured = self.retriever_config.get("from_publication_date")
        if configured:
            return str(configured)

        days = int(self.retriever_config.get("days", 30))
        return (date.today() - timedelta(days=days)).isoformat()

    def _add_mailto(self, params: dict[str, Any]) -> None:
        mailto = self.retriever_config.get("mailto")
        if mailto:
            params["mailto"] = str(mailto)

    def _load_source_cache(self) -> dict[str, dict[str, str]]:
        if not self.cache_path.exists():
            return {}
        try:
            with self.cache_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            logger.warning(f"Failed to load OpenAlex source cache {self.cache_path}: {exc}")
            return {}
        return data if isinstance(data, dict) else {}

    def _save_source_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as file:
            json.dump(self.source_cache, file, ensure_ascii=False, indent=2, sort_keys=True)


def _abstract_from_inverted_index(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""

    positioned_words = []
    for word, positions in inverted_index.items():
        positioned_words.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(positioned_words))


def _source_display_name(work: dict[str, Any]) -> str | None:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return source.get("display_name")


def _format_venue(venue: Venue, source_display_name: str | None) -> str:
    display_name = source_display_name or venue.name
    kind = "Journal" if venue.kind == "journal" else "Conference"
    details = [kind]
    if venue.ccf:
        details.append(venue.ccf)
    if venue.rank:
        details.append(venue.rank)
    return f"{display_name} ({'; '.join(details)})"


def _best_source_match(query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    query_lower = query.lower()
    for result in results:
        if (result.get("display_name") or "").lower() == query_lower:
            return result
    for result in results:
        if query_lower in (result.get("display_name") or "").lower():
            return result
    return results[0]
