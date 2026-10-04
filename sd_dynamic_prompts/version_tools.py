# NB: this file may not import anything from `sd_dynamic_prompts` because it is used by `install.py`.

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import logging
import re
import shlex
import subprocess
import sys
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import tomllib as tomli  # Python 3.11+
except ImportError:
    try:
        import tomli  # may have been installed already
    except ImportError:
        try:
            # pip has had this since version 21.2
            from pip._vendor import tomli
        except ImportError:
            raise ImportError(
                "A TOML library is required to install sd-dynamic-prompts, "
                "but could not be imported. "
                "Please install tomli (pip install tomli) and try again.",
            ) from None

try:
    from packaging.requirements import Requirement
except ImportError:
    # pip has had this since 2018
    from pip._vendor.packaging.requirements import (  # type: ignore[assignment]
        Requirement,
    )

logger = logging.getLogger(__name__)


def _normalize_repository_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("Repository URLs must not contain credentials, queries or fragments")
    path = parts.path.rstrip("/")
    if parts.hostname and parts.hostname.lower() == "github.com":
        path = path.lower()
    path = path.removesuffix(".git")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _git_requirement_source(url: str) -> tuple[str, str]:
    """Only immutable Git references are supported as direct dependencies."""
    if not url.startswith("git+"):
        raise ValueError("Direct dependencies must use a Git URL pinned to a full commit SHA")
    parts = urlsplit(url[4:])
    path, separator, commit = parts.path.rpartition("@")
    if not separator or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
        raise ValueError("Git dependencies must be pinned to a full commit SHA, not a branch or tag")
    repository = urlunsplit(parts._replace(path=path))
    return _normalize_repository_url(repository), commit.lower()


def _direct_url_matches(requirement_url: str, direct_url: dict | None) -> bool:
    repository, commit = _git_requirement_source(requirement_url)
    if not isinstance(direct_url, dict):
        return False
    vcs_info = direct_url.get("vcs_info")
    source_url = direct_url.get("url")
    if not isinstance(vcs_info, dict) or not isinstance(source_url, str):
        return False
    commit_id = vcs_info.get("commit_id")
    if vcs_info.get("vcs") != "git" or not isinstance(commit_id, str):
        return False
    try:
        return (
            _normalize_repository_url(source_url) == repository
            and commit_id.lower() == commit
        )
    except ValueError:
        return False


@dataclasses.dataclass
class InstallResult:
    requirement: Requirement
    installed: str | None
    direct_url: dict | None = None

    @property
    def message(self) -> str | None:
        if self.correct:
            return None
        if self.requirement.url:
            return (
                f"{self.requirement.name} version {self.installed or 'not installed'} "
                f"does not have the required Git repository and resolved commit: "
                f"{self.requirement.url}. Install metadata is missing, invalid, or does not match. "
                f"Please run `install.py` from the extension directory, or `{self.pip_install_command}`."
            )
        return (
            f"You have {self.requirement.name} version {self.installed or 'not'} installed, "
            f"but this extension requires version {self.requirement.specifier}. "
            f"Please run `install.py` from the sd-dynamic-prompts extension directory, "
            f"or `{self.pip_install_command}`."
        )

    @property
    def specifier_str(self) -> str:
        return str(self.requirement)

    @property
    def correct(self) -> bool:
        if self.requirement.url:
            return bool(self.installed and _direct_url_matches(self.requirement.url, self.direct_url))
        return bool(
            self.installed and self.requirement.specifier.contains(self.installed),
        )

    @property
    def pip_install_command(self) -> str:
        if self.requirement.url:
            return f'pip install --force-reinstall --no-deps "{self.specifier_str}"'
        return f"pip install {self.specifier_str}"

    def raise_if_incorrect(self) -> None:
        message = self.message
        if message:
            raise RuntimeError(message)


@lru_cache(maxsize=1)
def get_requirements() -> tuple[str, ...]:
    toml_text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    deps = tomli.loads(toml_text)["project"]["dependencies"]
    return tuple(str(dep) for dep in deps)


def get_install_result(req_str: str) -> InstallResult:
    req = Requirement(req_str)
    if req.url:
        _git_requirement_source(req.url)  # Reject mutable or unsupported references before invoking pip.
    try:
        installed_version = importlib.metadata.version(req.name)
    except ImportError:
        installed_version = None
    direct_url = None
    if req.url and installed_version:
        try:
            contents = importlib.metadata.distribution(req.name).read_text("direct_url.json")
            direct_url = json.loads(contents) if contents else None
        except (importlib.metadata.PackageNotFoundError, OSError, ValueError, UnicodeError):
            pass  # Unverifiable provenance is never satisfied.
    res = InstallResult(requirement=req, installed=installed_version, direct_url=direct_url)
    return res


def get_requirements_install_results() -> Iterable[InstallResult]:
    """
    Get InstallResult objects for all requirements.
    """
    return (get_install_result(req_str) for req_str in get_requirements())


def get_dynamicprompts_install_result() -> InstallResult:
    """
    Get the InstallResult for the dynamicprompts requirement.
    """
    for req in get_requirements():
        if req.startswith("dynamicprompts"):
            return get_install_result(req)
    raise RuntimeError("dynamicprompts requirement not found")


def install_requirements(force=False) -> None:
    """
    Invoke pip to install the requirements for the extension.
    """
    try:
        from launch import args

        if getattr(args, "skip_install", False):
            logger.info(
                "webui launch.args.skip_install is true, skipping dynamicprompts installation",
            )
            return
    except ImportError:
        pass

    results_to_install = [
        ires
        for ires in get_requirements_install_results()
        if (force or not ires.correct)
    ]

    if not results_to_install:
        return

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        *(str(ires.requirement) for ires in results_to_install),
    ]
    print(f"sd-dynamic-prompts installer: running {shlex.join(command)}")
    subprocess.check_call(command)

    # Resolve extras/dependencies normally first. If pip kept a same-version package,
    # replace only that direct dependency, without reinstalling its dependencies.
    for result in results_to_install:
        if not result.requirement.url:
            continue
        requirement = str(result.requirement)
        if not get_install_result(requirement).correct:
            command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                requirement,
            ]
            print(f"sd-dynamic-prompts installer: running {shlex.join(command)}")
            subprocess.check_call(command)
        get_install_result(requirement).raise_if_incorrect()


def selftest() -> None:
    for res in get_requirements_install_results():
        print("[OK]" if res.correct else "????", res.requirement, res)


if __name__ == "__main__":
    selftest()
