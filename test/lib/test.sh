#!/usr/bin/env bash
# Small repository-owned test harness. The extracted behavior suite must not
# depend on dotfiles being installed, because vscode-exts is a standalone tool.

PASS=0
FAIL=0
export VSCODE_EXTS_TEST_MODE=1
export PYTHONDONTWRITEBYTECODE=1

_pass() {
  PASS=$((PASS + 1))
  printf '  PASS: %s\n' "$1"
}

_fail() {
  FAIL=$((FAIL + 1))
  printf '  FAIL: %s\n' "$1" >&2
}

_assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    _pass "$desc"
  else
    _fail "$desc (expected '$expected', got '$actual')"
  fi
}

_assert_contains() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    _pass "$desc"
  else
    _fail "$desc (expected to contain '$expected', got '$actual')"
  fi
}

_assert_exit() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" -eq "$actual" ]]; then
    _pass "$desc"
  else
    _fail "$desc (expected exit $expected, got $actual)"
  fi
}

_assert_file_exists() {
  local desc="$1" path="$2"
  if [[ -f "$path" ]]; then
    _pass "$desc"
  else
    _fail "$desc (file not found: $path)"
  fi
}

_assert_file_missing() {
  local desc="$1" path="$2"
  if [[ ! -f "$path" ]]; then
    _pass "$desc"
  else
    _fail "$desc (file should not exist: $path)"
  fi
}

# All fixture paths live below one validated root so cleanup has a narrow,
# auditable target even when a test fails halfway through WSL path setup.
_VSCODE_EXTS_TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/vscode-exts-test.XXXXXXXX") || {
  printf 'vscode-exts test: could not create temporary root\n' >&2
  exit 1
}
case "$_VSCODE_EXTS_TEST_ROOT" in
  "${TMPDIR:-/tmp}"/vscode-exts-test.*) ;;
  *)
    printf 'vscode-exts test: unsafe temporary root: %s\n' \
      "$_VSCODE_EXTS_TEST_ROOT" >&2
    exit 1
    ;;
esac
[[ -d "$_VSCODE_EXTS_TEST_ROOT" ]] || {
  printf 'vscode-exts test: temporary root is not a directory: %s\n' \
    "$_VSCODE_EXTS_TEST_ROOT" >&2
  exit 1
}

_vscode_exts_test_cleanup() {
  local status=$?
  trap - EXIT
  rm -rf -- "$_VSCODE_EXTS_TEST_ROOT"
  exit "$status"
}
trap _vscode_exts_test_cleanup EXIT

_tmpdir() {
  local path
  path=$(mktemp -d "$_VSCODE_EXTS_TEST_ROOT/suite.XXXXXXXX") || {
    printf 'vscode-exts test: could not create suite temporary directory\n' >&2
    return 1
  }
  case "$path" in
    "$_VSCODE_EXTS_TEST_ROOT"/*) ;;
    *)
      printf 'vscode-exts test: unsafe suite temporary directory: %s\n' "$path" >&2
      return 1
      ;;
  esac
  [[ -d "$path" ]] || {
    printf 'vscode-exts test: suite temporary directory is missing: %s\n' "$path" >&2
    return 1
  }
  printf '%s\n' "$path"
}

_test_summary() {
  printf '\n================================\n'
  printf 'Results: %s passed, %s failed\n' "$PASS" "$FAIL"
  printf '================================\n'
  [[ "$FAIL" -eq 0 ]] && exit 0
  exit 1
}
