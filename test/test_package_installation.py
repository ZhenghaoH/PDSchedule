"""Build and inspect the minimal distribution without GPU or package-index access."""

import email.parser
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import venv
import zipfile

import pytest
from packaging.requirements import Requirement

from pdschedule import __version__
from pdschedule import paths
from pdschedule.resources import controller_template_dir


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.packaging


def run(command, **kwargs):
    env = os.environ.copy()
    env.pop("PDSCHEDULE_PROFILE_DIR", None)
    env.update(PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, **kwargs,
    )
    del env  # Do not include inherited environment values in assertion diagnostics.
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    output = tmp_path_factory.mktemp("package-dist")
    run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output), str(ROOT)])
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def test_metadata_has_one_runtime_dependency_and_version_source():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "pdschedule"
    assert config["project"]["authors"] == [{"name": "xxx"}]
    assert config["project"]["requires-python"] == ">=3.12,<3.14"
    dynamic = config["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"]["attr"] == "pdschedule.__version__"
    assert dynamic["dependencies"]["file"] == ["requirements.txt"]
    assert __version__ == "0.1.0"
    packages = config["tool"]["setuptools"]["packages"]
    assert "profile" not in packages
    assert not {"plan", "simulator", "test", "doc"}.intersection(packages)
    assert "instance.simulator" in packages


def test_generated_state_is_separate_from_installed_resources(tmp_path, monkeypatch):
    monkeypatch.delenv("PDSCHEDULE_PROFILE_DIR", raising=False)
    assert paths.profile_root() == ROOT / "profile"
    assert (controller_template_dir() / "requests.html").is_file()
    monkeypatch.setattr(paths, "source_root", lambda: None)
    monkeypatch.chdir(tmp_path)
    assert paths.profile_root() == tmp_path / "profile"
    monkeypatch.setenv("PDSCHEDULE_PROFILE_DIR", str(tmp_path / "custom-profile"))
    assert paths.profile_root() == tmp_path / "custom-profile"


def test_wheel_and_sdist_scope_and_metadata(artifacts):
    wheel, sdist = artifacts
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = email.parser.BytesParser().parsebytes(
            archive.read(next(name for name in names if name.endswith(".dist-info/METADATA")))
        )
    assert metadata["Version"] == __version__
    assert metadata["License-Expression"] == "Apache-2.0"
    expected = {
        str(Requirement(line)) for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {str(Requirement(line)) for line in metadata.get_all("Requires-Dist")} == expected
    required = {
        "pdschedule/__init__.py", "pdschedule/paths.py",
        "pdschedule/profiling/TTFT_predictor/prefill_predictor.py",
        "pdschedule/profiling/TPOT_predictor/TPOT_predictor.py",
        "pdschedule/resources/templates/requests.html",
        "examples/clientUI/templates/index.html", "instance/simulator/mock_resp.py",
    }
    assert required <= names
    forbidden = {"plan", "simulator", "profile", "doc", "docs", "test", "templates"}
    assert not {name.split("/")[0] for name in names}.intersection(forbidden)
    with tarfile.open(sdist) as archive:
        source_names = {name.partition("/")[2] for name in archive.getnames()}
    assert {"pyproject.toml", "requirements.txt", "templates/requests.html"} <= source_names
    assert {"doc/installation.md", "doc/quickstart_v1.md", "env/README.md"} <= source_names
    assert {name for name in source_names if name.startswith("doc/")} <= {
        "doc/", "doc/installation.md", "doc/quickstart_v1.md"
    }
    assert not any(name.startswith("docs/") for name in source_names)
    for members in (names, source_names):
        assert not any(name.startswith(("plan/", "simulator/", "test/")) for name in members)
        assert not any(name.endswith((".pkl", ".pickle", ".joblib", ".sh", ".ps1", ".cmd", ".pyc", ".log", ".sqlite3")) for name in members)


def test_clean_wheel_identity_resources_and_stdlib_profile(artifacts, tmp_path_factory):
    wheel, _ = artifacts
    # Avoid Windows' legacy path limit when the checkout itself has a long path.
    tmp_path = tmp_path_factory.mktemp("wi")
    environment = tmp_path / "v"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([sys.executable, "-m", "pip", "--python", str(python), "install", "--no-deps", "--no-index", str(wheel)])
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run([str(python), "-I", "-c", """
from importlib.metadata import version
from pathlib import Path
import sys, sysconfig
import pdschedule
from pdschedule.paths import source_root, profile_root
from pdschedule.resources import controller_template_dir
import config.controller as controller
import profile, cProfile
assert pdschedule.__version__ == version('pdschedule') == '0.1.0'
assert Path(pdschedule.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert source_root() is None
assert profile_root() == Path.cwd() / 'profile'
assert Path(profile.__file__).resolve().parent == Path(sysconfig.get_path('stdlib')).resolve()
cProfile.Profile()
assert (controller_template_dir() / 'requests.html').is_file()
assert Path(controller.TEMPLATES_PATH) == controller_template_dir()
assert not any(name in sys.modules for name in ('torch', 'vllm', 'nixl', 'lmcache'))
print('clean wheel: identity, template, profile, and stdlib imports passed')
"""], cwd=outside)
    assert "clean wheel:" in result.stdout
