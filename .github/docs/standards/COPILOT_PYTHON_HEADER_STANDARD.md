# PYTHON_PROJECT_CODING_STANDARD.md
# Project-Wide Python Coding Standard
# Applies to all Python scripts, modules, libraries, and automation

This document defines the authoritative Python coding standard for this
repository. All contributors — human or AI — must follow these rules to ensure
the codebase remains deterministic, maintainable, audit-ready, and consistent.

The standard applies to:
- CLI tools
- Libraries and modules
- Automation scripts
- Ingestion pipelines
- Background workers
- Internal utilities

The goal is to produce code that future maintainers can trust and understand
without tribal knowledge.

# ─────────────────────────────────────────────────────────────
# 1. File Header Requirements
# ─────────────────────────────────────────────────────────────
Every Python file must begin with a top-level module docstring containing:

1. Functional description of the script/module
2. Key features or responsibilities
3. Usage examples (CLI or import usage)
4. Inputs (CLI args, environment variables, files, APIs)
5. Outputs (files, logs, exit codes, return values)
6. Dependencies (Python packages, external binaries, services)
7. Operational assumptions or constraints

Prohibited in headers:
- Author names
- Creation dates
- Last-modified dates
- Version numbers
- Copyright
- Decorative ASCII art

All metadata belongs in Git.

# ─────────────────────────────────────────────────────────────
# 2. Required File Structure
# ─────────────────────────────────────────────────────────────
All Python files must follow this structure in order:

# ─────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────
- Standard library imports first
- Third-party imports second
- Local imports last
- No unused imports
- Prefer explicit imports over wildcard imports

# ─────────────────────────────────────────────────────────────
# Configuration / Constants
# ─────────────────────────────────────────────────────────────
- Centralize all configuration values
- Use UPPERCASE for constants
- Use environment variables for secrets or external configuration
- Prefer pathlib.Path for filesystem paths

# ─────────────────────────────────────────────────────────────
# Functions / Classes
# ─────────────────────────────────────────────────────────────
- Prefer pure functions where possible
- Each function must include a docstring describing:
    * Purpose
    * Inputs
    * Outputs
    * Side effects
    * Exceptions raised
- Classes only when stateful behavior is required
- Use dataclasses for structured data when appropriate
- Avoid deeply nested logic; refactor into smaller units

# ─────────────────────────────────────────────────────────────
# Main Entrypoint
# ─────────────────────────────────────────────────────────────
- Guard with: if __name__ == "__main__":
- Parse CLI arguments here (if applicable)
- Call orchestration functions, not business logic directly

# ─────────────────────────────────────────────────────────────
# 3. Code Style Requirements
# ─────────────────────────────────────────────────────────────
- Follow PEP 8 and PEP 257
- Use type hints everywhere
- Use f-strings for formatting
- Use descriptive variable names
- Avoid magic numbers; define constants
- Prefer explicit over implicit behavior
- Avoid side effects in module-level code
- Use context managers for file and resource handling
- Use pathlib instead of os.path
- Use enumerate() instead of range(len())
- Use list/dict comprehensions when clear

# ─────────────────────────────────────────────────────────────
# 4. Logging Standards
# ─────────────────────────────────────────────────────────────
- Use the logging module, not print()
- Use structured, meaningful log messages
- Include context in logs (file paths, identifiers, counts)
- Log at appropriate levels:
    * DEBUG for internal details
    * INFO for normal operations
    * WARNING for recoverable issues
    * ERROR for failures
    * CRITICAL for unrecoverable failures
- Never log secrets or sensitive data

# ─────────────────────────────────────────────────────────────
# 5. Error Handling Standards
# ─────────────────────────────────────────────────────────────
- Use explicit exception handling
- Never swallow exceptions silently
- Provide actionable error messages
- Prefer custom exception classes for domain-specific errors
- Avoid broad except: clauses
- Use try/except around external I/O, not internal logic
- Fail predictably and safely

# ─────────────────────────────────────────────────────────────
# 6. Security Requirements
# ─────────────────────────────────────────────────────────────
- Never hardcode secrets
- Validate all external inputs
- Sanitize file paths and user-provided data
- Avoid insecure defaults
- Use least-privilege access patterns
- Prefer environment variables or secret stores for credentials

# ─────────────────────────────────────────────────────────────
# 7. CLI Tool Standards
# ─────────────────────────────────────────────────────────────
- Use argparse or click
- Provide clear help text
- Document exit codes in the module docstring
- Keep CLI parsing separate from business logic
- Provide examples in the header docstring

# ─────────────────────────────────────────────────────────────
# 8. Pipeline & Automation Standards
# ─────────────────────────────────────────────────────────────
- Ensure deterministic behavior
- Avoid nondeterministic ordering unless documented
- Provide clear error classification
- Prefer quarantine or archival over deletion
- Log all classification decisions
- Avoid ingestion loops by removing or relocating failed files
- Use layered repair strategies when applicable (e.g., qpdf → Ghostscript)

# ─────────────────────────────────────────────────────────────
# 9. Testing Standards
# ─────────────────────────────────────────────────────────────
- Use pytest
- Tests must be deterministic
- Prefer fixtures over setup/teardown
- Mock external services and file I/O
- Include tests for:
    * edge cases
    * error handling
    * pipeline stages
    * configuration parsing
    * CLI behavior

# ─────────────────────────────────────────────────────────────
# 10. Documentation Standards
# ─────────────────────────────────────────────────────────────
- Every module must have a top-level docstring
- Every public function/class must have a docstring
- Inline comments only where logic is non-obvious
- README files must include:
    * Purpose
    * Usage
    * Inputs/outputs
    * Dependencies
    * Examples
- Provide architecture diagrams when helpful

# ─────────────────────────────────────────────────────────────
# 11. Performance & Reliability
# ─────────────────────────────────────────────────────────────
- Avoid premature optimization
- Prefer readability over micro-optimizations
- Use caching only when necessary and documented
- Ensure long-running scripts handle interruptions gracefully
- Avoid unbounded memory growth
- Use timeouts for external calls

# ─────────────────────────────────────────────────────────────
# 12. File & Directory Conventions
# ─────────────────────────────────────────────────────────────
- Use snake_case for filenames
- Use lowercase package names
- Use __init__.py to define package boundaries
- Keep modules focused and small
- Avoid monolithic files

# End of Python Project Coding Standard
