# Secret files — do not read or modify

## Rule

Never read, display, edit, or reference the contents of any `.env` file in this project.
This includes `.env`, `.env.local`, `.env.production`, or any file matching `.env*`.

These files contain live Bitget API keys, the Fernet encryption key, OAuth2 secrets,
and the JWT signing key. Exposing or modifying them could cause irreversible financial
or security damage.

## When the user asks about configuration

Direct them to `.env.example` — it lists every variable with a description and placeholder
value. Never suggest copying or displaying the real `.env`.

## Generate secrets on request

If the user needs a new key value, generate it with a command rather than reading existing ones:

```bash
# New Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# New SECRET_KEY (32-byte hex)
python -c "import secrets; print(secrets.token_hex(32))"
```

## Applies to

- `.env`
- `.env.*` (any variant)
- Any file the user says contains live credentials
