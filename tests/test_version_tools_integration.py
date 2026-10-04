"""Opt-in network test: all pip commands run in a new, isolated temporary venv."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("DP_RUN_INSTALLER_INTEGRATION") != "1",
    reason="Set DP_RUN_INSTALLER_INTEGRATION=1 to allow downloads in a temporary venv",
)
def test_pypi_to_pinned_git_in_isolated_venv(tmp_path):
    environment = tmp_path / "installer-venv"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([
        str(python), "-B", "-m", "pip", "install", "--no-deps", "dynamicprompts==0.31.0",
    ], check=True)
    script = r'''
import importlib.metadata
import json
import sys

sys.path.insert(0, sys.argv[1])
from sd_dynamic_prompts import version_tools as vt

commit = "fe942beb3381570e4e5f903b9904413d57fcb409"
requirement = "dynamicprompts @ git+https://github.com/HQJ00076/dynamicprompts.git@" + commit
assert importlib.metadata.version("dynamicprompts") == "0.31.0"
assert importlib.metadata.distribution("dynamicprompts").read_text("direct_url.json") is None
assert not vt.get_install_result(requirement).correct

# Test the actual installer without heavyweight magicprompt/torch extras.
# Unit tests cover preserving the exact extras requirement passed to pip.
vt.get_requirements = lambda: (requirement,)
vt.install_requirements()
assert vt.get_install_result(requirement).correct
assert importlib.metadata.version("dynamicprompts") == "0.31.0"
origin = json.loads(importlib.metadata.distribution("dynamicprompts").read_text("direct_url.json"))
assert origin["vcs_info"]["commit_id"] == commit
from dynamicprompts.wildcards.collection.text_file import WildcardTextFile
assert WildcardTextFile._parse_line("0.5::blue").weight == 0.5

# A second installer run must not invoke pip.
vt.subprocess.check_call = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected reinstall"))
vt.install_requirements()
print("Verified PyPI -> pinned fork -> no-op restart in " + sys.prefix)
'''
    subprocess.run([
        str(python), "-B", "-c", script, str(Path(__file__).resolve().parents[1]),
    ], check=True)
