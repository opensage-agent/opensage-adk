---
icon: lucide/git-pull-request-arrow
---

# Contributing Guidelines

## Code Style

- Follow Google Python Style Guide
- Use `pyink` for formatting
- Use `isort` for import sorting
- Run `autoformat.sh` before committing

## Commit Messages

Follow Conventional Commits format:

```
feat(component): Description
fix(component): Description
refactor(component): Description
```

## Pull Request Process

1. Create feature branch
2. Make changes with tests
3. Run tests and linting
4. Update documentation
5. Submit PR with description

## Third-Party Dependencies

External benchmarks and tools live under `third_party/` and are vendored via `git subtree`. To add a new one:

```bash
git subtree add --prefix third_party/cybergym \
    https://github.com/sunblaze-ucb/cybergym.git main --squash
```

Swap the prefix, URL, and branch for the dependency you are adding. The `--squash` flag keeps the upstream history out of the main repo log.
