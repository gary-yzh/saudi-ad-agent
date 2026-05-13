# Contributing to saudi-ad-agent

The project is currently proprietary (see [LICENSE](LICENSE)). This guide
exists for **team members and authorised collaborators**. External
contributions (pull requests from the public) aren't accepted at this
stage — open an issue first if you'd like to discuss an idea.

For the small group of people who actually need to push code, here's
the operating manual.

## First-time setup (10 minutes)

```bash
git clone https://github.com/gary-yzh/saudi-ad-agent.git
cd saudi-ad-agent

# Python 3.11+ (check with: python --version)
python -m venv .venv
.venv/Scripts/activate   # Windows
# OR: source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest, pre-commit deps

# One-time: enable the pre-commit hook. This blocks commits that
# break Python AST, leave dup const in JS, or break mermaid blocks
# with unescaped <placeholder>.
git config core.hooksPath .githooks

# Generate a local master key for encrypting API keys at rest
python -c "from cryptography.fernet import Fernet; print('SAA_MASTER_KEY=' + Fernet.generate_key().decode())" >> .env
```

Run the server: `python server.py` — it listens on `127.0.0.1:8000`.

## Running tests

```bash
# Pure-function unit tests (always green, no external deps):
pytest -q tests/

# Full end-to-end (needs OPENAI_API_KEY + ARK_API_KEY + TTS_API_KEY):
OPENAI_API_KEY=sk-... ARK_API_KEY=ark-... TTS_API_KEY=... pytest -q tests/

# CI matches the first form — the e2e test auto-skips without keys.
```

## Commit style

* **Subject line under 72 chars**, lower-case, imperative mood
  (`fix(video): ...`, not `Fixed the video bug`).
* Use a scope prefix when it helps:
  `fix(video)`, `ui:`, `docs:`, `refactor:`, `sprint-1-#3:`.
* Body wraps at 72 chars, explains the **why** more than the **what**
  (the diff already shows the what).
* Co-Author footer is fine (we use `Co-Authored-By: Claude...` when
  Claude wrote the code).

If you're unsure, look at the last 20 commits in `git log` — copy that
style.

## Pre-commit hook (automatic on every `git commit`)

`scripts/precommit_check.py` runs before each commit and rejects on:

| Check | What it catches |
| --- | --- |
| `ast.parse()` on staged `.py` files | Python syntax errors |
| Same-scope duplicate `const`/`let` in staged `.js` | The "Studio buttons all dead" bug class |
| Unescaped `<placeholder>` in mermaid blocks of staged `.md` | "README diagrams don't render" bug |

These are the bugs we've already shipped to GitHub by mistake during
the build — never again. If a check fails, fix the issue and re-commit.

Use `git commit --no-verify` to skip the hook **only** in genuine
emergencies (e.g. demo in 5 minutes).

## Branching

* `main` — what's deployed. Always green.
* `feat/<slug>` — new feature work.
* `fix/<slug>` — bug fixes.
* `chore/<slug>` — refactors, dependency bumps, docs.

Merge to `main` via PR with at least one review. No direct pushes
to `main` once the team is more than one person.

## Code style

* Python: PEP 8, type hints on public APIs (`from __future__ import
  annotations` at the top of every file). No formatter enforced yet
  — match the existing file's style.
* JavaScript: vanilla ES2020+, no framework, no transpiler. Match
  the existing app.js style (`const`, arrow functions, template
  literals, document-level event delegation).
* CSS: one class per concern, comments above non-obvious rules.
* Markdown: 80-char hard wrap in docs; mermaid blocks escape `<...>`.

## Architecture decisions

Significant decisions live in [`docs/adr/`](docs/adr/). Before
introducing a new framework, swapping a vendor, or making any
non-obvious trade-off, write an ADR first. Format is in
`docs/adr/000-template.md`.

## Security

Report vulnerabilities privately per [SECURITY.md](SECURITY.md) —
**never** in a public GitHub issue.

## What not to do

* **Never commit secrets** (`.env`, `*.pem`, API keys, customer
  brand manuals). `.gitignore` covers the common ones but always
  double-check `git status` before `git add`.
* **Never use `git push --force` on `main`**.
* **Never use `git commit --amend` on commits already pushed**.
* **Never disable the pre-commit hook by editing `.githooks/`** —
  if it's flagging false positives, fix `scripts/precommit_check.py`
  in a separate commit.
