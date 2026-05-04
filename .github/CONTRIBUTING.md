# Welcome to the OpenSage contributing guide

Thank you for investing your time in contributing to OpenSage :sparkles:.

:book: **For comprehensive contribution guidance, please visit our official [documentation](https://docs.adk.opensage-agent.ai/). This is our canonical source for all contribution processes and policies.**

Read our [Code of Conduct](./CODE_OF_CONDUCT.md) to keep our community approachable and respectable.


## What to contribute

We welcome:

- Bug fixes
- Tests
- Documentation improvements
- New examples
- Framework improvements that fit the project direction

If you are planning a larger change, open an issue first so the approach can be discussed before implementation.

## Getting started

1. Fork the repository.
2. Create a feature branch.
3. Install dependencies.
4. Make your changes.
5. Run the relevant checks locally.
6. Open a pull request.

## Local setup

This repository uses Python 3.12+ and `uv`.

```bash
# install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# install project dependencies
uv sync --group dev

# install git hooks
uv run pre-commit install
```

If you are working on the documentation site, also install the docs dependencies:

```bash
uv sync --group docs
```

Docker is required for some sandbox-related workflows and tests.

## Run checks locally

Run formatting and lint checks:

```bash
uv run pre-commit run --all-files
```

Run unit tests:

```bash
uv run pytest tests/unit -v --tb=short -m "not slow"
```

Run integration tests:

```bash
uv run pytest tests/integration -sv --tb=short -m "not slow"
```

Integration tests may require provider credentials such as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

To preview documentation locally:

```bash
# Regenerate the CLI reference pages from `opensage --help` output. Zensical
# has no build-time hook system, so this script must be run manually whenever
# CLI flags change.
uv run python docs/scripts/generate_cli_reference.py

uv run zensical serve
```

## Pull requests

When opening a pull request:

- Keep the scope focused.
- Include tests when changing behavior.
- Update documentation when changing user-facing APIs, workflows, or examples.
- Explain the motivation and the main change clearly in the PR description.

Before requesting review, make sure the relevant local checks pass.

## Questions

If you are unsure where to start, open an issue or start from the documentation listed above.
