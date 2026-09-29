# Tests

`test/run` executes two independent suites, then three repository checks:

- `vscode-exts-test` covers manifest aggregation, XDG discovery, additive
  reconciliation, validation, locking, local and remote resolution across the
  Linux and macOS CI hosts, WSL-to-Windows argument handling, timeout behavior,
  and broken-server fallback; and
- `install-test` covers the checkout-backed symlink and refusal to overwrite a
  user-owned command.

The checks compile `bin/vscode-exts` and the `lib/vscode_exts` Python modules,
validate the JSON Schema's `$schema` and `$id`, and run ShellCheck over the
programs in `.github/shellcheck-files.txt`. ShellCheck is skipped when it is
not on `PATH` or when `VSCODE_EXTS_SKIP_SHELLCHECK=1` is set, which CI uses when
its shared ShellCheck job already covers the same inventory.

The editor fixtures implement the real CLI argument and installed-inventory
contract using temporary directories. They do not assert merely that a mock was
called: resulting extension inventories, selected commands, argument vectors,
Windows wrapper content, and preservation of unmanaged entries are checked.

All temporary paths live below one validated suite root. Tests never invoke a
real package manager, gallery, desktop application, or installed VS Code
extension host.
