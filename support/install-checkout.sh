#!/usr/bin/env bash
# Install a stable link rather than copying the launcher. The launcher resolves
# that link back to this checkout, so its Python library and bundled schema stay
# version-coupled without a second installation tree that can drift.

set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="${BIN_DIR:-$PREFIX/bin}"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

source="$ROOT/bin/vscode-exts"
target="$BIN_DIR/vscode-exts"
if [[ ! -f "$source" || ! -x "$source" ]]; then
  printf 'vscode-exts: command source is not executable: %s\n' "$source" >&2
  exit 1
fi
if [[ (-e "$target" || -L "$target") && ! -L "$target" ]]; then
  printf 'vscode-exts: refusing to replace non-symlink path: %s\n' "$target" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
ln -sfn "$source" "$target"

printf 'installed vscode-exts to %s\n' "$target"
