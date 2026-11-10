from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from extrude import converters
from extrude.cli import app
from extrude.converters import base, ensure_dxf
from extrude.converters.libredwg import LibreDwgConverter
from extrude.converters.oda import OdaFileConverter
from extrude.errors import ConversionFailedError, ConverterNotFoundError, InputFileError


@pytest.fixture
def no_converters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.DwgConverter, "locate", lambda self: None)


@pytest.fixture
def fake_dwg(tmp_path: Path) -> Path:
    path = tmp_path / "plano con espacios; rm -rf.dwg"
    path.write_bytes(b"AC1032" + b"\x00" * 64)
    return path


def test_dxf_is_passed_through(tmp_path: Path) -> None:
    dxf = tmp_path / "a.dxf"
    dxf.write_text("0\nEOF\n")
    assert ensure_dxf(dxf, tmp_path / "work") == dxf


def test_unknown_extension_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputFileError, match="Extensión no soportada"):
        ensure_dxf(tmp_path / "a.pdf", tmp_path)


def test_missing_converter_gives_actionable_error(fake_dwg: Path, no_converters: None) -> None:
    with pytest.raises(ConverterNotFoundError) as info:
        ensure_dxf(fake_dwg, fake_dwg.parent / "work")
    assert "SAVEAS" in info.value.fix
    assert "ODA" in info.value.fix


def test_invalid_dwg_header_is_rejected(tmp_path: Path) -> None:
    bogus = tmp_path / "x.dwg"
    bogus.write_bytes(b"MZ\x90\x00 not a dwg")
    with pytest.raises(InputFileError, match="no parece un DWG"):
        base.stage_input(bogus, tmp_path / "work")


def test_staging_uses_fixed_name(fake_dwg: Path, tmp_path: Path) -> None:
    in_dir, out_dir = base.stage_input(fake_dwg, tmp_path / "work")
    assert [p.name for p in in_dir.iterdir()] == [base.STAGED_INPUT_NAME]
    assert out_dir.is_dir()


def test_oda_command_has_no_user_filename(tmp_path: Path) -> None:
    cmd = OdaFileConverter().build_command(Path("oda.exe"), tmp_path / "in", tmp_path / "out")
    assert cmd[3:] == ["ACAD2018", "DXF", "0", "1", "*.DWG"]


def test_env_var_overrides_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "dwg2dxf.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("EXTRUDE_DWG2DXF_PATH", str(exe))
    assert LibreDwgConverter().locate() == exe


def test_convert_runs_tool_and_returns_dxf(
    fake_dwg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "dwg2dxf.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("EXTRUDE_DWG2DXF_PATH", str(exe))
    monkeypatch.setattr(OdaFileConverter, "locate", lambda self: None)
    seen: list[list[str]] = []

    def fake_run(command: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        Path(command[3]).write_text("0\nEOF\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(base, "run_tool", fake_run)
    out = converters.ensure_dxf(fake_dwg, tmp_path / "work")
    assert out.name == base.STAGED_OUTPUT_NAME
    assert all("rm -rf" not in arg for arg in seen[0])


def test_convert_without_output_fails_clearly(
    fake_dwg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LibreDwgConverter, "locate", lambda self: Path("dwg2dxf"))
    monkeypatch.setattr(
        base, "run_tool", lambda c, t: subprocess.CompletedProcess(c, 1, "", "boom")
    )
    with pytest.raises(ConversionFailedError, match="boom"):
        LibreDwgConverter().convert(fake_dwg, tmp_path / "work")


def test_doctor_reports_converters(no_converters: None) -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "ODAFileConverter: no encontrado" in result.stdout
    assert "passthrough" in result.stdout
    assert "SAVEAS" in result.stdout
