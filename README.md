# AIgiSE

## Setup

### Python Environment

We use [uv](https://docs.astral.sh/uv) to manage Python dependencies:
- [Installing uv](https://docs.astral.sh/uv/getting-started/installation/#installing-uv)

```bash
# at the root of the repo
uv python install
uv sync

# install pre-commit hook (required for committing to this repo)
uv run pre-commit install
```

NOTE:
- `uv` installs dependencies into `.venv` which is unknown for your shell by default. Therefore, you should use `uv run <command>` to run all commands using the dependencies. Check [`uv`'s documentation](https://docs.astral.sh/uv/concepts/projects/run) for details.
- Since we use `uv` for dependency management, you should avoid using `pip` to change dependencies. Instead, always use `uv add` or `uv remove`. Check [`uv`'s documentation](https://docs.astral.sh/uv/concepts/projects/dependencies) for details.

## Development Notes

TODO
