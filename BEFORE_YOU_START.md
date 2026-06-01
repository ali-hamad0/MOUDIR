# Before You Start — Modir Setup Guide

> Do everything in this file ONCE before you touch Phase 0.
> This is machine setup, accounts, API keys, and repo initialization.
> Phase 0 assumes all of this is already done.

---

## Step 1 — Accounts to Create

Create these accounts first. Some take hours or days to approve.

| Account | URL | Why |
|---------|-----|-----|
| GitHub | github.com | Your code lives here |
| Google AI Studio | aistudio.google.com | Gemini API key |
| LangSmith | smith.langchain.com | Agent tracing |
| Cloudflare | cloudflare.com | Tunnel for local WhatsApp webhooks during dev |
| Meta Developer | developers.facebook.com | WhatsApp Business API (start this early — approval takes time) |

> **WhatsApp approval takes days.** Start the Meta Developer account NOW
> even if you're months away from needing it. Use a Telegram bot for
> development in the meantime — it's free, instant, and works the same way.

---

## Step 2 — Install Tools on Your Machine

Run these in order. Each command verifies the install worked.

### Git
```bash
# macOS
brew install git

# Windows (run in PowerShell as admin)
winget install --id Git.Git

# Verify
git --version   # should print git version 2.x
```

### Docker Desktop
Download from: https://docs.docker.com/desktop/install

```bash
# Verify
docker --version          # Docker version 27.x
docker compose version    # Docker Compose version v2.x
```

> Make sure Docker Desktop is running before Phase 0.

### Python 3.11
```bash
# macOS
brew install python@3.11

# Windows
winget install Python.Python.3.11

# Verify
python3.11 --version   # Python 3.11.x
```

### uv (Python package manager — replaces pip)
```bash
# macOS / Linux
curl -Ls https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env   # or restart terminal

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify
uv --version   # uv 0.5.x
```

### VS Code
Download from: https://code.visualstudio.com

### VS Code Extensions (install all of these)
Open VS Code, press `Ctrl+Shift+X` (or `Cmd+Shift+X` on Mac), and install:

| Extension | Why |
|-----------|-----|
| `ms-python.python` | Python language support |
| `ms-python.vscode-pylance` | Type checking |
| `ms-azuretools.vscode-docker` | Docker file support |
| `eamodio.gitlens` | Git history in editor |
| `humao.rest-client` | Test API endpoints from `.http` files |
| `charliermarsh.ruff` | Linting (same as CI) |
| `tamasfe.even-better-toml` | pyproject.toml support |

### Claude Code VS Code Extension
In VS Code Extensions, search for **Claude Code** and install it.
Or install the CLI version:
```bash
npm install -g @anthropic-ai/claude-code
```

### spec-kit
```bash
uv tool install specify-cli \
  --from git+https://github.com/github/spec-kit.git@v0.8.11

# Verify
specify --version
```

---

## Step 3 — Get Your API Keys

Collect these before Phase 2. Store them NOWHERE except Vault later.
For now, keep them in a password manager or a secure note — NOT in any file
that touches the repo.

### Gemini API Key
1. Go to https://aistudio.google.com
2. Click "Get API key" → "Create API key"
3. Copy and store securely
4. Note the model names you'll use:
   - Tier 1 (fast/cheap): `gemini-1.5-flash`
   - Tier 2 (powerful): `gemini-1.5-pro`

### LangSmith API Key
1. Go to https://smith.langchain.com
2. Create account → Settings → API Keys → Create
3. Copy and store securely
4. Also note your Project name — you'll set `LANGCHAIN_PROJECT=modir` in `.env`

### Telegram Bot Token (for development)
1. Open Telegram, search for `@BotFather`
2. Send `/newbot` and follow the steps
3. Name it something like `ModirDevBot`
4. Copy the token (looks like `7291234567:AAF_...`)
5. This is your WhatsApp replacement during Phases 2–8

### WhatsApp Business API (start the process, use later)
1. Go to https://developers.facebook.com
2. Create an app → Business → WhatsApp
3. Complete business verification (upload documents — this takes days)
4. Once approved you get a phone number ID and permanent token
5. Do NOT block progress waiting for this — use Telegram until it's approved

---

## Step 4 — Configure Git

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
git config --global init.defaultBranch main

# Generate SSH key for GitHub (if you don't have one)
ssh-keygen -t ed25519 -C "your@email.com"
cat ~/.ssh/id_ed25519.pub   # copy this
# Add to GitHub: Settings → SSH Keys → New SSH Key
```

---

## Step 5 — Create the GitHub Repository

1. Go to github.com → New repository
2. Name: `modir`
3. Visibility: Private (you'll make it public when it's ready)
4. Do NOT initialize with README, .gitignore, or license (Phase 0 handles this)
5. Click Create

```bash
# Clone to your machine
git clone git@github.com:YOUR_USERNAME/modir.git
cd modir
```

---

## Step 6 — Initialize spec-kit

```bash
cd modir
specify init . --integration claude_code
```

This creates:
```
modir/
├── .claude/
│   └── commands/       ← spec-kit slash commands
├── .specify/
│   ├── memory/         ← put constitution.md and ROADMAP.md here
│   └── templates/
└── CLAUDE.md           ← created by spec-kit, we'll fill it next
```

---

## Step 7 — Place Your Project Files

Put the files you already have into the right places:

```bash
# The constitution (the rules)
mkdir -p .specify/memory
cp path/to/constitution.md .specify/memory/constitution.md

# The roadmap (the plan)
cp path/to/ROADMAP.md .specify/memory/ROADMAP.md

# The phase files (one at a time, as you go)
mkdir -p .specify/memory/phases
cp path/to/PHASE_0_foundation_and_setup.md .specify/memory/phases/PHASE_0.md
```

---

## Step 8 — Write CLAUDE.md

This file is loaded automatically every time Claude Code opens in this
project. Keep it short — it points to the longer files.

**Replace the spec-kit generated `CLAUDE.md` with this content:**

```markdown
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
```

---

## Step 9 — Verify Everything Before Phase 0

Run this checklist. Every item must pass before you open Phase 0:

```bash
# Tools
git --version           # ✓ 2.x+
docker --version        # ✓ 27.x+
docker compose version  # ✓ v2.x+
python3.11 --version    # ✓ 3.11.x
uv --version            # ✓ 0.5.x+
specify --version       # ✓ 0.8.x+

# VS Code
code --version          # ✓ 1.9x+

# GitHub
git remote -v           # ✓ shows your github.com/username/modir

# Project structure
ls .specify/memory/     # ✓ constitution.md, ROADMAP.md
cat CLAUDE.md           # ✓ your content, not spec-kit default

# Docker is running
docker ps               # ✓ no error (even if no containers running)
```

---

## Step 10 — How to Use Claude Code in VS Code

This is your daily workflow once setup is complete:

**Starting a session:**
```bash
cd modir
code .                  # open VS Code in the project folder
# Open terminal in VS Code (Ctrl+` or Cmd+`)
claude                  # launch Claude Code
```

**Starting a new phase:**
```
Tell Claude Code:
"Read .specify/memory/constitution.md and
 .specify/memory/phases/PHASE_0.md.
 Implement Task 0.1. Create the branch, create the files,
 then pause for my approval before any commit."
```

**Task by task flow:**
1. Claude Code creates the branch and files for the task
2. You review what it created in VS Code
3. You approve: "Looks good, commit and move to Task 0.2"
4. Or you correct: "The Dockerfile should use python:3.11-slim not python:3.11"
5. Repeat until all tasks in the phase are done

**Key rule:** Never let Claude Code do more than one task without your review.
The constitution says "defend every line" — you can't defend what you didn't read.

---

## You Are Ready When

- [ ] All accounts created (GitHub, Google AI Studio, LangSmith, Meta Developer)
- [ ] All tools installed and versions verified
- [ ] API keys saved securely (NOT in any file)
- [ ] GitHub repo created and cloned
- [ ] spec-kit initialized in the repo
- [ ] `constitution.md` and `ROADMAP.md` in `.specify/memory/`
- [ ] `CLAUDE.md` written with Modir context
- [ ] `PHASE_0.md` in `.specify/memory/phases/`
- [ ] Docker Desktop is running
- [ ] Claude Code opens successfully in VS Code
- [ ] You have read the entire constitution once before starting Phase 0

**When all boxes are checked → open Phase 0 and start Task 0.1.**
