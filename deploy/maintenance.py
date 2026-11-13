"""Copia consistente con los servicios detenidos; restauración en volúmenes vacíos."""

import os
import sys
import tarfile
from pathlib import Path
from typing import BinaryIO


def backup(root: Path, stream: BinaryIO) -> None:
    with tarfile.open(fileobj=stream, mode="w|gz") as archive:
        for name in ("accounts", "jobs"):
            archive.add(root / name, arcname=name)


def restore(root: Path, stream: BinaryIO) -> None:
    if any(any((root / name).iterdir()) for name in ("accounts", "jobs")):
        raise SystemExit("Restaurar requiere volúmenes vacíos; no se sobrescriben datos")
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            if member.name.split("/")[0] not in ("accounts", "jobs"):
                raise SystemExit("Ruta ajena a los datos de Extrude")
            archive.extract(member, root, filter="data")


def main() -> None:
    os.umask(0o077)
    root = Path("/data")
    if sys.argv[1:] == ["backup"]:
        backup(root, sys.stdout.buffer)
    elif sys.argv[1:] == ["restore"]:
        restore(root, sys.stdin.buffer)
    else:
        raise SystemExit("Uso: maintenance.py backup|restore")


if __name__ == "__main__":
    main()
