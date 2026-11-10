"""Adaptador para ODA File Converter (gratuito, requiere registro en opendesign.com)."""

from __future__ import annotations

from pathlib import Path

from extrude.converters.base import DwgConverter

ODA_OUTPUT_VERSION = "ACAD2018"


class OdaFileConverter(DwgConverter):
    name = "ODAFileConverter"
    env_var = "EXTRUDE_ODA_PATH"
    install_hint = (
        "Descarga ODA File Converter desde https://www.opendesign.com/guestfiles/"
        "oda_file_converter e instálalo; o indica su ruta en EXTRUDE_ODA_PATH."
    )

    def candidate_paths(self) -> list[Path]:
        candidates: list[Path] = []
        for base in (Path("C:/Program Files/ODA"), Path("C:/Program Files (x86)/ODA")):
            if base.is_dir():
                candidates.extend(sorted(base.glob("ODAFileConverter*/ODAFileConverter.exe")))
        candidates.append(Path("/usr/bin/ODAFileConverter"))
        candidates.append(
            Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
        )
        return candidates

    def build_command(self, executable: Path, in_dir: Path, out_dir: Path) -> list[str]:
        # Sintaxis: <in> <out> <versión> <tipo> <recursivo> <auditar> [filtro]
        return [
            str(executable),
            str(in_dir),
            str(out_dir),
            ODA_OUTPUT_VERSION,
            "DXF",
            "0",
            "1",
            "*.DWG",
        ]
