"""Prueba HTTPS desde un cliente real. No llama a proveedores de IA."""

import argparse
import getpass
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("--user", help="Usuario de prueba; pide contraseña sin mostrarla")
    parser.add_argument("--other-user", help="Segundo usuario sin permisos sobre el proyecto")
    parser.add_argument("--drawing", type=Path, help="DXF de prueba; crea un proyecto persistente")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    if urlparse(origin).scheme != "https" or urlparse(origin).path:
        parser.error("Usa un origen HTTPS sin ruta")

    def call(path, token=None, data=None, content_type="application/json", expected=200):
        headers = {"Origin": origin}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(origin + path, data=data, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=65)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{path}: HTTP {response.status}"
            if path.startswith("/api/") and not path.endswith(".glb"):
                assert "application/json" in response.headers.get("Content-Type", ""), path
            return body

    def login(user):
        payload = json.dumps({"user": user, "password": getpass.getpass(f"Contraseña {user}: ")})
        return json.loads(call("/api/auth/login", data=payload.encode()))["token"]

    assert b"<html" in call("/").lower()
    assert b"<html" in call("/guia.html").lower()
    assert call("/draco/draco_decoder.wasm")[:4] == b"\x00asm"
    assert call("/models/block_2d.glb")[:4] == b"glTF"
    assert json.loads(call("/api/health"))["ok"]
    assert json.loads(call("/api/auth/me"))["mode"] == "shared"
    call("/api/projects", expected=401)
    if args.user:
        token = login(args.user)
        call("/api/no-existe", token, expected=404)
        if args.drawing:
            result = json.loads(
                call(
                    "/api/convert?name=smoke.dxf&async=1",
                    token,
                    args.drawing.read_bytes(),
                    "application/octet-stream",
                    202,
                )
            )
            path = result["status"]
            deadline = time.monotonic() + 900
            while True:
                result = json.loads(call(path, token))
                assert result["state"] != "failed", "La conversión de prueba falló"
                if result["state"] == "done":
                    break
                assert time.monotonic() < deadline, "La conversión no terminó en 15 minutos"
                time.sleep(1)
            job = result["job"]
            model = result["result"]["files"]["glb"]
            assert call(model, token)[:4] == b"glTF"
            for private in (
                "access.json",
                "job.json",
                "input.dxf",
                "../accounts/users.json",
            ):
                call(f"/api/jobs/{job}/{private}", token, expected=404)
            call(model, expected=401)
            if args.other_user:
                other = login(args.other_user)
                call(model, other, expected=404)
                call(path, other, expected=404)
                call("/api/auth/logout", other, b"{}")
            print(f"Proyecto de prueba creado: {job}; eliminar manualmente al cerrar la validación")
        call("/api/auth/logout", token, b"{}")
        call("/api/projects", token, expected=401)
    print("Comprobaciones HTTPS completadas (certificado verificado).")


if __name__ == "__main__":
    main()
