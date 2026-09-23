# Security Policy

## Reporting a vulnerability

Please report security issues privately — **do not open a public GitHub issue**.

Use GitHub's [private vulnerability reporting](../../security/advisories/new) for this
repository, or email the maintainers directly. Include:

- A description of the issue and its potential impact
- Steps to reproduce (a minimal example is ideal)
- The affected version / commit

We'll acknowledge your report as soon as we can and keep you updated as we investigate
and fix the issue. Please give us a reasonable amount of time to address the report
before any public disclosure.

## Handling API keys and secrets

This project talks to OpenAI, sec-api.io and earningscall.biz using API keys read from
environment variables (see `.env.example`). A few rules to keep those safe:

- **Never commit** a real `.env`, `.env.*` file, or any file containing an API key.
- If you accidentally commit a secret, treat it as compromised: rotate/revoke it with the
  provider immediately, then remove it from git history.
- Report a leaked secret found in this repository's history the same way as a
  vulnerability (above) so it can be scrubbed and any affected users notified.

## Supported versions

This project is pre-1.0 and moves quickly. Security fixes are made against the latest
release on the `main` branch; older tags are not separately patched.
