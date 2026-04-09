# .github/copilot-instructions.md
# Repository-Wide Copilot Ruleset
# Applies to all Copilot completions, chat, and inline suggestions

This file defines how GitHub Copilot should behave when generating code,
documentation, tests, or automation inside this repository. These rules apply to
all languages, with Python-specific rules included where relevant.

The goal: produce deterministic, maintainable, audit-ready code that future
maintainers can trust.

# ─────────────────────────────────────────────────────────────
# 1. General Repository-Wide Rules
# ─────────────────────────────────────────────────────────────
- Always generate code that is modular, explicit, and maintainable.
- Prefer clarity over cleverness.
- Avoid hidden side effects.
- Never include author names, creation dates, modification dates, or version
  numbers in file headers. Git tracks all metadata.
- Never generate decorative ASCII art unless explicitly requested.
- Use consistent naming conventions across the repo.
- Use type hints in languages that support them (Python, TypeScript).
- Use structured logging instead of print statements.
- Validate all external inputs.
- Never hardcode secrets or credentials.
- Prefer configuration via environment variables or config files.
- Avoid silent failures; classify and log all errors.

# ─────────────────────────────────────────────────────────────
# 2. Documentation Expectations
# ─────────────────────────────────────────────────────────────
- All scripts, modules, and tools must include a top-level description of their
  purpose and operational assumptions.
- Provide docstrings for all public functions, classes, and modules.
- Inline comments only where logic is non-obvious.
- Avoid redundant comments that restate the code.
- README files must include:
    * Purpose
    * Usage
    * Inputs/outputs
    * Dependencies
    * Examples

# ─────────────────────────────────────────────────────────────
# 3. Commit Message Style
# ─────────────────────────────────────────────────────────────
Use a clear, structured commit style:

<type>: <short summary>

Where <type> is one of:
- feat
- fix
- docs
- refactor
- test
- chore
- ci

Examples:
- feat: add PDF repair fallback using Ghostscript
- fix: correct quarantine path resolution
- refactor: extract ingestion pipeline into modular functions

# ─────────────────────────────────────────────────────────────
# 4. Security Expectations
# ─────────────────────────────────────────────────────────────
- Never commit secrets, tokens, passwords, or private keys.
- Use environment variables or secret stores.
- Validate all file paths, user inputs, and external data.
- Fail safely and predictably.
- Avoid insecure defaults.
- Prefer least-privilege access patterns.

# ─────────────────────────────────────────────────────────────
# 5. Python-Specific Rules
# ─────────────────────────────────────────────────────────────
Copilot must follow these rules for all Python code:

## 5.1 Required Module Header
Every Python file must begin with a top-level module docstring containing:

1. Functional description
2. Key features
3. Usage examples
4. Inputs (CLI args, env vars, files)
5. Outputs (files, logs, exit codes)
6. Dependencies
7. Operational assumptions

Never include:
- Author
- Creation date
- Last modified date
- Version number

## 5.2 Required File Structure
All Python files must follow this structure:

# ─────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Configuration / Constants
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Functions / Classes
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Main Entrypoint
# ─────────────────────────────────────────────────────────────

## 5.3 Code Style
- Follow PEP 8 and PEP 257.
- Use type hints everywhere.
- Use f-strings for formatting.
- Avoid deeply nested logic; refactor into smaller functions.
- Avoid magic numbers; define constants.
- Use pathlib instead of os.path where possible.
- Use logging instead of print().

## 5.4 Error Handling
- Use explicit exception handling.
- Provide actionable error messages.
- Never swallow exceptions silently.
- Prefer custom exception classes for pipeline or ingestion errors.

# ─────────────────────────────────────────────────────────────
# 6. CLI Tools
# ─────────────────────────────────────────────────────────────
- Use argparse or click.
- Provide clear help text.
- Document exit codes in the module docstring.
- Keep CLI parsing separate from business logic.

# ─────────────────────────────────────────────────────────────
# 7. Automation & Pipelines
# ─────────────────────────────────────────────────────────────
- Ensure deterministic behavior.
- Avoid nondeterministic ordering unless documented.
- Provide clear error classification.
- Prefer quarantine or archival over deletion.
- Log all classification decisions.

# ─────────────────────────────────────────────────────────────
# 8. Testing Expectations
# ─────────────────────────────────────────────────────────────
- Use pytest.
- Tests must be deterministic.
- Prefer fixtures over setup/teardown.
- Mock external services and file I/O.
- Include tests for:
    * edge cases
    * error handling
    * pipeline stages
    * configuration parsing

# End of Copilot instructions
