# Meeting-to-Backlog

> Your meeting ends, and your backlog is already drafted, checked, and linked back to the exact moment it was discussed.

A Chrome extension records a browser meeting beside a Trello board. Gemini turns it into evidence-linked user stories, checked against INVEST. A human reviews them, then approves them into Trello. A web app tracks meeting history and backlog quality.

**Status:** hackathon concept. This repo is design and file layout only, with no code yet.

Full design: [docs/DESIGN.md](docs/DESIGN.md)
AI usage disclosure (hackathon requirement): [docs/AI_USAGE.md](docs/AI_USAGE.md)

## Layout

```
apps/extension         Chrome extension (WXT + React + TS)
apps/web               Companion web app (React + shadcn/ui)
services/api           FastAPI backend
services/worker        Gemini processing pipeline
packages/shared-types  Shared schemas
adapters/trello        Trello adapter (MVP)
adapters/github        GitHub Issues adapter (stretch)
infra/docker           Docker Compose
tests/                 Tests and sample meeting fixtures
docs/                  Design doc
```

## Responsible use

Only record meetings where everyone knows and agrees. The extension never records without an explicit click.

## License

MIT, see [LICENSE](LICENSE).
