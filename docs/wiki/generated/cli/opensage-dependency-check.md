# opensage dependency-check

```text
Usage: opensage dependency-check [OPTIONS]

  Check OpenSage external dependencies.

Checks for manually installed dependencies:
  - CodeQL: Required for CodeQL static analysis features
  - Docker: Required for native Docker sandbox backend
  - kubectl: Required for Kubernetes sandbox backend (under development)

  All dependencies are optional unless you plan to use the corresponding
  features.

Options:
  --help  Show this message and exit.
```
