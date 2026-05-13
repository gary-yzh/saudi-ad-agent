# Deploy saudi-ad-agent to Fly.io

A free-tier-friendly public deployment in about 10 minutes. You'll end up
with a `https://<your-app>.fly.dev` URL you can share with anyone — face
of the take-home, links from the README, the "Try it live" button.

## Prerequisites

- A free Fly.io account — sign up at https://fly.io
- The `flyctl` CLI installed — https://fly.io/docs/flyctl/install/
- Your three vendor API keys ready (Doubao Ark, OpenAI/Qwen, ByteDance
  OpenSpeech) — you'll paste them into the Settings page **after** the
  app is up. Don't put them in env vars yet.

## One-time setup

### 1. Login

```bash
flyctl auth login
```

Opens your browser, logs you in, drops a token in `~/.fly/config.yml`.

### 2. Pick an app name and launch (without deploying)

```bash
# fly.toml has 'app = saudi-ad-agent' as a default — that name is likely
# taken globally. Edit fly.toml first and change it to something unique
# (e.g. 'saudi-ad-agent-garry' or 'kss-ad-demo-123').

flyctl launch --copy-config --no-deploy
```

When prompted:

- **Region**: accept `fra` (Frankfurt, what fly.toml has). Best balance for
  KSA latency.
- **PostgreSQL / Redis**: say **No** to both. We use SQLite + an on-disk
  audit log; no managed services needed.
- **Deploy now?**: say **No** — we still need to set secrets.

### 3. Create the persistent volume

The Fly Volume holds `/app/data/app.db` (SQLite + audit log) and uploaded
brand-manual PDFs across deploys.

```bash
flyctl volumes create saa_data --region fra --size 1
```

1 GB is plenty for take-home / demo use and within the 3 GB total free-tier
allowance.

### 4. Set the secrets

```bash
# Master key for encrypting customer API keys at rest. Generate a fresh
# random one — DO NOT reuse the development demo key.
flyctl secrets set SAA_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# REQUIRED for public deploy: ADMIN credentials. This account can view
# Settings (API keys), edit config, and read the audit log. Keep this
# password private — only you should know it.
flyctl secrets set SAA_ADMIN_USERNAME=admin
flyctl secrets set SAA_ADMIN_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(24))")

# OPTIONAL but recommended for public demo: DEMO credentials. This
# account can use the Studio (generation features) but CANNOT see
# Settings or audit. Share the demo password with interviewers / demo
# viewers — even if it leaks, your API keys stay private.
flyctl secrets set SAA_DEMO_USERNAME=demo
flyctl secrets set SAA_DEMO_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(16))")

# Print both passwords ONCE so you can save them — you'll need
# admin for /settings, and demo is the one you share publicly:
flyctl ssh console -C 'env | grep SAA_'
```

**Save BOTH passwords**: admin in your private password manager, demo
in a separate note you'll share with viewers. The browser will prompt
the first time you open `/settings` (admin) or click Generate on the
Studio (admin OR demo, either works).

If `SAA_DEMO_PASSWORD` is unset, demo role doesn't exist and only the
admin can use the Studio. Set it later (without redeploying) with
`flyctl secrets set SAA_DEMO_PASSWORD=...`.

Secrets are encrypted at rest in Fly's secrets store, only mounted into
the container as env vars at runtime. They never appear in `fly.toml` or
in deploy logs.

### 5. Deploy

```bash
flyctl deploy
```

First deploy takes ~3 minutes (downloads base image, installs deps, builds
Python layer, pushes to Fly registry, boots a machine). Subsequent deploys
are ~30s because the dep layer caches.

### 6. Open it

```bash
flyctl open
```

Browser opens to `https://<your-app>.fly.dev`. The Settings link will have
a red dot — you haven't configured API keys yet.

### 7. First-time config in the UI

- Go to `/settings`
- Paste the three API keys (LLM, Ark, TTS), click **Save**
- The red dot goes away

You're live. Load the Bateel sample, hit Generate storyboard, follow the
flow.

## Monitoring

```bash
flyctl logs                  # tail server logs in real-time
flyctl status                # machine + check health
flyctl volumes list          # volume usage
flyctl ssh console           # shell into the running machine
flyctl scale memory 1024     # bump to 1 GB if memory pressure shows up
```

## Updating after a code change

```bash
# from the repo root, after committing changes:
flyctl deploy
```

That's it. CI auto-deploy on push to main is Sprint 2 #14 (CI/CD pipeline)
— for now it's manual.

## Cost expectations

Free tier:

- **3 machines** at shared-cpu-1x / 256 MB free (we use one at 512 MB —
  still free)
- **3 GB persistent volume** free (we use 1 GB)
- **160 GB outbound bandwidth** free / month
- **Unlimited inbound**

Realistic monthly cost for take-home / sales-demo level usage: **$0**.

If usage grows past free tier:

- Each additional GB of volume: $0.15 / mo
- shared-cpu-1x 1 GB RAM: $1.94 / mo
- Outbound bandwidth above 160 GB: $0.02 / GB (KSA / EU)

A real customer-facing deployment doing 100 demos / month is still under
$5 / mo until you scale to multiple machines.

## Tearing it down

```bash
flyctl apps destroy <your-app-name>
flyctl volumes destroy <volume-id>
```

Verify with `flyctl apps list`.

## Production-readiness gaps

This deploy is **demo-grade**, not production-grade. Before billing real
customers, address:

1. **PostgreSQL** instead of SQLite — Sprint 2 #9. SQLite + a single Fly
   machine breaks the moment you scale to two machines.
2. **Object storage** instead of `/app/outputs/runs` — Sprint 2 #10.
   Generated videos currently live on the Fly volume; they should live
   in S3 / R2 / OSS with signed URLs.
3. **Job queue** instead of in-session async polling — Sprint 2 #11.
   Right now the user has to keep their browser tab open for the 3-5
   minute Seedance render. Production needs Celery + webhooks.
4. **CI/CD** — Sprint 2 #14. Manual `flyctl deploy` is fine for one
   person, doesn't scale to a team.
5. **APM + metrics** — Sprint 2 #13. The structlog output is there; ship
   it to Grafana Cloud or Datadog.

See `docs/adr/` and the project root README §9 for the full picture.
