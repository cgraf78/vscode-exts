# Shared data

The `schemas` directory contains the authoritative JSON Schema for vscode-exts
TOML manifests. Editors, linters, and configuration managers should reference
this provider-owned copy instead of maintaining a consumer-specific duplicate.

The schema describes individual fragments. Runtime aggregation remains the
authority for cross-file invariants such as conflicting profile targets or
extension version pins, which JSON Schema cannot express across several files.
