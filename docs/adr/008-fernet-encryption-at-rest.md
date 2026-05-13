# ADR 008: Fernet symmetric encryption for API keys at rest

## Status

`Accepted` (2026-05-13)

## Context

The customer's three vendor API keys (Doubao Ark, OpenAI/Qwen,
OpenSpeech) live in `data/app.db`. Before this change, they were
plaintext: anyone who got read access to the SQLite file walked away
with all three keys, which they could then use to burn the customer's
quota or impersonate the customer to Doubao.

For PDPL and any future SOC 2 audit, "encryption at rest" for
credentials is table-stakes.

## Decision

Use **Fernet** (AES-128-CBC + HMAC-SHA256, from the `cryptography`
library — audited, standard, simple). A master key is read from the
environment variable `SAA_MASTER_KEY`. Sensitive config rows (currently
the three `*_api_key` columns) are encrypted before write and decrypted
on read.

Encrypted values carry an `enc:` prefix so the codepath can:

* Tell encrypted ciphertext apart from legacy plaintext.
* Migrate legacy rows automatically on next save (load → encrypt →
  store).

If `SAA_MASTER_KEY` is unset, we fall back to a **deterministic** demo
key derived from a fixed string. This is explicitly NOT secure — it's
there so the take-home keeps running without env setup. Production
deployment must set a real `SAA_MASTER_KEY` (generate with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).

## Consequences

* **Gain:**
  * Customer keys are ciphertext on disk. A stolen `app.db` requires
    the master key too — defense in depth.
  * Audit log can safely log "config.update on openai_api_key" without
    risking the key value (`_audit_redact()` replaces sensitive values
    with `<redacted len=N>`).
  * Path to vault integration is short — swap `_master_key()` to read
    from AWS Secrets Manager / HashiCorp Vault.
* **Cost:**
  * If `SAA_MASTER_KEY` is lost or rotated incorrectly, encrypted rows
    become unrecoverable. The customer has to re-enter their API keys
    via Settings.
  * The fallback demo key is dangerous if accidentally used in
    production. Mitigated by a comment screaming "NOT FOR PRODUCTION"
    in the source and (TODO) a startup warning in the server logs
    when no real key is set.
* **Alternatives considered:**
  * Plaintext + filesystem permissions only — doesn't survive a backup
    leak or a misconfigured S3 bucket holding the DB.
  * Vendor envelope encryption (KMS, Cloud HSM) — more secure, but ops
    cost too high at this stage. Fernet now, KMS when we have a
    production deployment to attach it to.
  * Asymmetric encryption (RSA / age) — overkill for symmetric
    sensitive-config storage.
