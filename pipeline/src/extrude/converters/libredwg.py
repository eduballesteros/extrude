"""Adaptador para dwg2dxf de GNU LibreDWG (open source; soporte de DWG recientes parcial)."""

from __future__ import annotations

import os
from pathlib import Path

from extrude.converters.base import STAGED_INPUT_NAME, STAGED_OUTPUT_NAME, DwgConverter


class LibreDwgConverter(DwgConverter):
    name = "dwg2dxf"
    env_var = "EXTRUDE_DWG2DXF_PATH"
    install_hint = (
        "Instala LibreDWG (Debian/Ubuntu: apt install libredwg-tools; macOS: brew install "
        "libredwg; Windows: binarios en https://github.com/LibreDWG/libredwg/releases) o "
        "indica la ruta de dwg2dxf en EXTRUDE_DWG2DXF_PATH."
    )

    def candidate_paths(self) -> list[Path]:
        # Windows: el .zip oficial no tiene instalador; ubicaciones habituales al descomprimirlo
        roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs", Path("C:/Program Files")]
        found = [
            exe
            for root in roots
            if root.is_dir()
            for exe in sorted(root.glob("libredwg*/dwg2dxf.exe"), reverse=True)
        ]
        return [*found, Path("/usr/bin/dwg2dxf"), Path("/usr/local/bin/dwg2dxf")]

    def build_command(self, executable: Path, in_dir: Path, out_dir: Path) -> list[str]:
        return [
            str(executable),
            "-y",
            "-o",
            str(out_dir / STAGED_OUTPUT_NAME),
            str(in_dir / STAGED_INPUT_NAME),
        ]
