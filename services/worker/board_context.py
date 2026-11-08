"""Retrieve existing work from the same board, with an optional embedding cache.

For the MVP, vectors live in JSONB and cosine search runs in Python. This keeps
the database extension-free; move retrieval to pgvector when boards grow large.
"""

import hashlib
import json
import logging
import math
import os
import re
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from services.models import Board, Meeting, Story, StoryContext

logger = logging.getLogger(__name__)
EMBEDDING_MODEL = "gemini-embedding-001"
DIMENSIONS = 768
MAX_CANDIDATES = 500
MAX_RESULTS = 20


def embeddings_enabled() -> bool:
    return os.getenv("STORY_EMBEDDINGS_ENABLED", "true").lower() == "true"


def content_text(payload: dict) -> str:
    """Keep the useful story fields; omit timestamps, scores, and source quotes."""
    fields = ("title", "origin", "assumptions", "epic", "as_a", "i_want", "so_that", "acceptance_criteria", "solution_tasks", "description")
    return json.dumps({key: payload[key] for key in fields if payload.get(key)}, ensure_ascii=False)[:6000]


def embed_texts(texts: list[str]) -> list[list[float]]:
    from google import genai
    from google.genai import types

    client = genai.Client()
    vectors = []
    for start in range(0, len(texts), 32):
        response = client.models.embed_content(
            model=EMBEDDING_MODEL, contents=texts[start:start + 32],
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY", output_dimensionality=DIMENSIONS),
        )
        batch = [item.values for item in response.embeddings or []]
        if len(batch) != len(texts[start:start + 32]):
            raise ValueError("Embedding response count does not match the request")
        for vector in batch:
            if vector is None or len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector):
                raise ValueError("Invalid embedding vector")
            length = math.sqrt(sum(x * x for x in vector))
            if length == 0:
                raise ValueError("Empty embedding vector")
            vectors.append([x / length for x in vector])
    return vectors


def cache_vector(session, board_id, source, external_id, payload, vector):
    """An upsert tolerates overlapping jobs without duplicate cache rows."""
    statement = insert(StoryContext).values(
        board_id=board_id, source=source, external_id=external_id,
        payload=payload, embedding=vector, embedding_model=EMBEDDING_MODEL,
        content_hash=hashlib.sha256(content_text(payload).encode()).hexdigest(),
    )
    session.exec(statement.on_conflict_do_update(
        index_elements=["board_id", "source", "external_id"],
        set_={key: getattr(statement.excluded, key) for key in (
            "payload", "embedding", "embedding_model", "content_hash",
        )},
    ))


def index_stories(session: Session, board_id: UUID, stories: list[Story]) -> None:
    """Best-effort indexing after story creation; failure must not lose drafts."""
    if not embeddings_enabled() or not stories:
        return
    vectors = embed_texts([content_text(story.draft) for story in stories])
    for story, vector in zip(stories, vectors):
        cache_vector(session, board_id, "local", str(story.id), story.draft, vector)
    session.commit()


def load_board_candidates(session: Session, meeting_id: UUID):
    """Read current board-scoped text and its cache for either retrieval pass."""
    meeting = session.get(Meeting, meeting_id)
    board = session.get(Board, meeting.board_id)
    # Read current drafts, rather than trusting a potentially stale cache payload.
    stories = session.exec(
        select(Story).join(Meeting, Story.meeting_id == Meeting.id)
        .where(Meeting.board_id == board.id, Meeting.id != meeting_id, Story.status != "rejected")
        .order_by(Story.updated_at.desc(), Story.id).limit(MAX_CANDIDATES + 1)
    ).all()
    cached = session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).all()
    cache = {(row.source, row.external_id): row for row in cached}
    candidates = [
        {"source": "local", "id": str(story.id), "status": story.status, "draft": story.draft}
        for story in stories[:MAX_CANDIDATES]
    ]
    external = sorted((row for row in cached if row.source != "local"), key=lambda row: row.external_id)
    candidates.extend({"source": row.source, "id": row.external_id, "status": row.payload.get("status"), "draft": row.payload}
                      for row in external[:MAX_CANDIDATES])
    limited = len(stories) > MAX_CANDIDATES or len(external) > MAX_CANDIDATES
    return board, candidates, cache, limited


def embed_with_candidates(session, board, candidates, cache, queries):
    """Embed queries and stale candidates together; reuse unchanged board vectors."""
    missing, vectors = [], {}
    for index, item in enumerate(candidates):
        row = cache.get((item["source"], item["id"]))
        digest = hashlib.sha256(content_text(item["draft"]).encode()).hexdigest()
        if row and row.embedding and row.embedding_model == EMBEDDING_MODEL and row.content_hash == digest and len(row.embedding) == DIMENSIONS:
            vectors[index] = row.embedding
        else:
            missing.append(index)
    generated = embed_texts(queries + [content_text(candidates[index]["draft"]) for index in missing])
    for index, vector in zip(missing, generated[len(queries):]):
        item = candidates[index]
        vectors[index] = vector
        cache_vector(session, board.id, item["source"], item["id"], item["draft"], vector)
    if session is not None:
        session.commit()
    return generated[:len(queries)], [vectors[index] for index in range(len(candidates))]


def similarity(left, right):
    # embed_texts normalizes vectors, so the dot product is cosine similarity.
    return sum(a * b for a, b in zip(left, right))


def build_board_context(session: Session, meeting_id: UUID, transcript) -> dict:
    board, candidates, cache, limited = load_board_candidates(session, meeting_id)
    epics = sorted({item["draft"]["epic"][:200] for item in candidates if item["draft"].get("epic")})[:100]
    query = "\n".join(segment.text for segment in transcript.segments)
    # Sample across long meetings so the end of a meeting is still represented.
    if len(query) > 6000:
        query = query[:2000] + query[len(query)//2:len(query)//2 + 2000] + query[-2000:]
    terms = set(re.findall(r"\w+", query.lower()))
    scores = [len(terms & set(re.findall(r"\w+", content_text(item["draft"]).lower()))) for item in candidates]
    method = "keyword"
    if candidates and embeddings_enabled():
        try:
            queries, vectors = embed_with_candidates(session, board, candidates, cache, [query[:6000]])
            scores = [similarity(queries[0], vector) for vector in vectors]
            method = "embedding"
        except Exception:
            session.rollback()
            # Provider exceptions can contain request data or credentials.
            logger.warning("Board embeddings unavailable; using keyword context")
    selected = sorted(range(len(candidates)), key=lambda index: scores[index], reverse=True)[:MAX_RESULTS]
    return {
        "board_id": str(board.id), "board_name": board.name,
        "epic_names": epics, "retrieval_method": method,
        "candidate_limit_reached": limited,
        "external_context_synced": any(item["source"] != "local" for item in candidates),
        "existing_stories": [
            {"id": candidates[index]["id"], "source": candidates[index]["source"],
             "status": candidates[index]["status"], "story": content_text(candidates[index]["draft"]),
             "similarity": round(scores[index], 4) if method == "embedding" else None}
            for index in selected
        ],
    }


def build_review_context(stories, *, session=None, meeting_id=None) -> dict:
    """Find each draft's closest new drafts and existing board stories before review.

    Scores rank candidates for semantic comparison; they never decide an INVEST
    failure. The reviewer must distinguish containment, overlap, and shared topic.
    """
    if not stories or not embeddings_enabled():
        return {}
    try:
        board, candidates, cache, limited = (
            load_board_candidates(session, meeting_id) if session is not None
            else (None, [], {}, False)
        )
        if len(stories) == 1 and not candidates:
            return {}
        draft_vectors, board_vectors = embed_with_candidates(
            session, board, candidates, cache,
            [content_text(story.model_dump()) for story in stories],
        )
        pairs, matches = {}, []
        for index, vector in enumerate(draft_vectors):
            neighbors = sorted(
                ((other, similarity(vector, other_vector)) for other, other_vector in enumerate(draft_vectors) if other != index),
                key=lambda item: item[1], reverse=True,
            )[:3]
            for other, score in neighbors:
                left, right = sorted((index, other))
                pairs[left, right] = {"left_index": left, "right_index": right, "similarity": round(score, 4)}
            nearest_board = sorted(
                enumerate(board_vectors), key=lambda item: similarity(vector, item[1]), reverse=True,
            )[:3]
            for other, other_vector in nearest_board:
                candidate = candidates[other]
                matches.append({
                    "draft_index": index,
                    "reference": f"{candidate['source']}:{candidate['id']}",
                    "similarity": round(similarity(vector, other_vector), 4),
                    "status": candidate["status"], "story": content_text(candidate["draft"]),
                })
        return {
            "retrieval_method": "embedding", "candidate_limit_reached": limited,
            "draft_pairs": list(pairs.values()), "board_matches": matches,
        }
    except Exception:
        if session is not None:
            session.rollback()
        logger.warning("Per-story embeddings unavailable; review will compare supplied story text")
        return {"retrieval_method": "unavailable", "draft_pairs": [], "board_matches": []}
