# Contributing

Thanks for considering a contribution to OmniAgentOS Starter.

## Getting set up

```bash
git clone https://github.com/omnirogue/OmniAgentOS-Starter.git
cd OmniAgentOS-Starter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Set at least one provider key (`XAI_API_KEY`, `OPENROUTER_API_KEY`, or
`OPENAI_API_KEY`) to run the server against a live model, or skip that and
run `omniagentos demo` — it works with no key at all.

## Before opening a PR

```bash
ruff check .
pytest tests/unit -q
python3 scripts/lint_skills.py
```

All three run in CI (`ubuntu-latest` and `windows-latest`) plus a
`gitleaks` scan — please keep them green locally first.

## Adding a skill pack

New sample packs go under `skills/<category>/<slug>.md` and must follow the
format in `skills/README.md` / the upstream `FORMAT.md`. Run
`python3 scripts/lint_skills.py` before submitting — it enforces the
required sections, a 900-byte floor, unique slugs, and fails on any
internal hostname, IP, email, or local filesystem path.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For security
issues, see `SECURITY.md` instead of opening a public issue.

## Scope

This repository is the open orchestration engine only (see the README's
scope note). PRs that add hosted-platform features (billing, multi-tenant
white-label, managed hosting) are out of scope here — that's
`omnirogue/OmniAgentOS`.

## License

By contributing, you agree your contribution is licensed under this
project's MIT license (see `LICENSE`).
