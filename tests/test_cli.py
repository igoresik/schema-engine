import json

from typer.testing import CliRunner

from schema_engine import __version__
from schema_engine.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_samples_then_profile(tmp_path):
    result = runner.invoke(app, ["samples", str(tmp_path), "--rows", "12"])
    assert result.exit_code == 0, result.output
    csv_path = tmp_path / "dirty_orders.csv"
    assert csv_path.exists()

    result = runner.invoke(app, ["profile", str(csv_path), "--privacy-mode", "none", "--compact"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.splitlines()[0])
    assert data["privacy_mode"] == "none"
    assert "sample" not in data


def test_profile_xlsx_sheet_index(tmp_path):
    runner.invoke(app, ["samples", str(tmp_path), "--rows", "5"])
    result = runner.invoke(app, ["profile", str(tmp_path / "dirty_orders.xlsx"), "--sheet", "0"])
    assert result.exit_code == 0, result.output
    assert '"privacy_mode": "masked"' in result.stdout


def test_profile_unsupported_type(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{}")
    result = runner.invoke(app, ["profile", str(path)])
    assert result.exit_code == 2
