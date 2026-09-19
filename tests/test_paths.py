from pathlib import Path

from jobpilot.paths import jobpilot_home


def test_home_uses_env_var_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    assert jobpilot_home() == tmp_path


def test_home_falls_back_to_package_root_not_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("JOBPILOT_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    home = jobpilot_home()
    assert home != tmp_path
    assert (home / "src" / "jobpilot").is_dir()
