# Modir — Claude Code Context

## What this project is
Modir is a multi-tenant AI SaaS for Lebanese small business owners.
Business owners manage their shop (orders, inventory, finance, customers)
via WhatsApp and a web dashboard. Customers place orders over WhatsApp.
Five LangGraph agents handle different domains. Lebanese Arabic throughout.

## Your rules
Before writing any code, read:
- `.specify/memory/constitution.md` — how ALL code must be written (non-negotiable)
- `.specify/memory/ROADMAP.md` — what we're building and in what order

## Current phase
Before starting work, read the current phase file in `.specify/memory/phases/`.
Work task by task. Pause for approval after each task before committing.

## Stack
- Backend: Python 3.11, FastAPI, SQLAlchemy 2.x async, Alembic, structlog
- AI: LangGraph, Gemini Flash (Tier 1), Gemini Pro (Tier 2)
- Data: Postgres 16 + pgvector, Redis 7, MinIO
- Secrets: HashiCorp Vault dev mode
- Frontend: React 18 + Vite (Phase 3+)
- Package manager: uv (never pip)
- Container: Docker Compose

## Core constraint — The Wall
Every database row has tenant_id. Every repository method filters by tenant_id.
This is enforced in code, never in prompts. No exceptions.

## Language
Business owners speak Lebanese Arabic dialect.
Customers speak Lebanese Arabic.
All user-facing messages must be in Lebanese Arabic.
Code, comments, and variable names are in English.

## When in doubt
Ask before assuming. One wrong architectural decision here compounds across
every future phase. It is better to pause and confirm than to build in the
wrong direction.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
