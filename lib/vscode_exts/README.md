# vscode_exts library

`reconciler.py` owns the reusable implementation behind the public command. Its
functions separate manifest loading, target discovery, installed-state
inspection, and additive installation so callers can reuse the pieces without
reimplementing platform policy.

The module is provider-private rather than a separately versioned PyPI package.
The public installer symlinks the CLI back to this checkout, which guarantees
the launcher and library update together and avoids a second package lifecycle
for a single-command tool.

`VSCODE_EXTS_TEST_*` variables are internal fixture seams used to simulate WSL
process boundaries. The supported runtime controls are documented in the root
README and intentionally limited to `VSCODE_EXTS_WINDOWS_HOME` and
`VSCODE_EXTS_TIMEOUT_SECONDS`.
