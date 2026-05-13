# Security Policy

## Reporting a vulnerability

**Do not** open a public GitHub issue for security vulnerabilities. They
get indexed by search engines within minutes — by the time we patch,
exploit traffic has already started.

Instead:

1. Email the maintainer directly (open a private issue or reach out via
   the GitHub profile contact link on the repository's main page).
2. Include:
   - The component affected (URL, file path, or function name).
   - Reproduction steps — the smaller the example, the faster the fix.
   - The impact you observed (data leakage, account takeover, RCE,
     denial of service, etc.).
3. Allow 7 days for an initial response and up to 30 days for a fix
   before any public disclosure.

We'll acknowledge receipt within 48 hours and keep you updated as we
investigate.

## Scope

In-scope:
* The `saudi-ad-agent` server (`server.py` + `src/`).
* The web UI (`web/`).
* The audit log, encryption-at-rest, and any handling of customer
  API keys or brand manual content.

Out-of-scope (vulnerabilities you find here should be reported to the
respective vendor, not us):
* Doubao Seedream / Seedance / OpenSpeech APIs themselves (report to
  Volcengine / ByteDance).
* OpenAI / Qwen / Anthropic LLM endpoints.
* Cloud-provider infrastructure (AWS, Azure, etc.).
* Third-party Python libraries (report upstream; we'll bump if it
  affects us).

## What we consider a security issue

* Leaking customer API keys (Doubao Ark, OpenAI, ByteDance OpenSpeech)
  in logs, error messages, audit rows, or HTTP responses.
* Bypassing the input keyword guard or post-storyboard scan.
* Cross-session data leakage (one tenant seeing another tenant's
  brand manual, brief, or generated content).
* Authentication / authorisation flaws (once SSO + RBAC land).
* Server-side request forgery, command injection, or SQL injection.
* Unauthenticated access to `/api/audit` when `SAA_ADMIN_TOKEN` is set.
* Reflected or stored XSS in the Studio UI.

## What we don't consider a security issue (without context)

* Missing security headers (CSP, HSTS, X-Frame-Options) — we know,
  they're on the roadmap.
* SQLite file permissions — we ship for SaaS deployment where the DB
  is on a managed disk; self-hosted setups should harden their own
  filesystem.
* DoS via large brief uploads — there are size caps; if you find a
  cap missing, that's in scope, but raw throughput-based DoS isn't.
