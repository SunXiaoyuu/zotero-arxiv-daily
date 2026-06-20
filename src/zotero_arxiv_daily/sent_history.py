from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from loguru import logger

from .protocol import Paper
from .venues import resolve_project_path


class SentHistory:
    def __init__(self, path: str | Path):
        self.path = resolve_project_path(path)
        self.data = self._load()
        self.keys = {
            item.get("key")
            for item in self.data.get("papers", [])
            if item.get("key")
        }

    def contains(self, paper: Paper) -> bool:
        return paper_key(paper) in self.keys

    def add_many(self, papers: list[Paper]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.data.setdefault("papers", [])
        added = 0
        for paper in papers:
            key = paper_key(paper)
            if key in self.keys:
                continue
            existing.append(
                {
                    "key": key,
                    "title": paper.title,
                    "source": paper.source,
                    "url": paper.url,
                    "venue": paper.venue,
                    "sent_at": now,
                }
            )
            self.keys.add(key)
            added += 1

        if added:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "papers": []}
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            logger.warning(f"Failed to load sent history {self.path}: {exc}")
            return {"version": 1, "papers": []}

        if not isinstance(data, dict):
            return {"version": 1, "papers": []}
        data.setdefault("version", 1)
        data.setdefault("papers", [])
        return data


def paper_key(paper: Paper) -> str:
    url = (paper.url or paper.pdf_url or "").strip()
    arxiv_id = _extract_arxiv_id(url)
    if arxiv_id:
        return f"arxiv:{arxiv_id}"

    doi = _extract_doi(url)
    if doi:
        return f"doi:{doi.lower()}"

    if url:
        return f"url:{_normalize_url(url)}"

    title = re.sub(r"\s+", " ", paper.title).strip().lower()
    return f"title:{title}"


def _extract_arxiv_id(url: str) -> str | None:
    match = re.search(r"arxiv\.org/(?:abs|pdf|html|e-print)/([^?#/]+)", url, flags=re.IGNORECASE)
    if not match:
        return None
    arxiv_id = match.group(1).removesuffix(".pdf")
    return re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)


def _extract_doi(url: str) -> str | None:
    match = re.search(r"(?:doi\.org/|doi:)(10\.\S+)", url, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).rstrip(".,;)")


def _normalize_url(url: str) -> str:
    normalized = url.strip().lower()
    normalized = re.sub(r"^http://", "https://", normalized)
    return normalized.rstrip("/")
