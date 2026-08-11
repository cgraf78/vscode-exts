# vscode-exts

![Tests](https://github.com/cgraf78/vscode-exts/actions/workflows/test.yml/badge.svg?branch=main)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue.svg)](https://www.python.org/)

`vscode-exts` reconciles declarative extension manifests across local VS Code,
VS Code Insiders, remote VS Code Server installations, and native Windows VS
Code reached from WSL. Reconciliation is intentionally additive: declared
extensions are installed when missing, pinned versions are corrected, and
extensions installed outside the manifest remain untouched.

```console
vscode-exts
vscode-exts --manifest base.toml --manifest laptop.toml
```

## Installation

For the simplest checkout-backed install:

```bash
curl -fsSL https://raw.githubusercontent.com/cgraf78/vscode-exts/main/install.sh | bash
```

This keeps a durable managed checkout under `$XDG_DATA_HOME` when that path is
absolute, or under `$HOME/.local/share` otherwise, so the launcher, private
Python library, and schema remain version-coupled. To manage the checkout path
yourself:

```bash
git clone https://github.com/cgraf78/vscode-exts.git
cd vscode-exts
./install.sh
```

`PREFIX` defaults to `$HOME/.local`; `BIN_DIR` can override its `bin` child.
The symlink resolves back to the matching checkout, keeping the CLI, Python
library, and schema version-coupled. Dependency managers can expose
`bin/vscode-exts` directly; for example, a shdeps entry is:

```text
cgraf78/vscode-exts  github
```

## Configuration

Without `--manifest`, `vscode-exts` reads the standard XDG configuration:

```text
$XDG_CONFIG_HOME/vscode-exts/extensions.toml
$XDG_CONFIG_HOME/vscode-exts/extensions.d/*.toml
```

When `XDG_CONFIG_HOME` is unset or relative, the root is
`$HOME/.config/vscode-exts`. The top-level file is loaded first when present;
files directly under `extensions.d` follow in lexical order. At least one file
must exist. Supplying one or more `--manifest` arguments uses exactly those
files in argument order and bypasses default discovery, which lets a
configuration manager retain its own fragment-selection policy.

Each manifest contains reusable extension bundles and concrete install
profiles:

```toml
[bundle.common]
extensions = [
  "ms-python.python",
  "rust-lang.rust-analyzer",
]

[bundle.desktop]
extensions = ["ms-vscode-remote.remote-ssh"]

[profile.vscode-local]
editor = "vscode"
channel = "stable"
scope = "local"
include = ["common", "desktop"]

[profile.vscode-remote]
editor = "vscode"
channel = "stable"
scope = "remote"
include = ["common"]
```

Fragments compose by name. Repeated bundles append extensions, and repeated
profiles append `include` entries. A profile's `editor`, `channel`, and `scope`
may be stated in any one fragment, but conflicting values are rejected. An
unpinned declaration requires only that an extension be present; append
`@VERSION` to require an exact version. Conflicting pins are rejected before
any editor is changed.

See [`examples/extensions.toml`](examples/extensions.toml) for a complete
starting point. The JSON Schema lives at
[`share/vscode-exts/schemas/extensions.schema.json`](share/vscode-exts/schemas/extensions.schema.json).

## Target discovery

Supported profile fields are:

- `editor = "vscode"`;
- `channel = "stable"` or `"insiders"`; and
- `scope = "local"` or `"remote"`.

On Linux, `vscode-exts` resolves the normal `code` or `code-insiders` command.
On macOS it also checks the standard application bundle when the CLI is absent
from `PATH`. A VS Code Remote IPC shim is never mistaken for a local desktop
install.

Remote profiles use a runnable `code-server` from the modern or legacy VS Code
Server layout, preferring the newest usable product version. Under WSL, local
profiles target native Windows VS Code through a small Windows-side command
wrapper, while remote profiles continue to target the WSL-side server. Set
`VSCODE_EXTS_WINDOWS_HOME` to the WSL-visible Windows profile directory when
Windows environment discovery is unavailable.

## Reconciliation and failures

For each resolved target, `vscode-exts`:

1. takes a non-blocking lock keyed by the extension directory;
2. asks the editor for its installed extension inventory;
3. installs missing declarations and mismatched pins; and
4. leaves undeclared and already-satisfied extensions unchanged.

Locks live under `$XDG_CACHE_HOME/vscode-exts/locks`, falling back to
`$HOME/.cache/vscode-exts/locks`. `VSCODE_EXTS_TIMEOUT_SECONDS` changes the
default 300-second timeout for each editor CLI invocation.

Malformed manifests and a missing default configuration return status 2.
Unavailable editor targets are skipped silently because profiles are portable
across machines. Gallery/network errors, lock contention, and individual
install failures are advisory: they emit warnings but do not make a
configuration update fail. This distinction lets a configuration error stop
automation while an offline laptop or temporarily broken editor remains
recoverable on the next run.

## Requirements

`vscode-exts` requires Python 3.11 or newer. It uses only the Python standard
library. The relevant VS Code CLI is optional at invocation time; profiles for
editors not installed on the current machine are skipped.

## Development

Run the complete behavior, installation, compilation, and ShellCheck suite:

```bash
test/run
```

The suite uses temporary fake editor installations and never changes the
machine's real extensions. See [`test/README.md`](test/README.md) and
[`docs/design.md`](docs/design.md) for the test and ownership boundaries.

## License

MIT. See [`LICENSE`](LICENSE).
