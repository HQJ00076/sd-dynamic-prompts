import importlib.metadata
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sd_dynamic_prompts import version_tools as vt

COMMIT = "fe942beb3381570e4e5f903b9904413d57fcb409"
REPOSITORY = "https://github.com/HQJ00076/dynamicprompts.git"
GIT_REQUIREMENT = f"dynamicprompts[attentiongrabber,magicprompt] @ git+{REPOSITORY}@{COMMIT}"


def provenance(url=REPOSITORY, commit=COMMIT, **vcs_fields):
    return {"url": url, "vcs_info": {"vcs": "git", "commit_id": commit, **vcs_fields}}


@pytest.fixture
def installed_metadata(monkeypatch):
    version = Mock(return_value="0.31.0")
    distribution = Mock()
    distribution.return_value.read_text.return_value = json.dumps(provenance())
    monkeypatch.setattr(vt.importlib.metadata, "version", version)
    monkeypatch.setattr(vt.importlib.metadata, "distribution", distribution)
    return version, distribution.return_value.read_text


@pytest.mark.parametrize("requirement, satisfied", [
    ("dynamicprompts~=0.31.0", True),
    ("dynamicprompts>=0.32.0", False),
    ("send2trash==0.31.0", True),
    ("send2trash==2.1.0", False),
])
def test_index_requirements_keep_version_check(installed_metadata, requirement, satisfied):
    assert vt.get_install_result(requirement).correct is satisfied
    installed_metadata[1].assert_not_called()


def test_missing_distribution(installed_metadata):
    installed_metadata[0].side_effect = importlib.metadata.PackageNotFoundError("dynamicprompts")
    result = vt.get_install_result(GIT_REQUIREMENT)
    assert result.installed is None
    assert not result.correct
    installed_metadata[1].assert_not_called()


@pytest.mark.parametrize("contents", [None, "", "not json", "[]", "null", '{}',
    json.dumps(provenance(url="https://github.com/other/dynamicprompts.git")),
    json.dumps(provenance(commit="0" * 40, requested_revision=COMMIT)),
    json.dumps(provenance(commit="main", requested_revision=COMMIT)),
    json.dumps({"url": REPOSITORY, "vcs_info": []}),
    json.dumps({"url": REPOSITORY, "vcs_info": {"vcs": "hg", "commit_id": COMMIT}}),
])
def test_unverifiable_or_wrong_provenance(installed_metadata, contents):
    installed_metadata[1].return_value = contents
    result = vt.get_install_result(GIT_REQUIREMENT)
    assert not result.correct
    assert "resolved commit" in result.message


@pytest.mark.parametrize("url", [
    REPOSITORY, "https://github.com/HQJ00076/dynamicprompts",
    "https://github.com/HQJ00076/dynamicprompts.git/",
    "https://GITHUB.COM/hqj00076/DynamicPrompts/",
])
def test_equivalent_github_urls(installed_metadata, url):
    installed_metadata[1].return_value = json.dumps(provenance(url=url, requested_revision="old-branch"))
    assert vt.get_install_result(GIT_REQUIREMENT).correct


@pytest.mark.parametrize("url", [
    "http://github.com/HQJ00076/dynamicprompts.git",
    "https://github.com/HQJ00076/other.git",
    "https://github.com.evil.example/HQJ00076/dynamicprompts.git",
    "https://github.com/HQJ00076/dynamicprompts.git?different=1",
    "https://github.com/HQJ00076/dynamicprompts.git#subdirectory=other",
])
def test_non_equivalent_urls(installed_metadata, url):
    installed_metadata[1].return_value = json.dumps(provenance(url=url))
    assert not vt.get_install_result(GIT_REQUIREMENT).correct


def test_non_github_path_case_is_preserved():
    assert vt._normalize_repository_url("https://example.com/Owner/Repo.git") != (
        vt._normalize_repository_url("https://example.com/owner/repo.git")
    )


def test_sha_case_is_not_significant(installed_metadata):
    installed_metadata[1].return_value = json.dumps(provenance(commit=COMMIT.upper()))
    assert vt.get_install_result(GIT_REQUIREMENT).correct


@pytest.mark.parametrize("revision", ["main", "v0.31.0", COMMIT[:7]])
def test_mutable_or_short_revisions_are_rejected(revision):
    with pytest.raises(ValueError, match="full commit SHA"):
        vt.get_install_result(f"dynamicprompts @ git+{REPOSITORY}@{revision}")


def test_unsupported_direct_reference_is_rejected():
    with pytest.raises(ValueError, match="Git URL pinned"):
        vt.get_install_result("dynamicprompts @ https://example.com/package.whl")


def test_unreadable_metadata_is_unsatisfied(installed_metadata):
    installed_metadata[1].side_effect = OSError("unreadable")
    assert not vt.get_install_result(GIT_REQUIREMENT).correct


@pytest.fixture
def installer(monkeypatch):
    monkeypatch.setitem(sys.modules, "launch", SimpleNamespace(args=SimpleNamespace(skip_install=False)))
    check_call = Mock()
    monkeypatch.setattr(vt.subprocess, "check_call", check_call)
    return check_call


def result(requirement, direct_url=None, installed="0.31.0"):
    return vt.InstallResult(vt.Requirement(requirement), installed, direct_url)


def test_satisfied_git_dependency_does_not_run_pip(monkeypatch, installer):
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [result(GIT_REQUIREMENT, provenance())])
    vt.install_requirements()
    installer.assert_not_called()


def test_same_version_fallback_only_reinstalls_direct_package(monkeypatch, installer):
    wrong = result(GIT_REQUIREMENT)
    right = result(GIT_REQUIREMENT, provenance())
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [wrong, result("send2trash==2.1.0")])
    monkeypatch.setattr(vt, "get_install_result", Mock(side_effect=[wrong, right]))
    vt.install_requirements()
    assert installer.call_args_list[0].args[0] == [
        sys.executable, "-m", "pip", "install", GIT_REQUIREMENT, "send2trash==2.1.0",
    ]
    assert installer.call_args_list[1].args[0] == [
        sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", GIT_REQUIREMENT,
    ]


def test_normal_install_replaced_package_without_force(monkeypatch, installer):
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [result(GIT_REQUIREMENT)])
    monkeypatch.setattr(vt, "get_install_result", lambda _: result(GIT_REQUIREMENT, provenance()))
    vt.install_requirements()
    assert installer.call_count == 1


def test_installer_fails_if_reinstalled_provenance_still_wrong(monkeypatch, installer):
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [result(GIT_REQUIREMENT)])
    monkeypatch.setattr(vt, "get_install_result", lambda _: result(GIT_REQUIREMENT))
    with pytest.raises(RuntimeError, match="resolved commit"):
        vt.install_requirements()
    assert installer.call_count == 2


def test_pypi_install_command_unchanged(monkeypatch, installer):
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [result("send2trash==2.1.0")])
    vt.install_requirements()
    installer.assert_called_once_with([sys.executable, "-m", "pip", "install", "send2trash==2.1.0"])


def test_skip_install_is_respected(monkeypatch, installer):
    monkeypatch.setitem(sys.modules, "launch", SimpleNamespace(args=SimpleNamespace(skip_install=True)))
    vt.install_requirements()
    installer.assert_not_called()


def test_force_still_selects_satisfied_dependencies(monkeypatch, installer):
    monkeypatch.setattr(vt, "get_requirements_install_results", lambda: [result("send2trash==0.31.0")])
    vt.install_requirements(force=True)
    installer.assert_called_once_with([sys.executable, "-m", "pip", "install", "send2trash==0.31.0"])


def test_dynamicprompts_requirement_lookup(monkeypatch, installed_metadata):
    monkeypatch.setattr(vt, "get_requirements", lambda: ("send2trash==2.1.0", GIT_REQUIREMENT))
    assert vt.get_dynamicprompts_install_result().correct


def test_dependency_configuration_pins_fork_and_keeps_extras():
    requirements = vt.get_requirements()
    requirement = next(vt.Requirement(value) for value in requirements if value.startswith("dynamicprompts"))
    assert requirement.url == f"git+{REPOSITORY}@{COMMIT}"
    assert requirement.extras == {"attentiongrabber", "magicprompt"}
    assert "send2trash==2.1.0" in requirements


def test_previous_library_commit_is_not_satisfied(installed_metadata):
    installed_metadata[1].return_value = json.dumps(provenance(commit="5ad48daae5d5e5678e18c5a9cf3f38d68f75ddf6"))
    assert not vt.get_install_result(GIT_REQUIREMENT).correct
