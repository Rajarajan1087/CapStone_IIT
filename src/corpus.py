"""
The documentation corpus: loading, chunking, and the chunking comparison.

Serves FR-03. Feeds acceptance criterion A4.

CloudServe's support knowledge base is 29 articles, each written to the same
internal shape: a title, an "applies to" line, a Symptoms list, Common
causes, a numbered Resolution sequence, and Notes. That regularity is the
single most important fact about this corpus, because it tells you where
NOT to cut.

Splitting inside a numbered resolution sequence produces passages that
retrieve well -- they contain the right keywords -- but read as incomplete
instructions. A customer told to "clear cookies for the CloudServe domain"
without the preceding step that establishes when to do that has been given
a worse answer than no answer. The Setup Guide asks for at least two
chunking configurations to be tried and the difference measured; this
module implements both and `scripts/compare_chunking.py` measures them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ingest import clean_text


@dataclass
class Chunk:
    """
    One retrievable passage.

    `doc_id` is what makes citation verifiable (A6): every chunk resolves
    back to a real article in documentation.json, and the section name
    records which part of that article the text came from.
    """

    chunk_id: str
    doc_id: str
    title: str
    category: str
    section: str
    text: str
    last_reviewed_days_ago: int = 0

    def citation_text(self) -> str:
        """How this passage is presented to the generator."""
        return f"[{self.doc_id}] {self.title} — {self.section}\n{self.text}"


@dataclass
class Article:
    doc_id: str
    title: str
    category: str
    applies_to: str
    content: str
    related_docs: list[str] = field(default_factory=list)
    last_reviewed_days_ago: int = 0


def load_corpus(path: str | Path) -> list[Article]:
    """Load documentation.json, tolerating a missing or malformed record."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    articles: list[Article] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("doc_id"):
            continue
        articles.append(
            Article(
                doc_id=str(item["doc_id"]),
                title=clean_text(item.get("title")),
                category=clean_text(item.get("category")),
                applies_to=clean_text(item.get("applies_to")),
                content=item.get("content") or "",
                related_docs=list(item.get("related_docs") or []),
                last_reviewed_days_ago=int(item.get("last_reviewed_days_ago") or 0),
            )
        )
    return articles


# --- Strategy A: fixed-size sliding window --------------------------------
# The conventional default, and the one the Setup Guide shows. Included so
# the comparison is against a real baseline rather than a straw man.

def chunk_fixed(article: Article, chunk_size: int = 800,
                overlap: int = 120) -> list[Chunk]:
    """
    Split on character count with overlap, ignoring document structure.

    Cheap and general. Its weakness on this corpus is precisely that it is
    structure-blind: a 800-character window lands mid-resolution-sequence
    more often than not, because the resolution sections are longer than
    that.
    """
    text = clean_text(article.content)
    chunks: list[Chunk] = []
    start = 0
    index = 0
    step = max(1, chunk_size - overlap)
    while start < len(text):
        window = text[start:start + chunk_size].strip()
        if window:
            chunks.append(Chunk(
                chunk_id=f"{article.doc_id}::fixed::{index}",
                doc_id=article.doc_id,
                title=article.title,
                category=article.category,
                section="(fixed window)",
                text=window,
                last_reviewed_days_ago=article.last_reviewed_days_ago,
            ))
            index += 1
        start += step
    return chunks


# --- Strategy B: section-aware --------------------------------------------
# Splits on the article's own markdown headings, then keeps each numbered
# resolution sequence intact as one unit.

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def chunk_section_aware(article: Article, max_chars: int = 1400) -> list[Chunk]:
    """
    Split on the article's own section headings, never inside a step list.

    Every article in this corpus follows the same template, so the headings
    are reliable boundaries. A section longer than max_chars is split on
    paragraph breaks rather than mid-sentence, and a numbered list is kept
    whole even when that pushes a chunk over the limit -- an over-long but
    complete instruction is more useful than a tidy but truncated one.

    The title and section name are prepended to each chunk's text. Customers
    do not describe problems using the words in the article title (Ines's
    point in discovery: "my deployment keeps dying" versus "resolving
    container health check failures"), so carrying the title into the
    embedded text gives the retriever a second chance at the match.
    """
    text = clean_text(article.content)
    if not text:
        return []

    # Locate headings; everything before the first is the preamble.
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, str]] = []
    if not matches:
        sections.append(("(body)", text))
    else:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("(intro)", preamble))
        for i, match in enumerate(matches):
            name = match.group(1).strip()
            body_start = match.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            if body:
                sections.append((name, body))

    chunks: list[Chunk] = []
    index = 0
    for name, body in sections:
        for piece in _split_section(body, max_chars):
            header = f"{article.title} — {name}"
            chunks.append(Chunk(
                chunk_id=f"{article.doc_id}::sec::{index}",
                doc_id=article.doc_id,
                title=article.title,
                category=article.category,
                section=name,
                # Title and section carried into the embedded text so a
                # query phrased in the customer's words can still match.
                text=f"{header}\n{body if len(body) <= max_chars else piece}",
                last_reviewed_days_ago=article.last_reviewed_days_ago,
            ))
            index += 1
    return chunks


_NUMBERED_STEP_RE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)


def _split_section(body: str, max_chars: int) -> list[str]:
    """
    Split an over-long section without breaking a numbered sequence.

    If the section contains numbered steps it is returned whole regardless
    of length. That is a deliberate trade: a resolution sequence cut in
    half retrieves a fragment that reads like complete instructions, which
    is the failure mode this corpus is most exposed to.
    """
    if len(body) <= max_chars:
        return [body]
    if _NUMBERED_STEP_RE.search(body):
        return [body]  # keep step lists intact, whatever the length

    pieces: list[str] = []
    current = ""
    for paragraph in body.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > max_chars:
            pieces.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        pieces.append(current.strip())
    return pieces


CHUNKERS = {
    "fixed": chunk_fixed,
    "section_aware": chunk_section_aware,
}


def build_chunks(articles: list[Article], strategy: str = "section_aware") -> list[Chunk]:
    """Chunk every article with the named strategy."""
    if strategy not in CHUNKERS:
        raise ValueError(f"unknown chunking strategy {strategy!r}; "
                         f"choose from {sorted(CHUNKERS)}")
    chunker = CHUNKERS[strategy]
    chunks: list[Chunk] = []
    for article in articles:
        chunks.extend(chunker(article))
    return chunks
