# Commands

`vscode-exts` is the only public command. It deliberately contains just enough
bootstrap code to resolve a checkout or installed symlink and import the
matching provider-private Python library. Manifest parsing, target discovery,
locking, and reconciliation belong under `lib/vscode_exts` so other Python
consumers can reuse them without invoking a subprocess.
