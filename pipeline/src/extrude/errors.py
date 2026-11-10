"""Errores de usuario: cada uno dice qué pasó y cómo arreglarlo."""


class ExtrudeError(Exception):
    """Error accionable mostrado al usuario sin traza."""

    def __init__(self, problem: str, fix: str) -> None:
        super().__init__(f"{problem}\n  → Cómo arreglarlo: {fix}")
        self.problem = problem
        self.fix = fix


class InputFileError(ExtrudeError):
    """El archivo de entrada no existe, no es legible o no tiene un formato válido."""


class ConverterNotFoundError(ExtrudeError):
    """No hay ningún conversor DWG→DXF disponible."""


class ConversionFailedError(ExtrudeError):
    """El conversor externo falló o no produjo salida."""
