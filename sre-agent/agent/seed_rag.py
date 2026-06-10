"""Seed the Qdrant 'runbooks' collection with embedded runbook markdown files.

Each runbook is short (one symptom / diagnosis / remediation paragraph trio),
so we embed it as a single chunk rather than splitting. The file slug
(e.g. ``001-shell-in-container``) becomes the point's payload tag for filtering.

Usage:
    uv run python -m agent.seed_rag
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from agent.config import settings
from agent.logging_utils import configure_logging

log = logging.getLogger("seed_rag")

RUNBOOKS_DIR = Path(__file__).resolve().parent / "runbooks"
VECTOR_SIZE = 768  # nomic-embed-text


def _embed(client: httpx.Client, text: str) -> list[float]:
    resp = client.post(
        f"{settings.ollama_host}/api/embeddings",
        json={"model": settings.embed_model, "prompt": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _ensure_collection(qdrant: QdrantClient) -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if settings.qdrant_collection in existing:
        log.info("collection %r already exists; recreating to refresh", settings.qdrant_collection)
        qdrant.delete_collection(settings.qdrant_collection)
    qdrant.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=qmodels.VectorParams(size=VECTOR_SIZE, distance=qmodels.Distance.COSINE),
    )


def seed_one(slug: str, text: str, tags: dict[str, str] | None = None) -> None:
    """Embed and upsert a single runbook into the collection without re-seeding
    the rest. Used by the use-case runner to inject case-specific runbooks
    just-in-time; tagged so a future cleanup can remove them selectively."""
    qdrant = QdrantClient(url=settings.qdrant_url)
    with httpx.Client() as http:
        vector = _embed(http, text)
    payload = {"slug": slug, "text": text}
    if tags:
        payload.update(tags)
    point = qmodels.PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, slug)),
        vector=vector,
        payload=payload,
    )
    qdrant.upsert(collection_name=settings.qdrant_collection, points=[point], wait=True)
    log.info("seeded one runbook %r (tags=%s)", slug, tags or {})


def _load_runbooks() -> list[tuple[str, str, str]]:
    """Return (slug, body, domain) for every runbook.

    Top-level `runbooks/*.md` are the SRE domain (back-compat). Runbooks
    under `runbooks/security/*.md` are tagged `domain=security` so the
    Phase-21 security agent's RAG retrieval can filter to them. The slug
    is prefixed with `security/` for security runbooks to avoid id
    collisions and to make the domain visible in the SPA's RAG-hit list.
    """
    out: list[tuple[str, str, str]] = []
    for f in sorted(RUNBOOKS_DIR.glob("*.md")):
        out.append((f.stem, f.read_text(encoding="utf-8"), "sre"))
    sec_dir = RUNBOOKS_DIR / "security"
    if sec_dir.is_dir():
        for f in sorted(sec_dir.glob("*.md")):
            out.append((f"security/{f.stem}", f.read_text(encoding="utf-8"), "security"))
    if not out:
        log.error("no runbooks found in %s", RUNBOOKS_DIR)
        sys.exit(2)
    return out


def main() -> int:
    configure_logging()
    log.info("connecting to qdrant=%s ollama=%s", settings.qdrant_url, settings.ollama_host)
    qdrant = QdrantClient(url=settings.qdrant_url)
    _ensure_collection(qdrant)

    runbooks = _load_runbooks()
    points: list[qmodels.PointStruct] = []
    with httpx.Client() as http:
        for slug, body, domain in runbooks:
            log.info("embedding %s [%s] (%d chars)", slug, domain, len(body))
            vector = _embed(http, body)
            points.append(
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, slug)),
                    vector=vector,
                    payload={"slug": slug, "text": body, "domain": domain},
                )
            )

    qdrant.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)
    log.info("upserted %d runbooks into %r", len(points), settings.qdrant_collection)

    count = qdrant.count(collection_name=settings.qdrant_collection, exact=True).count
    log.info("collection now holds %d points", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
