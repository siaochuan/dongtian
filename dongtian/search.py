"""Dual-mode search: FTS5 + DeepSeek embedding with hybrid ranking."""

import sqlite3
from typing import Optional

from . import db as dbmod
from .embeddings import (
    cosine_similarity,
    get_client,
    pack_embedding,
    unpack_embedding,
)


def search(
    conn: sqlite3.Connection,
    query: str,
    config: dict,
    wing: Optional[str] = None,
    room: Optional[str] = None,
    mode: str = "hybrid",
    limit: int = 10,
) -> list[dict]:
    embedding_client = get_client(config)

    if mode == "keyword" or (mode == "hybrid" and embedding_client is None):
        return _search_fts(conn, query, wing, room, limit)

    if mode == "embedding":
        if embedding_client is None:
            return _search_fts(conn, query, wing, room, limit)
        return _search_embedding(conn, query, embedding_client, wing, room, limit)

    # hybrid
    fts_results = _search_fts(conn, query, wing, room, limit * 2)
    emb_results = _search_embedding(conn, query, embedding_client, wing, room, limit * 2)
    return _merge_results(fts_results, emb_results, limit)


def _search_fts(
    conn: sqlite3.Connection,
    query: str,
    wing: Optional[str],
    room: Optional[str],
    limit: int,
) -> list[dict]:
    try:
        results = dbmod.search_fts(conn, query, wing, room, limit)
    except Exception:
        return []
    # bm25 returns negative (more negative = better), normalize to 0-1
    if not results:
        return []
    scores = [-r["score"] for r in results]
    mn, mx = min(scores), max(scores)
    rng = mx - mn if mx != mn else 1.0
    for r, s in zip(results, scores):
        r["fts_score"] = (s - mn) / rng
        r["emb_score"] = 0.0
        r["combined_score"] = r["fts_score"]
    return results


def _search_embedding(
    conn: sqlite3.Connection,
    query: str,
    client,
    wing: Optional[str],
    room: Optional[str],
    limit: int,
) -> list[dict]:
    query_vec = client.embed_one(query)
    rows = dbmod.get_drawers_with_embeddings(conn, wing, room)
    if not rows:
        return []
    scored = []
    for r in rows:
        doc_vec = unpack_embedding(r["embedding"])
        sim = cosine_similarity(query_vec, doc_vec)
        entry = dict(r)
        del entry["embedding"]
        entry["emb_score"] = sim
        entry["fts_score"] = 0.0
        entry["combined_score"] = sim
        scored.append(entry)
    scored.sort(key=lambda x: x["emb_score"], reverse=True)
    return scored[:limit]


def _merge_results(fts_results: list[dict], emb_results: list[dict], limit: int) -> list[dict]:
    by_id: dict[int, dict] = {}

    for r in fts_results:
        by_id[r["id"]] = {
            **r,
            "fts_score": r.get("fts_score", 0.0),
            "emb_score": 0.0,
        }

    for r in emb_results:
        rid = r["id"]
        if rid in by_id:
            by_id[rid]["emb_score"] = r.get("emb_score", 0.0)
        else:
            by_id[rid] = {
                **r,
                "fts_score": 0.0,
                "emb_score": r.get("emb_score", 0.0),
            }

    for entry in by_id.values():
        entry["combined_score"] = 0.4 * entry["fts_score"] + 0.6 * entry["emb_score"]

    ranked = sorted(by_id.values(), key=lambda x: x["combined_score"], reverse=True)
    return ranked[:limit]
