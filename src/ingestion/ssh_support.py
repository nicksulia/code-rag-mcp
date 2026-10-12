"""POSIX/OpenSSH references, not key custody.

Only bounded format headers and public fingerprints are inspected. Encrypted
keys must be unlocked externally in the inherited agent; files are never
copied, chmodded, decrypted, or removed by this module.
"""

import os
import json
import logging
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("rag.repository")


class SSHError(RuntimeError):
    """Fixed category only; subprocess diagnostics and paths are not retained."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Identity:
    private_key_path: str
    fingerprint: str


@dataclass
class GitContext:
    repo_id: Optional[str] = None
    identity: Optional[Identity] = None
    ssh_allowed: bool = True
    captured: bool = False


def run_checked(arguments, *, check=True, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=15, env=None,
                start_new_session=True, encoding="utf-8", errors="replace", cwd=None):
    """Own the process group, including descendants, on timeout/cancellation."""
    with subprocess.Popen(
        arguments, cwd=cwd, stdin=stdin, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, start_new_session=start_new_session,
        text=text, encoding=encoding, errors=errors,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            if start_new_session and os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.stdout.close()
                process.stderr.close()
                process.wait(timeout=1)
            raise
        result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, arguments, stdout, stderr)
        return result


def _effective(arguments: list[str], *, trace: bool = False) -> tuple[dict[str, list[str]], list[Path]]:
    try:
        result = run_checked(["ssh", *(["-vv"] if trace else []), "-G", *arguments],
                             timeout=15, start_new_session=False)
    except (FileNotFoundError, PermissionError):
        raise SSHError("ssh_tool_unavailable") from None
    except subprocess.TimeoutExpired:
        raise SSHError("ssh_key_timeout") from None
    except (OSError, subprocess.CalledProcessError):
        raise SSHError("ssh_config_unsupported") from None
    settings: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if not separator or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*", key) or has_controls(value):
            raise SSHError("ssh_config_unsupported")
        settings.setdefault(key.lower(), []).append(value)
    files = [Path(line.split("Reading configuration data ", 1)[1])
             for line in result.stderr.splitlines() if "Reading configuration data " in line]
    return settings, files


def _quote(value: str) -> str:
    if has_controls(value) or '"' in value or "\\" in value:
        raise SSHError("ssh_config_unsupported")
    return '"' + value + '"'


def _path_tokens(key: str, value: str, files: list[Path]) -> list[str]:
    """Recover multi-path token boundaries before serializing ssh -G output.

    OpenSSH prints quoted path lists without quotes. Only an unambiguous
    original directive (or known built-in defaults) is safe to reconstruct.
    """
    candidates: set[tuple[str, ...]] = set()
    defaults = {
        "userknownhostsfile": (os.path.expanduser("~/.ssh/known_hosts"), os.path.expanduser("~/.ssh/known_hosts2")),
        "globalknownhostsfile": ("/etc/ssh/ssh_known_hosts", "/etc/ssh/ssh_known_hosts2"),
    }
    if " ".join(defaults[key]) == value:
        candidates.add(defaults[key])
    if value and not any(character.isspace() for character in value):
        candidates.add((value,))
    for path in files:
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    directive = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9]*)\s*(?:=\s*)?(.*)$", line)
                    if directive and directive[1].lower() == key:
                        values = tuple(shlex.split(directive[2], comments=True))
                        if " ".join(values) == value:
                            candidates.add(values)
        except (OSError, ValueError, UnicodeError):
            raise SSHError("ssh_config_unsupported") from None
    if len(candidates) != 1:
        raise SSHError("ssh_config_unsupported")
    return list(next(iter(candidates)))


def materialize_configuration(arguments: list[str], identity: Identity, directory: Path) -> Path:
    """Resolve the destination once; only non-target hops load ordinary config."""
    original, files = _effective(arguments, trace=True)
    host = original.get("host", [""])[0]
    if not re.fullmatch(r"[a-zA-Z0-9_.:%-]+", host) or host.startswith("-"):
        raise SSHError("ssh_config_unsupported")
    key, _, _ = inspect_private_key(identity.private_key_path, identity.fingerprint)
    reference = directory / "identity"
    reference.symlink_to(key)
    overrides = {
        "identityfile": [str(reference)], "certificatefile": ["none"],
        "identitiesonly": ["yes"], "batchmode": ["yes"],
        "preferredauthentications": ["publickey"], "pubkeyauthentication": ["yes"],
        "passwordauthentication": ["no"], "kbdinteractiveauthentication": ["no"],
        "hostbasedauthentication": ["no"], "gssapiauthentication": ["no"],
        "forwardagent": ["no"], "addkeystoagent": ["no"],
        "identityagent": [os.environ.get("SSH_AUTH_SOCK") or "none"],
        "stricthostkeychecking": ["yes"],
        "nohostauthenticationforlocalhost": ["no"],
        "canonicalizehostname": ["false"],
        "controlmaster": ["no"], "controlpath": ["none"], "controlpersist": ["no"],
        # Inspection trace verbosity must not become network diagnostics.
        "loglevel": ["ERROR"],
    }
    settings = {**original, **overrides}
    if "nohostauthenticationforproxycommand" in settings:
        settings["nohostauthenticationforproxycommand"] = ["no"]
    lines = ["Host " + host]
    cli_sendenv = "SendEnv=GIT_PROTOCOL" in arguments
    skipped_sendenv = False
    for key, values in settings.items():
        if key == "host":
            continue
        for value in values:
            if key == "sendenv" and value == "GIT_PROTOCOL" and cli_sendenv and not skipped_sendenv:
                skipped_sendenv = True
                continue
            if key in {"userknownhostsfile", "globalknownhostsfile"}:
                value = " ".join(_quote(token) for token in _path_tokens(key, value, files))
            elif key in {"identityfile", "identityagent", "certificatefile", "xauthlocation",
                         "revokedhostkeys", "securitykeyprovider", "hostkeyalias", "hostname", "user"}:
                value = _quote(value)
            lines.append("  " + key + " " + value)
    # -F replaces both ordinary files. Include them only for proxy-hop hosts.
    target_arguments = list(arguments)
    includes = [os.path.expanduser("~/.ssh/config"), "/etc/ssh/ssh_config"]
    if "-F" in target_arguments:
        index = target_arguments.index("-F")
        includes = [target_arguments[index + 1]]
        del target_arguments[index:index + 2]
    lines.extend(["Host * !" + host, *["  Include " + _quote(path) for path in includes]])
    config = directory / "config"
    with config.open("x", encoding="utf-8") as stream:
        os.chmod(config, 0o600)
        stream.write("\n".join(lines) + "\n")
    effective, _ = _effective(["-F", str(config), *target_arguments])
    for key, values in settings.items():
        if key in {"pubkeyauthentication", "controlmaster", "addkeystoagent"}:
            # OpenSSH normalizes these booleans to true/false.
            values = [{"yes": "true", "no": "false"}.get(value, value) for value in values]
        if key == "stricthostkeychecking":
            values = ["true"]
        if key == "host":
            continue
        if key == "controlpath" and values == ["none"] and key not in effective:
            continue
        if effective.get(key) != values:
            raise SSHError("ssh_config_unsupported")
    return config


def _split_git_ssh(arguments: list[str]) -> tuple[list[str], list[str]]:
    options: list[str] = []
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        if option in {"-p", "-l", "-o"} and index + 1 < len(arguments):
            value = arguments[index + 1]
            if option == "-o" and value.lower() != "sendenv=git_protocol":
                raise SSHError("ssh_config_unsupported")
            options.extend([option, value])
            index += 2
        elif option in {"-4", "-6"}:
            options.append(option)
            index += 1
        else:
            raise SSHError("ssh_config_unsupported")
    if index >= len(arguments):
        raise SSHError("ssh_config_unsupported")
    return [*options, arguments[index]], arguments[index + 1:]


def launcher_main() -> int:
    directory = Path(sys.argv[1])
    try:
        metadata = json.loads((directory / "profile.json").read_text())
        identity = Identity(metadata["private_key_path"], metadata["fingerprint"])
        target, command = _split_git_ssh(sys.argv[2:])
        # Git can invoke the launcher more than once within one operation.
        with tempfile.TemporaryDirectory(prefix="target-", dir=directory) as target_dir:
            config = materialize_configuration(target, identity, Path(target_dir))
            # This is Git's protocol pipe, not interactive user input.
            # BatchMode and the parent's prompt controls prohibit unlocking.
            result = subprocess.run(
                ["ssh", "-F", str(config), *target, *command],
                check=False,
            )
            return result.returncode
    except SSHError as error:
        (directory / "error").write_text(error.code, encoding="ascii")
        return 80
    except (FileNotFoundError, PermissionError):
        (directory / "error").write_text("ssh_tool_unavailable", encoding="ascii")
        return 80
    except (OSError, ValueError, KeyError):
        (directory / "error").write_text("ssh_config_unsupported", encoding="ascii")
        return 80


def has_controls(text: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in text)


def validate_private_path(value: str) -> Path:
    if os.name != "posix" or not isinstance(value, str) or has_controls(value):
        raise SSHError("ssh_key_invalid")
    try:
        if len(value.encode("utf-8")) > 4096 or not (value.startswith("/") or value.startswith("~/")):
            raise SSHError("ssh_key_invalid")
        path = Path(os.path.expanduser(value)).resolve(strict=True)
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR
                or not os.access(path, os.R_OK)):
            raise SSHError("ssh_key_invalid")
        with path.open("rb") as stream:
            if stream.read(64).splitlines()[0:1] != [b"-----BEGIN OPENSSH PRIVATE KEY-----"]:
                raise SSHError("ssh_key_invalid")
        return path
    except (OSError, RuntimeError, UnicodeError):
        raise SSHError("ssh_key_invalid") from None


def public_metadata(path: Path) -> tuple[str, str]:
    try:
        # keygen can prefer <path>.pub over the private file. A private
        # symlink reference without a sidecar forces inspection of this file.
        with tempfile.TemporaryDirectory(prefix="rag-key-inspection-") as directory:
            reference = Path(directory) / "identity"
            reference.symlink_to(path)
            result = subprocess.run(
                ["ssh-keygen", "-l", "-E", "sha256", "-f", str(reference)],
                check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=15, encoding="utf-8", errors="replace",
                env={**os.environ, "SSH_ASKPASS": "/usr/bin/false", "SSH_ASKPASS_REQUIRE": "never"},
            )
    except subprocess.TimeoutExpired:
        raise SSHError("ssh_key_timeout") from None
    except (FileNotFoundError, PermissionError):
        raise SSHError("ssh_tool_unavailable") from None
    except (OSError, subprocess.CalledProcessError):
        raise SSHError("ssh_key_invalid") from None
    match = re.fullmatch(
        r"[0-9]+ (SHA256:[A-Za-z0-9+/]{43}) [^\r\n]* \((ED25519|RSA|ECDSA|DSA|ED25519-SK|ECDSA-SK)\)\n?",
        result.stdout,
    )
    if match is None:
        raise SSHError("ssh_key_invalid")
    return match.group(1), match.group(2)


def inspect_private_key(value: str, fingerprint: Optional[str] = None) -> tuple[Path, str, str]:
    try:
        path = validate_private_path(value)
        actual, algorithm = public_metadata(path)
    except SSHError as error:
        if fingerprint is not None and error.code == "ssh_key_invalid":
            raise SSHError("ssh_key_unavailable") from None
        raise
    if fingerprint is not None and actual != fingerprint:
        raise SSHError("ssh_key_changed")
    return path, actual, algorithm


def file_identity(path: Path) -> Optional[tuple[int, int]]:
    try:
        info = path.stat()
        return info.st_dev, info.st_ino
    except FileNotFoundError:
        return None
    except OSError as error:
        logger.warning("Protected file identity unavailable (%s).", type(error).__name__)
        return None


class ProtectedFiles:
    """Canonical paths survive missing references; inode checks protect aliases."""

    def __init__(self, paths: Iterable[str] = ()):
        self.paths = set()
        for value in paths:
            path = Path(value)
            try:
                self.paths.add(path.resolve())
            except (OSError, RuntimeError) as error:
                logger.warning("Protected path resolution unavailable (%s).", type(error).__name__)
                self.paths.add(path.absolute())
        self.identities = {identity for path in self.paths
                           if (identity := file_identity(path)) is not None}

    def contains(self, path: Path) -> bool:
        try:
            if path.resolve() in self.paths or file_identity(path) in self.identities:
                return True
            if not path.is_file():
                return False
            with path.open("rb") as stream:
                header = stream.read(128)
            return any(marker in header for marker in (
                b"-----BEGIN OPENSSH PRIVATE KEY-----",
                b"-----BEGIN RSA PRIVATE KEY-----", b"-----BEGIN EC PRIVATE KEY-----",
                b"-----BEGIN DSA PRIVATE KEY-----", b"-----BEGIN PRIVATE KEY-----",
                b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
            ))
        except (OSError, RuntimeError):
            # Unreadable candidates cannot be safely returned or indexed.
            return True


if __name__ == "__main__":
    sys.exit(launcher_main())
