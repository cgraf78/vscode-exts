# Design and ownership

## Boundary

vscode-exts owns behavior that is reusable across configuration managers:

- the manifest model and schema;
- standard XDG discovery and ordered fragment aggregation;
- VS Code, VS Code Insiders, VS Code Server, macOS, and WSL target discovery;
- installed-inventory parsing, exact-version decisions, locking, and installs;
- warning and exit-status policy; and
- the Windows-side wrapper needed to keep native VS Code arguments off WSL UNC
  paths.

Consumers own only their extension selections and activation timing. A consumer
with its own layering semantics should select files itself and pass repeated
`--manifest` arguments; vscode-exts must not learn that consumer's overlay or
replacement model.

## Manifest aggregation

Every input file is a fragment of one aggregate. Bundle extension arrays and
profile include arrays append in input order. Target identity fields may be
split from includes, but conflicting definitions fail before target discovery.
Extension IDs normalize to lower case for comparison. Repeated unpinned entries
collapse, one exact pin takes precedence over an unpinned entry, and two
different pins are invalid.

This makes small environment-specific fragments useful without making order an
implicit conflict resolver. The default XDG stream is the optional
`extensions.toml` followed by immediate `extensions.d/*.toml` files in lexical
order. Explicit manifest arguments replace that discovery rather than extending
it, so automation remains deterministic.

## Additive state

The manifest is not a lockfile. Unpinned declarations mean “present,” not
“always newest.” Pinned declarations mean “present at this version.” Unmanaged
extensions are never removed, and already-satisfied declarations do not invoke
the installer. VS Code remains free to update unpinned extensions through its
own lifecycle.

Configuration errors return status 2 because retrying cannot repair them.
Machine state is different: a missing editor is skipped because manifests are
portable, while an offline gallery, broken candidate, timeout, or busy lock may
recover later and therefore warns without failing the run. The tool validates
the complete manifest aggregate before resolving or changing any target.

## Concurrency and artifacts

Different profile names can resolve to the same extension directory. Locks are
therefore keyed by the resolved directory rather than the profile, and stored
under the XDG cache tree. A process without an absolute XDG cache root or HOME
warns and proceeds without inventing a cwd-relative lock identity.

Most durable state belongs to VS Code itself. On WSL, vscode-exts additionally
writes `vscode-exts-code-cli.cmd` inside the native Windows VS Code profile. The
small deterministic wrapper contains only native drive paths; it exists to
prevent VS Code from treating a WSL UNC argument as an untrusted remote host.

## Packaging

The repository ships a thin `bin/vscode-exts` bootstrap and a provider-private
Python library. `install.sh` symlinks the bootstrap instead of copying it, so a
checkout update cannot leave the CLI, implementation, and schema at different
versions. Python 3.11 is the minimum because the standard-library `tomllib`
parser avoids a runtime package dependency.
