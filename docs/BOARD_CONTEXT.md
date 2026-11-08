# Board context for story generation

When processing a saved meeting, Backlogger retrieves existing work from that
meeting's board after transcription. Both extraction and critique receive the
same context: board name, known epic names, existing story references, review
status, and relevant story text. A standalone `run_pipeline("audio.wav")` still
works without opening the database.

The extension refreshes external work before starting processing. Other API
clients should call this endpoint first, sending their connected account cookie:

```http
POST /api/boards/{board_id}/context/sync
```

`board_id` is Backlogger's UUID, not the provider's ID. The response contains
`board_id` and `synced_items`. Sync reads from the provider; it never creates or
updates external cards/issues. It replaces that board's previous external
snapshot and removes records no longer returned by the provider. Failed reads
preserve the previous snapshot. The extension stops before processing if sync
fails, so reconnect or retry rather than silently using stale external work.
Direct API processing without sync uses the last saved external snapshot and
current local stories. There is no periodic sync.

- Trello context includes cards (including archived cards), descriptions,
  Acceptance Criteria and Solution Tasks checklists. Epic names come from
  `Epic: Name` labels or an `Epic: Name` description line.
- Jira Cloud context includes project issues, descriptions, statuses, epic
  names, and the standard parent relationship. Search follows page tokens;
  legacy custom Epic Link fields are not mapped. A registered Jira board
  represents a project, so context covers the project rather than a custom
  agile-board filter.
- Local context reads the current story draft, including its epic, criteria,
  and proposed solution tasks. Rejected stories and stories from the current
  meeting are excluded. Stories from other boards are excluded.

## Embeddings and related work

`STORY_EMBEDDINGS_ENABLED=true` (the default) uses Gemini's
`gemini-embedding-001`, with `SEMANTIC_SIMILARITY` and 768 dimensions. Vectors
are normalized and compared with cosine similarity. The existing `google-genai`
dependency supplies the embedding API; there is no new Python dependency.
See Google's [embedding documentation](https://ai.google.dev/gemini-api/docs/embeddings).

New generated stories are indexed after their database transaction commits.
Existing local stories and synced external items are indexed on demand when
processing the next meeting. The `story_context` table caches one vector per
board/source/story identity, with its model and a hash of the embedded text.
Editing a local draft causes its vector to be regenerated on the next retrieval.
Changing a synced external item clears its cached vector. Status and score
changes alone do not require re-embedding. Embedding failures fall back to
keyword retrieval and never discard saved drafts. Set the environment setting
to `false` to use keyword context without embedding requests.

Gemini can return `draft.related_story_ids`, for example `local:<story UUID>`,
`trello:<card ID>`, or `jira:APP-42`. References absent from the supplied context
are removed before saving. The extension shows related references in the
critique. Prompts ask Gemini to reuse fitting epic names and flag potential
overlap for clarification. Similarity is not proof of duplication; drafts are
never automatically merged, rejected, or deleted on that basis. Every new
requirement and evidence quote must still come from the current meeting.

## Limits and operation

Apply `infra/db/004_story_context.sql` after migrations 001-003. It adds a
JSONB-based cache table and preserves existing application data.

This MVP ranks up to 500 recent local stories and 500 external items, sends the
20 most relevant to Gemini, and includes up to 100 known epic names. Text is
bounded, and very long transcripts are sampled at the beginning, middle, and
end for retrieval. External sync rejects snapshots over 500 items or incomplete
pagination. Retrieval reports its method and whether candidate limits were
reached in the model context. An absent match is not a guarantee of uniqueness.

Vectors are stored in JSONB and ranked in Python; pgvector and a database vector
index are not required. For larger boards, add database vector search and full
pagination before raising these limits. The first run can make multiple paid
embedding requests; subsequent runs reuse unchanged story vectors. Board text
and transcript retrieval text are sent to Gemini as well as the audio.

Workspace records remain shared, as in the existing application. Board filtering
does not introduce user authentication or tenant isolation.
