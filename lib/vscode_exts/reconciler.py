#!/usr/bin/env python3
"""Install declared VS Code marketplace extensions for dotfiles."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import tomllib

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

EXTENSION_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9][A-Za-z0-9_-]*"
    r"(?:@[A-Za-z0-9][A-Za-z0-9._+~-]*)?$"
)
SUPPORTED_EDITORS = {"vscode"}
SUPPORTED_CHANNELS = {"stable", "insiders"}
SUPPORTED_SCOPES = {"local", "remote"}
DEFAULT_CLI_TIMEOUT_SECONDS = 300.0


@dataclasses.dataclass(frozen=True)
class ExtensionSpec:
    """A marketplace extension requested by the manifest."""

    raw: str
    extension_id: str
    version: str | None

    @classmethod
    def parse(cls, value: str) -> ExtensionSpec:
        if not EXTENSION_RE.fullmatch(value):
            raise ValueError(f"invalid extension spec: {value}")
        if "@" in value:
            extension_id, version = value.rsplit("@", 1)
        else:
            extension_id, version = value, None
        return cls(raw=value, extension_id=extension_id.lower(), version=version)

    def install_arg(self) -> str:
        return self.raw


@dataclasses.dataclass(frozen=True)
class Profile:
    """A concrete extension install profile from extensions.toml."""

    name: str
    editor: str
    channel: str
    scope: str
    extensions: tuple[ExtensionSpec, ...]


@dataclasses.dataclass(frozen=True)
class Target:
    """A resolved VS Code extension host."""

    profile: Profile
    command: tuple[str, ...]
    extensions_dir: Path
    extensions_dir_arg: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    cwd: Path | None = None


@dataclasses.dataclass(frozen=True)
class Candidate:
    """A candidate VS Code Server install."""

    command: tuple[str, ...]
    version: tuple[int, ...]
    mtime: float


class ManifestError(Exception):
    """Raised when extensions.toml is malformed."""


@dataclasses.dataclass
class ProfileFragment:
    """Profile data collected from one or more overlay TOML fragments."""

    editor: str | None = None
    channel: str | None = None
    scope: str | None = None
    include: list[str] = dataclasses.field(default_factory=list)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def load_manifests(paths: Sequence[Path]) -> list[Profile]:
    """Load extension profiles from one or more overlay-friendly TOML manifests."""

    bundles: dict[str, list[ExtensionSpec]] = {}
    profiles: dict[str, ProfileFragment] = {}

    # Dot overlays should be able to add a small amount of editor policy without
    # repeating the base file. Treat every TOML file as a fragment: bundles with
    # the same name append extensions, and profile fragments append `include`
    # entries. Target identity fields (`editor`, `channel`, `scope`) are allowed
    # to appear only once for a profile so two overlays cannot silently point the
    # same profile name at different extension hosts.
    for path in paths:
        manifest = _load_toml(path)
        _merge_manifest(path, manifest, bundles, profiles)

    parsed_profiles: list[Profile] = []
    for name in sorted(profiles):
        fragment = profiles[name]
        editor = _complete_profile_field(fragment.editor, name, "editor")
        channel = _complete_profile_field(fragment.channel, name, "channel")
        scope = _complete_profile_field(fragment.scope, name, "scope")
        _validate_profile_target(name, editor, channel, scope)

        requested: list[ExtensionSpec] = []
        for bundle_name in fragment.include:
            if bundle_name not in bundles:
                raise ManifestError(f"profile.{name} includes unknown bundle: {bundle_name}")
            requested.extend(bundles[bundle_name])

        parsed_profiles.append(
            Profile(
                name=name,
                editor=editor,
                channel=channel,
                scope=scope,
                extensions=tuple(_normalize_extensions(name, requested)),
            )
        )

    return parsed_profiles


def _load_toml(path: Path) -> dict[str, object]:
    """Load one TOML file and attach the path to normal user-facing errors."""

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"{path}: {exc}") from exc
    return data


def _merge_manifest(
    path: Path,
    manifest: dict[str, object],
    bundles: dict[str, list[ExtensionSpec]],
    profiles: dict[str, ProfileFragment],
) -> None:
    """Merge one manifest fragment into accumulated bundle/profile state."""

    _reject_unknown_keys(path, "manifest", manifest, {"bundle", "profile"})
    raw_bundles = manifest.get("bundle", {})
    raw_profiles = manifest.get("profile", {})
    if not isinstance(raw_bundles, dict):
        raise ManifestError(f"{path}: bundle must be a TOML table")
    if not isinstance(raw_profiles, dict):
        raise ManifestError(f"{path}: profile must be a TOML table")

    for name, data in raw_bundles.items():
        if not isinstance(data, dict):
            raise ManifestError(f"{path}: bundle.{name} must be a TOML table")
        _reject_unknown_keys(path, f"bundle.{name}", data, {"extensions"})
        extensions = data.get("extensions", [])
        if not isinstance(extensions, list) or not all(
            isinstance(item, str) for item in extensions
        ):
            raise ManifestError(f"{path}: bundle.{name}.extensions must be a string array")
        bundles.setdefault(name, []).extend(ExtensionSpec.parse(item) for item in extensions)

    for name, data in raw_profiles.items():
        if not isinstance(data, dict):
            raise ManifestError(f"{path}: profile.{name} must be a TOML table")
        _reject_unknown_keys(
            path, f"profile.{name}", data, {"editor", "channel", "scope", "include"}
        )

        fragment = profiles.setdefault(name, ProfileFragment())
        _merge_profile_field(path, name, "editor", data, fragment)
        _merge_profile_field(path, name, "channel", data, fragment)
        _merge_profile_field(path, name, "scope", data, fragment)
        include = data.get("include", [])
        if not isinstance(include, list) or not all(isinstance(item, str) for item in include):
            raise ManifestError(f"{path}: profile.{name}.include must be a string array")
        fragment.include.extend(include)


def _reject_unknown_keys(
    path: Path, context: str, data: dict[str, object], allowed: set[str]
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ManifestError(f"{path}: {context} has unknown key: {unknown[0]}")


def _merge_profile_field(
    path: Path,
    profile_name: str,
    field: str,
    data: dict[str, object],
    fragment: ProfileFragment,
) -> None:
    value = data.get(field)
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{path}: profile.{profile_name}.{field} must be a non-empty string")

    current = getattr(fragment, field)
    if current is not None and current != value:
        raise ManifestError(
            f"{path}: profile.{profile_name}.{field} conflicts with earlier value {current!r}"
        )
    setattr(fragment, field, value)


def _complete_profile_field(value: str | None, profile_name: str, field: str) -> str:
    if value is None:
        raise ManifestError(f"profile.{profile_name}.{field} is required after overlay merge")
    return value


def _validate_profile_target(profile_name: str, editor: str, channel: str, scope: str) -> None:
    """Reject profile target typos before they become silent install skips."""

    # The schema catches these during normal editing, but the helper is the
    # runtime authority during cron and on machines where schema lint may not
    # have run. Treat unsupported target values as configuration errors so a
    # misspelled `remote` profile cannot quietly skip extension installation.
    if editor not in SUPPORTED_EDITORS:
        raise ManifestError(f"profile.{profile_name}.editor is unsupported: {editor}")
    if channel not in SUPPORTED_CHANNELS:
        raise ManifestError(f"profile.{profile_name}.channel is unsupported: {channel}")
    if scope not in SUPPORTED_SCOPES:
        raise ManifestError(f"profile.{profile_name}.scope is unsupported: {scope}")


def _normalize_extensions(
    profile_name: str, extensions: Sequence[ExtensionSpec]
) -> list[ExtensionSpec]:
    """Collapse duplicate extension requests into one deterministic spec per ID."""

    # Overlay fragments compose into a single profile, so the same extension can
    # appear through multiple bundles. Duplicate unpinned specs are harmless, and
    # an unpinned baseline plus one pinned overlay should deterministically mean
    # "pin it". Two different pins for the same extension are configuration
    # drift, not an ordering decision the installer should make implicitly.
    by_id: dict[str, ExtensionSpec] = {}
    for spec in extensions:
        current = by_id.get(spec.extension_id)
        if current is None:
            by_id[spec.extension_id] = spec
            continue
        if current.version == spec.version:
            continue
        if current.version is None:
            by_id[spec.extension_id] = spec
            continue
        if spec.version is None:
            continue
        raise ManifestError(
            f"profile.{profile_name} requests conflicting versions for "
            f"{spec.extension_id}: {current.version} and {spec.version}"
        )
    return list(by_id.values())


def resolve_target(profile: Profile, home: Path) -> Target | None:
    """Resolve a profile into an installable VS Code extension target."""

    if profile.editor not in SUPPORTED_EDITORS:
        warn(f"{profile.name}: unsupported editor '{profile.editor}'")
        return None
    if profile.channel not in SUPPORTED_CHANNELS:
        warn(f"{profile.name}: unsupported channel '{profile.channel}'")
        return None
    if profile.scope not in SUPPORTED_SCOPES:
        warn(f"{profile.name}: unsupported scope '{profile.scope}'")
        return None

    if profile.scope == "local":
        return _resolve_local_target(profile, home)
    return _resolve_remote_target(profile, home)


def _resolve_local_target(profile: Profile, home: Path) -> Target | None:
    if _is_wsl():
        return _resolve_wsl_windows_target(profile)

    command_name = "code" if profile.channel == "stable" else "code-insiders"
    command = shutil.which(command_name)
    command_path: Path | None = Path(command) if command else None
    if command_path is not None and _is_vscode_remote_cli(command_path, home):
        # VS Code Remote injects a `code` shim that talks to the already-running
        # remote window over IPC. That is useful in an integrated terminal, but it
        # is not a desktop install and it is unavailable from cron/plain shells.
        # Treat it as "no local target" so the remote profile can be handled by
        # the standalone server-side code-server resolver below.
        command_path = None

    if command_path is None and platform.system() == "Darwin":
        # GUI VS Code installs on macOS do not always put `code` on PATH,
        # especially for cron or non-login shells. The app bundle carries the
        # same CLI shim, so use it as a conservative local-only fallback.
        app_name = (
            "Visual Studio Code.app"
            if profile.channel == "stable"
            else "Visual Studio Code - Insiders.app"
        )
        candidate = (
            Path("/Applications")
            / app_name
            / "Contents"
            / "Resources"
            / "app"
            / "bin"
            / command_name
        )
        if candidate.is_file() and os.access(candidate, os.X_OK):
            command_path = candidate

    if command_path is None:
        return None

    extensions_dir = home / (
        ".vscode/extensions" if profile.channel == "stable" else ".vscode-insiders/extensions"
    )
    return Target(profile=profile, command=(str(command_path),), extensions_dir=extensions_dir)


def _resolve_wsl_windows_target(profile: Profile) -> Target | None:
    """Resolve native Windows VS Code from a WSL process."""

    windows_home = _wsl_windows_home()
    if windows_home is None:
        return None

    if profile.channel == "stable":
        app_names = ("Microsoft VS Code",)
        exe_name = "Code.exe"
        extensions_dir = windows_home / ".vscode" / "extensions"
    else:
        app_names = ("Microsoft VS Code Insiders",)
        exe_name = "Code - Insiders.exe"
        extensions_dir = windows_home / ".vscode-insiders" / "extensions"

    app_roots: list[Path] = []
    for app_name in app_names:
        app_roots.extend(
            [
                windows_home / "AppData" / "Local" / "Programs" / app_name,
                Path("/mnt/c/Program Files") / app_name,
                Path("/mnt/c/Program Files (x86)") / app_name,
            ]
        )

    for app_root in app_roots:
        code_exe = app_root / exe_name
        cli_js = _windows_vscode_cli(app_root)
        if not code_exe.is_file() or cli_js is None:
            continue

        # Do not execute `Code.exe cli.js ...` directly from WSL. Windows may
        # see the script argument as a `\\wsl.localhost\...` UNC path even when
        # it lives on `/mnt/c`, which triggers VS Code's allowed-host prompt.
        # A tiny Windows-side batch file keeps every path that VS Code receives
        # in native `C:\...` form and avoids trusting the WSL UNC host.
        cmd_exe = _windows_cmd_exe()
        wrapper = _write_windows_cli_wrapper(profile, windows_home, code_exe, cli_js)
        if cmd_exe is None or wrapper is None:
            continue

        target = Target(
            profile=profile,
            command=(str(cmd_exe), "/D", "/C", wrapper),
            extensions_dir=extensions_dir,
            extensions_dir_arg=_windows_arg_path(extensions_dir),
            # `cmd.exe` refuses UNC current directories and otherwise falls
            # back to C:\Windows. Start it inside the Windows profile so every
            # path in this invocation is local to Windows, not WSL UNC state.
            cwd=windows_home,
        )
        if _can_list_extensions(target):
            return target
        warn(f"{profile.name}: cannot list extensions with {_command_display(target.command)}")

    return None


def _windows_vscode_cli(app_root: Path) -> Path | None:
    direct = app_root / "resources" / "app" / "out" / "cli.js"
    if direct.is_file():
        return direct

    candidates = [path for path in app_root.glob("*/resources/app/out/cli.js") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, str(path)))


def _write_windows_cli_wrapper(
    profile: Profile, windows_home: Path, code_exe: Path, cli_js: Path
) -> str | None:
    """Write a Windows-side wrapper so VS Code never sees WSL UNC CLI paths."""

    # Keep the wrapper under the Windows user profile rather than under
    # XDG_CACHE_HOME or /tmp. A WSL-side script path would itself become a
    # `\\wsl.localhost\...` argument to cmd.exe, recreating the prompt this
    # wrapper is meant to avoid. Rewriting the file on each run is cheap and
    # keeps it aligned with VS Code updates that move cli.js between layouts.
    wrapper_dir = windows_home / (".vscode" if profile.channel == "stable" else ".vscode-insiders")
    wrapper = wrapper_dir / "dot-code-cli.cmd"
    # Set ELECTRON_RUN_AS_NODE in the batch file instead of relying on WSLENV.
    # WSLENV forwarding is useful for simple .exe launches, but here cmd.exe is
    # the process boundary we control; declaring the variable in Windows command
    # syntax makes the wrapper self-contained and removes one more WSL-specific
    # transformation from the path.
    content = (
        "@echo off\r\n"
        'set "ELECTRON_RUN_AS_NODE=1"\r\n'
        f'"{_windows_arg_path(code_exe)}" "{_windows_arg_path(cli_js)}" %*\r\n'
    )
    try:
        wrapper_dir.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(content, encoding="utf-8", newline="")
    except OSError as exc:
        warn(f"{profile.name}: failed to write Windows VS Code CLI wrapper: {exc}")
        return None
    return _windows_arg_path(wrapper)


def _wsl_windows_home() -> Path | None:
    override = (
        os.environ.get("DOT_TEST_WINDOWS_HOME")
        or os.environ.get("DOT_WINDOWS_HOME")
        or os.environ.get("DOT_VSCODE_WINDOWS_HOME")
    )
    if override:
        return Path(override)

    if os.environ.get("DOT_TEST") == "1":
        return None

    userprofile = _windows_env_path("USERPROFILE")
    if userprofile is not None:
        return userprofile

    appdata = _windows_env_path("APPDATA")
    if appdata is not None and appdata.name == "Roaming" and appdata.parent.name == "AppData":
        return appdata.parent.parent

    # Do not infer the Windows username by scanning /mnt/c/Users. On shared or
    # reused Windows machines, "the only profile with VS Code installed" can
    # still be the wrong account. If Windows cannot tell us the current profile,
    # skip the native target unless the caller sets DOT_VSCODE_WINDOWS_HOME
    # explicitly.
    return None


def _windows_cmd_exe() -> Path | None:
    override = os.environ.get("DOT_TEST_WINDOWS_CMD_EXE")
    if override:
        return Path(override)

    # Prefer the real Windows system cmd.exe over a PATH entry. WSL PATH can be
    # customized heavily, and a stale/fake `cmd.exe` earlier in PATH would break
    # both Windows profile discovery and the wrapper launch. Keep PATH only as
    # a discovery fallback for unusual automount/PATH setups, then still require
    # the resolved command to be the Windows System32 cmd.exe.
    candidates: list[Path] = []
    system_cmd = _wslpath(r"C:\Windows\System32\cmd.exe")
    if system_cmd is not None:
        candidates.append(system_cmd)
    candidates.append(Path("/mnt/c/Windows/System32/cmd.exe"))
    path_command = shutil.which("cmd.exe")
    if path_command is not None:
        candidates.append(Path(path_command))

    seen: set[Path] = set()
    for command_path in candidates:
        if command_path in seen:
            continue
        seen.add(command_path)
        if (
            command_path.exists()
            and _is_windows_system_cmd(command_path)
            and _can_run_windows_cmd(command_path)
        ):
            return command_path
    return None


def _is_windows_system_cmd(command_path: Path) -> bool:
    windows_path = _windows_arg_path(command_path).replace("/", "\\").lower()
    return bool(re.fullmatch(r"[a-z]:\\windows\\system32\\cmd\.exe", windows_path))


def _can_run_windows_cmd(command_path: Path) -> bool:
    # Give cmd.exe a Windows-mounted cwd while validating it. If it starts in
    # the agent's Linux cwd, Windows reports a UNC cwd warning and falls back to
    # C:\Windows, which is exactly the class of cross-boundary behavior this
    # helper is trying to avoid.
    cwd = _windows_cmd_cwd(command_path)
    kwargs: dict[str, object] = {}
    if cwd is not None and cwd.is_dir():
        kwargs["cwd"] = cwd
    try:
        result = subprocess.run(
            [str(command_path), "/D", "/C", "exit 0"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _windows_env_path(name: str) -> Path | None:
    command_path = _windows_cmd_exe()
    if command_path is None:
        return None
    cwd = _windows_cmd_cwd(command_path)
    kwargs: dict[str, object] = {}
    if cwd is not None and cwd.is_dir():
        kwargs["cwd"] = cwd
    try:
        result = subprocess.run(
            [str(command_path), "/D", "/C", f"set {name}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    prefix = f"{name.upper()}="
    value = ""
    for line in result.stdout.replace("\r", "").splitlines():
        if line.upper().startswith(prefix):
            value = line.split("=", 1)[1].strip()
            break
    if not value:
        return None
    converted = _wslpath(value)
    if converted is not None:
        return converted
    return None


def _windows_cmd_cwd(command_path: Path) -> Path | None:
    windows_path = _windows_arg_path(command_path)
    match = re.match(r"^([A-Za-z]):\\", windows_path)
    if match is not None:
        drive_root = _wslpath(f"{match.group(1)}:\\")
        if drive_root is not None and drive_root.is_dir():
            return drive_root

    parts = command_path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1:
        return Path("/", "mnt", parts[2])
    return None


def _wslpath(value: str) -> Path | None:
    command = shutil.which("wslpath")
    if command is None:
        return None
    try:
        result = subprocess.run(
            [command, value],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    converted = result.stdout.strip()
    return Path(converted) if result.returncode == 0 and converted else None


def _windows_arg_path(path: Path) -> str:
    """Convert a WSL-mounted Windows path into a Windows CLI argument."""

    # Prefer `C:\...` over `/mnt/c/...` or `C:/...` for arguments that cross
    # from WSL into cmd.exe and then into VS Code. The important property is not
    # aesthetics; it is that VS Code's own URI/trust checks see a local Windows
    # drive path rather than a WSL UNC host such as `\\wsl.localhost\...`.
    converted = _wslpath_windows(path)
    if converted is not None:
        return converted

    parts = path.parts
    if len(parts) >= 4 and parts[0] == "/" and parts[1] == "mnt" and len(parts[2]) == 1:
        drive = parts[2].upper()
        return f"{drive}:\\" + "\\".join(parts[3:])
    return str(path)


def _wslpath_windows(path: Path) -> str | None:
    # Ask WSL for the Windows spelling instead of assuming `/mnt/<drive>`.
    # Users can configure a different automount root, and future WSL versions
    # may expose paths differently. Reject UNC results because the native VS Code
    # CLI must see a local drive path to avoid the allowed-host prompt.
    command = shutil.which("wslpath")
    if command is None:
        return None
    try:
        result = subprocess.run(
            [command, "-w", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    converted = result.stdout.strip()
    if result.returncode == 0 and re.match(r"^[A-Za-z]:\\", converted):
        return converted
    return None


def _is_vscode_remote_cli(command_path: Path, home: Path) -> bool:
    """Return true when a PATH `code` command is VS Code Remote's IPC shim."""

    # The remote shim commonly lives at
    # ~/.vscode-server/.../server/bin/remote-cli/code. Reject the whole
    # .vscode-server family for local desktop profiles; the remote resolver has a
    # separate, non-IPC code-server path that works from cron and normal shells.
    try:
        resolved = command_path.resolve()
    except OSError:
        resolved = command_path.absolute()

    for root_name in (".vscode-server", ".vscode-server-insiders"):
        root = home / root_name
        if _path_is_relative_to(resolved, root):
            return True
    return "remote-cli" in resolved.parts


def _is_wsl() -> bool:
    return bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_remote_target(profile: Profile, home: Path) -> Target | None:
    if profile.channel == "stable":
        root = home / ".vscode-server"
        pattern = "Stable-*"
    else:
        root = home / ".vscode-server-insiders"
        pattern = "Insiders-*"

    server_root = root / "cli" / "servers"
    candidates: list[Candidate] = []
    for install in server_root.glob(pattern):
        server = install / "server"
        command = server / "bin" / "code-server"
        product = server / "product.json"
        # Do not use server/bin/remote-cli/code here. That shim needs VS Code
        # terminal/IPC context and fails from the normal shells and cron jobs
        # that run `dot update` on remote hosts. The server's `code-server`
        # binary supports extension management directly when pointed at the
        # shared server extension directory.
        if not command.is_file() or not os.access(command, os.X_OK) or not product.is_file():
            continue
        candidates.append(
            Candidate(
                command=(str(command),),
                version=_product_version(product),
                mtime=server.stat().st_mtime,
            )
        )
    legacy_server_root = root / "bin"
    if legacy_server_root.is_dir():
        for server in legacy_server_root.iterdir():
            command = server / "bin" / "code-server"
            product = server / "product.json"
            if not command.is_file() or not os.access(command, os.X_OK) or not product.is_file():
                continue
            candidates.append(
                Candidate(
                    command=(str(command),),
                    version=_product_version(product),
                    mtime=server.stat().st_mtime,
                )
            )

    for candidate in sorted(
        candidates,
        key=lambda item: (item.version, item.mtime, _command_display(item.command)),
        reverse=True,
    ):
        target = Target(
            profile=profile, command=candidate.command, extensions_dir=root / "extensions"
        )
        if _can_list_extensions(target):
            return target
        warn(f"{profile.name}: cannot list extensions with {_command_display(candidate.command)}")

    return None


def _product_version(path: Path) -> tuple[int, ...]:
    try:
        with path.open(encoding="utf-8") as handle:
            version = json.load(handle).get("version", "")
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(version, str):
        return ()
    parts: list[int] = []
    for item in version.split("."):
        match = re.match(r"^(\d+)", item)
        if match is None:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _can_list_extensions(target: Target) -> bool:
    result = _run_code(target, ["--list-extensions", "--show-versions"])
    if result is None:
        return False
    return _extension_listing_succeeded(target, result)


def _extension_listing_succeeded(target: Target, result: subprocess.CompletedProcess[str]) -> bool:
    """Reject IPC-only shims that print an error but incorrectly exit zero."""

    if result.returncode != 0:
        return False
    if result.stdout.strip() or not result.stderr.strip():
        return True

    version = _run_code(target, ["--version"])
    if version is None or version.returncode != 0:
        return False
    first_line = next((line.strip() for line in version.stdout.splitlines() if line.strip()), "")
    return re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", first_line) is not None


def installed_extensions(target: Target) -> dict[str, str | None] | None:
    """Return installed extension versions keyed by lower-case extension ID."""

    result = _run_code(target, ["--list-extensions", "--show-versions"])
    if result is None:
        return None
    if not _extension_listing_succeeded(target, result):
        warn(f"{target.profile.name}: failed to list extensions: {_stderr_summary(result)}")
        return None

    installed: dict[str, str | None] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "@" in line:
            extension_id, version = line.rsplit("@", 1)
        else:
            extension_id, version = line, None
        installed[extension_id.lower()] = version
    return installed


def needs_install(spec: ExtensionSpec, installed: dict[str, str | None]) -> bool:
    if spec.extension_id not in installed:
        return True
    # Unpinned specs express minimum presence, not freshness. Pinned specs are
    # the only case where this helper should force a reinstall/update, keeping
    # routine dot updates from fighting VS Code's own extension lifecycle.
    return spec.version is not None and installed[spec.extension_id] != spec.version


def install_profile(target: Target) -> None:
    """Install missing extensions for one resolved target."""

    with extension_lock(target.extensions_dir) as locked:
        if not locked:
            warn(f"{target.profile.name}: extension directory is locked; skipping")
            return

        installed = installed_extensions(target)
        if installed is None:
            return

        # This manifest is intentionally not a lockfile. Preserve anything the
        # user installed by hand, and only ask VS Code to install missing or
        # version-mismatched declared extensions.
        target.extensions_dir.mkdir(parents=True, exist_ok=True)
        for spec in target.profile.extensions:
            if not needs_install(spec, installed):
                continue
            result = _run_code(target, ["--install-extension", spec.install_arg()])
            if result is None:
                continue
            if result.returncode == 0:
                installed[spec.extension_id] = spec.version
            else:
                warn(
                    f"{target.profile.name}: failed to install {spec.install_arg()}: "
                    f"{_stderr_summary(result)}"
                )


@contextlib.contextmanager
def extension_lock(extensions_dir: Path) -> Iterator[bool]:
    """Take a non-blocking lock for an extension directory."""

    # Extension dirs are shared between VS Code itself, manual `dot update`, and
    # cron. Lock by resolved target directory rather than by profile name so
    # different manifest fragments that point at the same extension host cannot
    # race writes to extensions.json or the extension install tree.
    lock_path = _extension_lock_path(extensions_dir)
    if lock_path is None:
        # Reconciliation is advisory. A process without either an absolute XDG
        # cache root or HOME can still proceed, but must not invent a
        # cwd-relative lock that disagrees with another invocation.
        warn("HOME is not set and XDG_CACHE_HOME is not absolute; proceeding without a lock")
        yield True
        return

    cache_dir = lock_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a", encoding="utf-8") as handle:
        if fcntl is None:
            # Windows Python has no fcntl module. Keep extension reconciliation
            # best-effort instead of failing at import time; VS Code installs are
            # advisory and a missing lock is less harmful than disabling the
            # entire helper on that platform.
            yield True
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _xdg_cache_home(env: Mapping[str, str] = os.environ) -> Path | None:
    """Resolve an absolute XDG cache root without depending on the cwd."""

    cache_home = Path(env.get("XDG_CACHE_HOME", ""))
    if cache_home.is_absolute():
        return cache_home
    home = Path(env.get("HOME", ""))
    if home.is_absolute():
        return home / ".cache"
    return None


def _extension_lock_path(extensions_dir: Path, env: Mapping[str, str] = os.environ) -> Path | None:
    """Return the stable lock identity for one extension directory."""

    cache_home = _xdg_cache_home(env)
    if cache_home is None:
        return None
    key = hashlib.sha256(str(extensions_dir).encode("utf-8")).hexdigest()[:24]
    return cache_home / "dot" / "vscode-extensions" / f"{key}.lock"


def _run_code(target: Target, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    target.extensions_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *target.command,
        "--extensions-dir",
        target.extensions_dir_arg or str(target.extensions_dir),
        *args,
    ]
    env = os.environ.copy()
    env.update(dict(target.env))
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_cli_timeout_seconds(),
            env=env,
            cwd=target.cwd,
        )
    except subprocess.TimeoutExpired:
        warn(f"{target.profile.name}: VS Code CLI timed out: {' '.join(command)}")
        return None
    except OSError as exc:
        warn(
            f"{target.profile.name}: failed to run VS Code CLI "
            f"{_command_display(target.command)}: {exc}"
        )
        return None


def _command_display(command: Sequence[str]) -> str:
    return " ".join(command)


def _cli_timeout_seconds() -> float:
    raw = os.environ.get("DOT_VSCODE_EXTENSIONS_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_CLI_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_CLI_TIMEOUT_SECONDS
    return timeout if timeout > 0 else DEFAULT_CLI_TIMEOUT_SECONDS


def _stderr_summary(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return text.splitlines()[-1] if text else f"exit {result.returncode}"


def run(manifests: Sequence[Path], home: Path) -> int:
    try:
        profiles = load_manifests(manifests)
    except (ManifestError, ValueError) as exc:
        warn(str(exc))
        return 2

    for profile in profiles:
        target = resolve_target(profile, home)
        if target is None:
            continue
        install_profile(target)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args()
    return run(args.manifest, args.home)


if __name__ == "__main__":
    raise SystemExit(main())
