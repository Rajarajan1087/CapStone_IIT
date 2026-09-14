"""
Retrieval over the documentation corpus.

Serves FR-03. Checked by acceptance criterion A4.

Two design decisions here are worth stating plainly, because both were
forced by constraints and both have costs.

1. NO EXTERNAL VECTOR DATABASE.
   The pack's Setup Guide suggests Chroma with sentence-transformers
   embeddings. Neither could be installed in the environment this was
   built in, so retrieval is implemented directly: a TF-IDF weighted
   vector space with cosine similarity, in pure Python over a 29-article
   corpus of ~145 passages.

   At this scale that is not a compromise -- an exhaustive scan of 145
   vectors is microseconds, and an approximate-nearest-neighbour index
   would add complexity for no measurable gain. What IS lost is semantic
   matching: TF-IDF matches words, not meanings, so "my deployment keeps
   dying" does not automatically match "container health check failures".
   That is exactly the gap discovery identified as CloudServe's core
   problem, so it is mitigated deliberately rather than ignored: see the
   synonym expansion below.

2. RETURNING NOTHING IS A CORRECT ANSWER.
   A relevance floor is applied and results below it are discarded. The
   Build Specification is explicit that retrieval which returns something
   for every query regardless of relevance "hides failure". Roughly 29% of
   tickets are not answerable from this corpus at all, and for those the
   right result is an empty list.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from src.corpus import Chunk

# Words carrying no retrieval signal. Deliberately short: an aggressive
# stop list removes terms like "not" and "cannot" that genuinely
# distinguish a symptom from its absence.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those is are was were be been
being do does did doing have has had having i you he she it we they my our your
of in on at to for with from by as into over under again further once here there
all any both each few more most other some such no nor only own same so too very
can will just should now
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Bridges the vocabulary gap between how customers describe problems and
# how the documentation titles them. Every entry below was derived from
# the intent labels and article titles in the supplied data -- this is the
# "no path between those two phrases in a keyword search" problem Ines
# described in discovery, addressed explicitly because TF-IDF alone cannot.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "dying": ("failure", "failing", "crash"),
    "died": ("failure", "failing"),
    "broken": ("failure", "error"),
    "stuck": ("failure", "timeout", "hang"),
    "slow": ("latency", "performance", "degradation"),
    "sluggish": ("latency", "performance"),
    "login": ("authentication", "credential", "signin"),
    "signin": ("authentication", "credential", "login"),
    "password": ("credential", "authentication"),
    "locked": ("lockout", "credential", "authentication"),
    "bill": ("billing", "invoice", "charge"),
    "charged": ("billing", "invoice", "overage"),
    "cost": ("billing", "spend", "invoice"),
    "quota": ("limit", "overage", "rate"),
    "throttled": ("rate", "limit", "quota"),
    "429": ("rate", "limit", "throttle"),
    "rollback": ("revert", "release", "deployment"),
    "revert": ("rollback", "release"),
    "deploy": ("deployment", "release", "build"),
    "deployment": ("deploy", "release", "container"),
    "build": ("deployment", "dependency", "compile"),
    "key": ("credential", "token", "api"),
    "token": ("key", "credential", "api"),
    "sso": ("saml", "signon", "authentication"),
    "saml": ("sso", "authentication"),
    "export": ("extract", "download", "backup"),
    "backup": ("restore", "retention", "export"),
    "residency": ("region", "regional", "location"),
    "gdpr": ("compliance", "residency", "audit"),
    "audit": ("compliance", "logging", "evidence"),
    "breach": ("compromise", "security", "incident"),
    "hacked": ("compromise", "security", "incident"),
    "compromised": ("compromise", "security", "incident"),
    "webhook": ("delivery", "retry", "signature"),
    "permission": ("role", "access", "member"),
    "role": ("permission", "access", "member"),
    "invite": ("member", "team", "access"),
    "database": ("connection", "pool", "db"),
    "connection": ("pool", "database", "timeout"),
    "scaling": ("autoscaling", "instance", "capacity"),
    "pagination": ("cursor", "page", "results"),
    "onboarding": ("setup", "first", "walkthrough"),
    "migrate": ("migration", "migrating", "onboarding"),
}


def tokenise(text: str, expand: bool = False) -> list[str]:
    """
    Lowercase, split on word characters, drop stopwords.

    When `expand` is true (queries only, never documents) each token also
    contributes its synonyms. Expanding the query rather than the corpus
    keeps the index honest: a document is only ever indexed under words it
    actually contains.
    """
    tokens = [t for t in _TOKEN_RE.findall(text.lower())
              if t not in _STOPWORDS and len(t) > 1]
    if not expand:
        return tokens
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(_SYNONYMS.get(token, ()))
    return expanded


@dataclass
class RetrievalResult:
    """One retrieved passage and why it was returned."""

    chunk: Chunk
    score: float

    @property
    def doc_id(self) -> str:
        return self.chunk.doc_id


class Retriever:
    """
    TF-IDF vector space over the documentation corpus.

    Built once at startup from the chunk list, then queried per ticket.
    Index construction over 145 passages takes single-digit milliseconds,
    so there is no persistence layer and therefore none of the stale-index
    failure modes that come with one.
    """

    def __init__(self, chunks: list[Chunk], score_threshold: float = 0.12,
                 top_k: int = 5) -> None:
        self.chunks = chunks
        self.score_threshold = score_threshold
        self.top_k = top_k

        self._doc_frequency: Counter[str] = Counter()
        self._vectors: list[dict[str, float]] = []
        self._norms: list[float] = []
        self._build()

    def _build(self) -> None:
        tokenised = [tokenise(c.text) for c in self.chunks]
        for tokens in tokenised:
            self._doc_frequency.update(set(tokens))

        total_docs = max(1, len(self.chunks))
        for tokens in tokenised:
            counts = Counter(tokens)
            length = max(1, len(tokens))
            vector: dict[str, float] = {}
            for term, count in counts.items():
                tf = count / length
                # Smoothed IDF; +1 keeps a term appearing in every document
                # from collapsing to exactly zero weight.
                idf = math.log((total_docs + 1) / (self._doc_frequency[term] + 1)) + 1.0
                vector[term] = tf * idf
            self._vectors.append(vector)
            self._norms.append(math.sqrt(sum(v * v for v in vector.values())) or 1.0)

    def search(self, query: str, top_k: int | None = None,
               score_threshold: float | None = None) -> list[RetrievalResult]:
        """
        Return the best-matching passages, or an empty list.

        An empty list is a legitimate outcome and is what the pipeline
        relies on to recognise a question this corpus cannot answer. It is
        never padded with the least-bad match.
        """
        top_k = self.top_k if top_k is None else top_k
        threshold = self.score_threshold if score_threshold is None else score_threshold

        query_tokens = tokenise(query, expand=True)
        if not query_tokens:
            return []

        counts = Counter(query_tokens)
        length = max(1, len(query_tokens))
        total_docs = max(1, len(self.chunks))
        query_vector: dict[str, float] = {}
        for term, count in counts.items():
            idf = math.log((total_docs + 1) / (self._doc_frequency[term] + 1)) + 1.0
            query_vector[term] = (count / length) * idf
        query_norm = math.sqrt(sum(v * v for v in query_vector.values())) or 1.0

        scored: list[RetrievalResult] = []
        for i, vector in enumerate(self._vectors):
            # Iterate the shorter side; queries are far shorter than chunks.
            dot = sum(weight * vector.get(term, 0.0)
                      for term, weight in query_vector.items())
            if dot <= 0:
                continue
            score = dot / (query_norm * self._norms[i])
            if score >= threshold:
                scored.append(RetrievalResult(chunk=self.chunks[i], score=score))

        scored.sort(key=lambda r: r.score, reverse=True)
        return self._diversify(scored, top_k)

    @staticmethod
    def _diversify(results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
        """
        Prefer covering several articles over many passages from one.

        Without this, all five slots are routinely filled by consecutive
        sections of a single article, which crowds out a second article
        that might hold the actual answer. At most two passages per article
        are admitted before any third is considered.
        """
        per_doc: Counter[str] = Counter()
        primary: list[RetrievalResult] = []
        overflow: list[RetrievalResult] = []
        for result in results:
            if per_doc[result.doc_id] < 2:
                per_doc[result.doc_id] += 1
                primary.append(result)
            else:
                overflow.append(result)
        return (primary + overflow)[:top_k]

    def resolve(self, doc_id: str) -> list[Chunk]:
        """
        Every chunk belonging to an article.

        Used to verify a citation: A6 is checked by following a citation
        back to the passage it claims to support, so that lookup has to
        exist as a first-class operation rather than a test-only helper.
        """
        return [c for c in self.chunks if c.doc_id == doc_id]


# --- Why the threshold is 0.12, and what it can and cannot do -------------
#
# Measured over the 500 development tickets, section-aware chunking:
#
#   threshold   precision   recall
#       0.08       71.9%     99.4%
#       0.12       72.7%     97.5%   <- chosen
#       0.20       74.4%     79.8%
#       0.30       78.0%     41.7%
#
# Precision never exceeds about 78% at any threshold, and buying those few
# points costs more than half of recall. The reason is visible in the data:
# `feature_request` tickets score a median of 0.285, HIGHER than the median
# answerable ticket (0.280), because a customer requesting a feature
# discusses real product areas in the documentation's own vocabulary. No
# similarity cut-off can separate "asks about deployments and is answered by
# the deployment article" from "asks for a new deployment feature that does
# not exist yet".
#
# The conclusion that follows is architectural rather than numerical:
# ANSWERABILITY IS DECIDED BY INTENT, NOT BY SIMILARITY SCORE. The router
# excludes never-automate intents outright (FR-08) before retrieval score
# is ever consulted, and generation is required to decline when the
# retrieved passages do not actually address the question.
#
# 0.12 is therefore chosen to keep recall high (97.5%) and let the router
# and the generator make the answerability call, rather than pushing the
# threshold up to buy precision this signal cannot honestly deliver.
