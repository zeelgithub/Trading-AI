# Security Policy

## Scope

This is a personal, single-operator paper-trading bot (see
[CLAUDE.md](CLAUDE.md)) — there is no shared server, hosted service, or user
database to compromise. Each self-hosted deployment is independent. "Security"
here mostly means: **can this code be tricked into placing an order it
shouldn't, holding a position naked, or leaking a credential it holds**
(Alpaca trading keys, Telegram bot token, Anthropic API key)?

## Reporting a vulnerability

Open a private report via GitHub's "Report a vulnerability" (Security tab) if
available, or a regular issue for anything that isn't itself sensitive — this
is paper-trading research software, not a production system with real users
at risk, so most findings are fine to discuss in the open. If the issue would
only be exploitable with access to someone's already-compromised `.env` file,
note that explicitly — a lot of this bot's "security model" is standard
credential hygiene (secrets never in git, never logged), not defense against
a remote attacker.

## What's already handled

- Secrets live only in `.env` (gitignored); `config/*.yaml` never holds credentials.
- Trading credentials are loaded only by `src/execution/`
  (`load_trading_credentials()`); every other layer that needs read-only
  account access uses `AlpacaAccountReader`, which cannot place, replace, or
  cancel an order even if a caller tries.
- Credential dataclasses (`src/common/secrets.py`) mark every secret field
  `repr=False`, so logging the object itself can't leak a key.
- Telegram command handling checks the sender's chat id against an explicit
  allowlist (`TELEGRAM_ALLOWED_CHAT_IDS`); an empty allowlist denies everyone
  rather than defaulting open.
- `mode: live` (real money) is a deliberate, manually-set flag — never a side
  effect of any other change; the live-mode gate itself is unit-tested.

## Out of scope

- Vulnerabilities in Alpaca's, Telegram's, or Anthropic's own APIs — report
  those to the respective vendor.
- Anything requiring physical or already-root access to the machine running
  the bot.
