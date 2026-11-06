# Changelog

## 2.0.0 - 2026-11-06

- Add `paperless-sync --clean-consume` to identify invalid PDFs already in the consume folder, with a dry-run mode.
- Move malformed PDFs to `CHECK_DIR` with a `.corrupt.pdf` suffix; classify other validation failures with `.invalid.pdf`.
- Have the pre-consume hook leave non-PDF inputs unchanged for Paperless to handle.
- Document AppleDouble sidecar handling, consume cleanup, reconciliation, and pre-consume behavior.
