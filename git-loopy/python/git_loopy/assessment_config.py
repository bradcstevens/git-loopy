"""Assessment policy is a startup snapshot, never repository content."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .settings import global_dir


REPOSITORY_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/"
    r"(?!gh[pousr]_|github_pat_)"
    r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,99})"
)
MILESTONE_PATTERN = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
KEYS = frozenset({
    "workflows", "app_id", "installation_id", "app_identity", "key_file",
})
OPTIONAL_KEYS = frozenset({"sandbox", "faults"})
PAGINATED_READS = frozenset({
    "Milestone catalog", "Counted checks", "Issue catalog", "Open pull requests",
    "Closed pull requests", "Baseline tag", "Line commit range",
})
SINGLE_READS = frozenset({"Trunk head", "VERSION at pinned SHA"})


@dataclass(frozen=True)
class Fault:
    read: str
    fault: str


class AssessmentConfigError(ValueError):
    """The service cannot start with this policy."""


def _check_macos_acl(fd: int) -> None:
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = libc.acl_get_fd_np(fd, 0x100)  # ACL_TYPE_EXTENDED from macOS sys/acl.h.
    if acl is None:
        # For an opened descriptor, Darwin's ENOENT means no extended ACL exists.
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise AssessmentConfigError("could not inspect App key file ACL")
    libc.acl_free(acl)
    raise AssessmentConfigError("App key file must have no extended ACL")


def read_app_key(path: Path) -> bytes:
    """Check the opened file, not a separate stat that could race a replacement."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as key:
            info = os.fstat(key.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise AssessmentConfigError("App key file must be a regular file")
            if os.name == "posix" and (
                info.st_uid != os.geteuid() or info.st_mode & 0o077
            ):
                raise AssessmentConfigError("App key file must be owner-only")
            if os.name == "posix" and not info.st_mode & stat.S_IRUSR:
                raise AssessmentConfigError("App key file is unreadable")
            if sys.platform == "darwin":
                _check_macos_acl(key.fileno())
            material = key.read(65537)
    except FileNotFoundError:
        raise AssessmentConfigError("App key file is missing") from None
    except OSError:
        raise AssessmentConfigError("App key file is unreadable") from None
    if not material or len(material) > 65536:
        raise AssessmentConfigError("App key file is empty or too large")
    return material


@dataclass(frozen=True)
class Policy:
    workflows: tuple[str, ...]
    app_id: str
    installation_id: str
    app_identity: str
    key_file: Path
    revision: str
    faults: tuple[Fault, ...] = ()


def load_policy(env: Mapping[str, str]) -> dict[str, Policy]:
    path = global_dir(env) / "assessment" / "config.toml"
    try:
        tables = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise AssessmentConfigError(f"cannot read assessment configuration: {exc}") from exc
    if not tables:
        raise AssessmentConfigError("assessment configuration has no repository tables")
    policies = {}
    for repository, table in tables.items():
        if (
            len(repository) > 140
            or re.fullmatch(REPOSITORY_PATTERN, repository) is None
            or not isinstance(table, dict)
        ):
            raise AssessmentConfigError("configuration must contain owner/name tables")
        if set(table) - KEYS - OPTIONAL_KEYS or KEYS - set(table):
            unknown = sorted(set(table) - KEYS - OPTIONAL_KEYS)
            missing = sorted(KEYS - set(table))
            raise AssessmentConfigError(
                f"unknown keys: {unknown}; missing keys: {missing}"
            )
        sandbox = table.get("sandbox", False)
        faults = table.get("faults", [])
        if not isinstance(sandbox, bool):
            raise AssessmentConfigError(f"{repository}: sandbox must be a boolean")
        if not isinstance(faults, list) or len(faults) > 100:
            raise AssessmentConfigError(f"{repository}: faults must be a bounded array")
        if faults and not sandbox:
            raise AssessmentConfigError(
                f"{repository}: fault entries require sandbox = true"
            )
        seen = set()
        for entry in faults:
            if not isinstance(entry, dict) or set(entry) != {"read", "fault"}:
                raise AssessmentConfigError(
                    f"{repository}: each fault entry requires only read and fault"
                )
            read, fault = entry["read"], entry["fault"]
            if (
                not isinstance(read, str) or len(read) > 100
                or not isinstance(fault, str)
                or fault not in {"rate-limited", "pagination-cut"}
            ):
                raise AssessmentConfigError(f"{repository}: invalid read or fault")
            paginated = read in PAGINATED_READS or re.fullmatch(
                r"Native blockers of issue #[1-9][0-9]{0,19}", read,
            ) is not None
            single = read in SINGLE_READS or re.fullmatch(
                r"Merge commit of pull request #[1-9][0-9]{0,19}", read,
            ) is not None
            if not (paginated or single) or (fault == "pagination-cut" and not paginated):
                raise AssessmentConfigError(f"{repository}: unsupported read for {fault}")
            if read in seen:
                raise AssessmentConfigError(f"{repository}: only one fault per read")
            seen.add(read)
        workflows = table["workflows"]
        if (
            not isinstance(workflows, list) or not workflows
            or not all(isinstance(w, str) and 0 < len(w) <= 200 for w in workflows)
            or len(workflows) > 100 or len(set(workflows)) != len(workflows)
        ):
            raise AssessmentConfigError("workflows must be unique, bounded names")
        for key in ("app_id", "installation_id"):
            if not isinstance(table[key], str) or not re.fullmatch(
                r"[1-9][0-9]{0,19}", table[key],
            ):
                raise AssessmentConfigError(f"{key} must be a positive numeric string")
        for key in ("app_identity", "key_file"):
            if not isinstance(table[key], str) or not 0 < len(table[key]) <= 4096:
                raise AssessmentConfigError(f"{key} must be a bounded nonempty string")
        canonical = json.dumps(table, sort_keys=True, separators=(",", ":"))
        policies[repository] = Policy(
            workflows=tuple(workflows), app_id=table["app_id"],
            installation_id=table["installation_id"],
            app_identity=table["app_identity"], key_file=Path(table["key_file"]),
            revision=hashlib.sha256(canonical.encode()).hexdigest(),
            faults=tuple(Fault(**entry) for entry in faults),
        )
    return policies
