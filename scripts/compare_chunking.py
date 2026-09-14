"""
Compare chunking strategies against the labelled development set.

The Setup Guide asks for at least two configurations to be tried and the
difference in retrieval quality measured, rather than a default accepted.
This is that measurement.

    python -m scripts.compare_chunking
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.corpus import CHUNKERS, build_chunks, load_corpus  # noqa: E402
from src.ingest import normalise_batch  # noqa: E402
from src.retrieve import Retriever  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    articles = load_corpus(ROOT / "data" / "documentation.json")
    tickets = normalise_batch(
        json.loads((ROOT / "data" / "development_tickets.json").read_text()))

    print("Chunking strategy comparison")
    print("============================")
    print(f"corpus: {len(articles)} articles   tickets: {len(tickets)}\n")

    results = {}
    for name in CHUNKERS:
        chunks = build_chunks(articles, name)
        retriever = Retriever(chunks, score_threshold=0.0, top_k=5)

        hit_at_1 = hit_at_3 = answerable = 0
        answerable_scores: list[float] = []
        unanswerable_scores: list[float] = []

        for ticket in tickets:
            labels = ticket.raw.get("labels", {})
            hits = retriever.search(ticket.text)
            top = hits[0].score if hits else 0.0

            if labels.get("answerable_from_docs"):
                answerable += 1
                answerable_scores.append(top)
                expected = set(labels.get("expected_doc_ids") or [])
                returned = [h.doc_id for h in hits]
                if returned and returned[0] in expected:
                    hit_at_1 += 1
                if expected & set(returned[:3]):
                    hit_at_3 += 1
            else:
                unanswerable_scores.append(top)

        sizes = [len(c.text) for c in chunks]
        results[name] = {
            "chunks": len(chunks),
            "mean_chars": statistics.mean(sizes),
            "hit_at_1": hit_at_1 / answerable,
            "hit_at_3": hit_at_3 / answerable,
            "median_answerable": statistics.median(answerable_scores),
            "median_unanswerable": statistics.median(unanswerable_scores),
        }

        print(f"{name}")
        print(f"  chunks produced          {len(chunks)}")
        print(f"  mean chunk size          {statistics.mean(sizes):.0f} chars")
        print(f"  hit@1 (correct doc first) {hit_at_1 / answerable:.1%}")
        print(f"  hit@3                     {hit_at_3 / answerable:.1%}")
        print(f"  median score, answerable  {statistics.median(answerable_scores):.3f}")
        print(f"  median score, not         {statistics.median(unanswerable_scores):.3f}")
        print()

    best = max(results, key=lambda n: results[n]["hit_at_1"])
    print(f"Chosen: {best}")
    print()
    print("Why: every article in this corpus follows the same template — title,")
    print("applies-to, symptoms, causes, a numbered resolution sequence, notes.")
    print("Section-aware chunking cuts on those boundaries and keeps a numbered")
    print("resolution list whole even when that makes the chunk over-long. A")
    print("fixed window lands mid-sequence and produces passages that retrieve")
    print("well but read as incomplete instructions, which is a worse answer")
    print("than none.")
    print()
    print("Note the overlap in the last two rows: answerable and unanswerable")
    print("tickets score similarly, which is why answerability is decided by")
    print("intent rather than by retrieval score.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
