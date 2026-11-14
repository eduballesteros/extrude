"""Claves de IA desde el .env local (extrude serve en desarrollo)."""

from __future__ import annotations

from pathlib import Path

from extrude.local_env import find_env_file, load_env_file, parse_env

TEMPLATE = """# Comentario
ANTHROPIC_API_KEY=clave-de-prueba-anthropic
OPENAI_API_KEY=
export GEMINI_API_KEY="clave con comillas"
EXTRUDE_ANTHROPIC_MODEL=claude-sonnet-5-5  # más barato
PATH=/no/debe/tocarse
linea sin igual
"""


def test_parse_keeps_only_provider_keys_and_extrude_settings() -> None:
    assert parse_env(TEMPLATE) == {
        "ANTHROPIC_API_KEY": "clave-de-prueba-anthropic",
        "GEMINI_API_KEY": "clave con comillas",
        "EXTRUDE_ANTHROPIC_MODEL": "claude-sonnet-5-5",
    }


def test_environment_wins_and_empty_values_do_not_count(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(TEMPLATE, encoding="utf-8")
    env = {
        "ANTHROPIC_API_KEY": "del-sistema",
        "OPENAI_API_KEY": "del-sistema",
        "GEMINI_API_KEY": " ",
    }
    loaded = load_env_file(path, env)
    assert loaded == ["EXTRUDE_ANTHROPIC_MODEL", "GEMINI_API_KEY"]  # solo nombres, nunca valores
    assert env["ANTHROPIC_API_KEY"] == "del-sistema"
    assert env["OPENAI_API_KEY"] == "del-sistema"  # «OPENAI_API_KEY=» vacío no la borra
    assert env["GEMINI_API_KEY"] == "clave con comillas"
    assert "PATH" not in env


def test_bom_from_windows_editors_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=k\n", encoding="utf-8-sig")
    env: dict[str, str] = {}
    assert load_env_file(path, env) == ["ANTHROPIC_API_KEY"]


def test_found_from_a_subfolder_without_leaving_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pipeline" / "src").mkdir(parents=True)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=de-fuera\n", encoding="utf-8")
    assert find_env_file(repo / "pipeline" / "src") is None  # el de fuera del repo no cuenta
    (repo / ".env").write_text("ANTHROPIC_API_KEY=k\n", encoding="utf-8")
    assert find_env_file(repo / "pipeline" / "src") == (repo / ".env").resolve()


def test_outside_a_repository_only_the_current_folder_counts(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=k\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert find_env_file(tmp_path / "sub") is None
    assert find_env_file(tmp_path) == (tmp_path / ".env").resolve()
