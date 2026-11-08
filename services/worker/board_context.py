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
    fields = ("title", "epic", "as_a", "i_want", "so_that", "acceptance_criteria", "solution_tasks", "description")
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


def build_board_context(session: Session, meeting_id: UUID, transcript) -> dict:
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
            missing = []
            vectors = {}
            for index, item in enumerate(candidates):
                row = cache.get((item["source"], item["id"]))
                digest = hashlib.sha256(content_text(item["draft"]).encode()).hexdigest()
                if row and row.embedding and row.embedding_model == EMBEDDING_MODEL and row.content_hash == digest and len(row.embedding) == DIMENSIONS:
                    vectors[index] = row.embedding
                else:
                    missing.append(index)
            generated = embed_texts([query[:6000]] + [content_text(candidates[index]["draft"]) for index in missing])
            for index, vector in zip(missing, generated[1:]):
                item = candidates[index]
                vectors[index] = vector
                cache_vector(session, board.id, item["source"], item["id"], item["draft"], vector)
            session.commit()
            scores = [sum(a * b for a, b in zip(generated[0], vectors[index])) for index in range(len(candidates))]
            method = "embedding"
        except Exception:
            session.rollback()
            # Provider exceptions can contain request data or credentials.
            logger.warning("Board embeddings unavailable; using keyword context")
    selected = sorted(range(len(candidates)), key=lambda index: scores[index], reverse=True)[:MAX_RESULTS]
    return {
        "board_id": str(board.id), "board_name": board.name,
        "epic_names": epics, "retrieval_method": method,
        "candidate_limit_reached": len(stories) > MAX_CANDIDATES or len(external) > MAX_CANDIDATES,
        "external_context_synced": bool(external),
        "existing_stories": [
            {"id": candidates[index]["id"], "source": candidates[index]["source"],
             "status": candidates[index]["status"], "story": content_text(candidates[index]["draft"]),
             "similarity": round(scores[index], 4) if method == "embedding" else None}
            for index in selected
        ],
    }
