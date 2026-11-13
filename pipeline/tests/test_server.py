from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ezdxf
import pytest
import yaml

from extrude.server import CONFIG_HEADER, MAX_JOBS, JobStore, create_server

CONFIGS = Path(__file__).parents[2] / "samples" / "config"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    srv = create_server("127.0.0.1", 0, jobs_dir=tmp_path / "jobs", max_mb=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def request(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = dict(headers or {})
    if data is not None:  # como el visor: los navegadores deben enviar octet-stream
        headers.setdefault("Content-Type", "application/octet-stream")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 (URL local)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL local del test)
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def post_dxf(base: str, path: Path, **headers: str) -> tuple[int, dict[str, Any]]:
    status, _, body = request(
        f"{base}/api/convert?name={path.name}", path.read_bytes(), {"Origin": ORIGIN, **headers}
    )
    return status, json.loads(body)


def test_health_and_cors(server: str) -> None:
    status, headers, body = request(f"{server}/api/health", headers={"Origin": ORIGIN})
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    _, other, _ = request(f"{server}/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in other


def test_preflight_allows_config_header(server: str) -> None:
    status, headers, _ = request(
        f"{server}/api/convert", headers={"Origin": ORIGIN}, method="OPTIONS"
    )
    assert status == 204
    assert CONFIG_HEADER in headers["Access-Control-Allow-Headers"]


def test_dxf_without_config_generates_draft(server: str, samples_dir: Path) -> None:
    status, payload = post_dxf(server, samples_dir / "block_2d.dxf")
    assert status == 200, payload
    assert payload["config_generated"] is True
    assert payload["report"]["layers"]["EDIFICIOS"]["heights"] == {"text": 4}
    code, _, glb = request(server + payload["files"]["glb"])
    assert code == 200
    assert glb[:4] == b"glTF"
    code, _, config = request(server + payload["files"]["config"])
    assert yaml.safe_load(config)["layers"][0]["match"] == "AGUA"


def test_dxf_with_config_header(server: str, samples_dir: Path) -> None:
    config = (CONFIGS / "nested_blocks.yaml").read_bytes()
    status, payload = post_dxf(
        server,
        samples_dir / "nested_blocks.dxf",
        **{CONFIG_HEADER: base64.b64encode(config).decode()},
    )
    assert status == 200, payload
    assert payload["config_generated"] is False
    assert payload["report"]["layers"]["VEGETACION"]["instanced"] == {"ARBOL": 20}


@pytest.mark.parametrize(
    ("name", "body", "headers", "status", "fragment"),
    [
        ("plano.pdf", b"%PDF", {}, 415, ".dwg o .dxf"),
        ("plano.dxf", b"0\nEOF\n", {CONFIG_HEADER: "@@no-base64@@"}, 400, "Base64"),
        ("roto.dxf", b"basura que no es dxf", {}, 422, "'roto.dxf' no es un DXF"),
        ("plano.dwg", b"no es un dwg", {}, 422, "DWG"),
        ("plano.dxf", b"x" * (1_048_576 + 1), {}, 413, "límite"),
    ],
)
def test_bad_inputs_get_actionable_errors(
    server: str, name: str, body: bytes, headers: dict[str, str], status: int, fragment: str
) -> None:
    code, _, raw = request(f"{server}/api/convert?name={name}", body, headers)
    payload = json.loads(raw)
    assert code == status, payload
    assert fragment in payload["error"] + payload["fix"]


def test_invalid_config_is_reported(server: str, samples_dir: Path) -> None:
    bad = base64.b64encode(b"layers:\n  - match: A\n    kind: castle\n").decode()
    status, payload = post_dxf(server, samples_dir / "block_2d.dxf", **{CONFIG_HEADER: bad})
    assert status == 422
    assert "kind" in payload["error"]


def test_malformed_yaml_header_is_an_input_error(server: str, samples_dir: Path) -> None:
    status, payload = post_dxf(
        server,
        samples_dir / "block_2d.dxf",
        **{CONFIG_HEADER: base64.b64encode(b"layers: [").decode()},
    )
    assert status == 422
    assert "YAML" in payload["error"]
    assert "line" in payload["error"] and "column" in payload["error"]
    assert "indentación" in payload["fix"]


@pytest.mark.parametrize("size", ["nan", "inf", "-inf"])
@pytest.mark.parametrize("mode", ["", "&async=1"])
def test_nonfinite_tile_sizes_are_rejected(server: str, size: str, mode: str) -> None:
    code, _, raw = request(f"{server}/api/convert?name=x.dxf&tiles={size}{mode}", b"invalid")
    assert code == 400
    assert "finito" in json.loads(raw)["error"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs/../../etc/passwd/x",
        "/api/jobs/zzzz/model.glb",
        "/api/jobs/" + "a" * 32 + "/secret.txt",
        "/api/jobs/" + "a" * 32 + "/tiles/../input.dxf",
        "/api/jobs/" + "a" * 32 + "/tiles/%2e%2e/input.dxf",
        "/api/jobs/" + "a" * 32 + "/tiles/tile_0_0.glb/..",
        "/api/jobs/" + "a" * 32 + "/tiles//tile_0_0.glb",
        "/api/jobs/" + "a" * 32 + "/tiles/tile_0_0.txt",
        "/api/jobs/" + "a" * 32 + "/out/model.glb",
        "/api/jobs/" + "a" * 32 + "/input.dxf",
    ],
)
def test_job_paths_are_validated(server: str, path: str) -> None:
    code, _, _ = request(server + path)
    assert code == 404


def test_tiles_job_serves_manifest_and_tiles(server: str, samples_dir: Path) -> None:
    config = (CONFIGS / "block_2d.yaml").read_bytes()
    status, _, body = request(
        f"{server}/api/convert?name=block_2d.dxf&tiles=25",
        (samples_dir / "block_2d.dxf").read_bytes(),
        {"Origin": ORIGIN, CONFIG_HEADER: base64.b64encode(config).decode()},
    )
    payload = json.loads(body)
    assert status == 200, payload
    assert payload["files"]["glb"] is None
    manifest_url = payload["files"]["manifest"]
    assert manifest_url == f"/api/jobs/{payload['job']}/manifest.json"

    status, headers, body = request(server + manifest_url, headers={"Origin": ORIGIN})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    manifest = json.loads(body)
    assert len(manifest["tiles"]) > 1
    for tile in manifest["tiles"]:
        assert tile["file"].startswith("tiles/tile_")
        status, headers, glb = request(f"{server}/api/jobs/{payload['job']}/{tile['file']}")
        assert status == 200
        assert headers["Content-Type"] == "model/gltf-binary"
        assert glb[:4] == b"glTF"
        assert len(glb) == tile["size_bytes"]

    missing = f"{server}/api/jobs/{payload['job']}/tiles/tile_99_99.glb"
    assert request(missing)[0] == 404


def test_job_store_rejects_traversal_even_with_existing_files(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job_id, job = store.create()
    (job / "out" / "tiles").mkdir(parents=True)
    (job / "out" / "tiles" / "tile_0_0.glb").write_bytes(b"glTF")
    (job / "input.dxf").write_text("secreto")
    found = store.file(job_id, "tiles/tile_0_0.glb")
    assert found is not None
    assert found[1] == "model/gltf-binary"
    for name in ("../input.dxf", "tiles/../../input.dxf", "/etc/passwd", "tiles\\tile_0_0.glb"):
        assert store.file(job_id, name) is None


def test_tiles_with_insertion_points_outside_block_are_downloadable(
    server: str,
    tmp_path: Path,
) -> None:
    doc = ezdxf.new()
    doc.units = 6
    block = doc.blocks.new("OFFSET")
    block.add_lwpolyline([(100, 100), (110, 100), (110, 110), (100, 110)], close=True)
    for x in (0, 100):
        doc.modelspace().add_blockref("OFFSET", (x, 0), dxfattribs={"layer": "E"})
    source = tmp_path / "offset.dxf"
    doc.saveas(source)
    config = yaml.safe_dump(
        {
            "instancing_min": 2,
            "layers": [
                {"match": "E", "kind": "building", "height": 9},
            ],
        }
    ).encode()
    status, _, body = request(
        f"{server}/api/convert?name=offset.dxf&tiles=50",
        source.read_bytes(),
        {"Origin": ORIGIN, CONFIG_HEADER: base64.b64encode(config).decode()},
    )
    payload = json.loads(body)
    assert status == 200, payload
    status, _, body = request(server + payload["files"]["manifest"])
    assert status == 200
    manifest = json.loads(body)
    assert {t["file"] for t in manifest["tiles"]} == {
        "tiles/tile_-2_-2.glb",
        "tiles/tile_0_-2.glb",
    }
    for tile in manifest["tiles"]:
        status, headers, glb = request(
            f"{server}/api/jobs/{payload['job']}/{tile['file']}",
            headers={"Origin": ORIGIN},
        )
        assert status == 200
        assert headers["Content-Type"] == "model/gltf-binary"
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert glb[:4] == b"glTF"
        assert len(glb) == tile["size_bytes"]


@pytest.mark.parametrize("indices", ["-2_0", "0_-2", "-2_-2", "1000000_-1000000"])
@pytest.mark.parametrize("revision", ["", ".rev-001", ".rev-1000"])
def test_job_store_serves_signed_pipeline_tile_indices(
    tmp_path: Path, indices: str, revision: str
) -> None:
    store = JobStore(tmp_path)
    job_id, job = store.create()
    name = f"tile_{indices}{revision}.glb"
    path = job / "out" / "tiles" / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b"glTF")
    assert store.file(job_id, f"tiles/{name}") == (path.resolve(), "model/gltf-binary")


@pytest.mark.parametrize("indices", ["--2_0", "+2_0", "2.0_0", "٢_0", "2_-", "2_0_extra"])
def test_job_store_rejects_malformed_existing_tiles(tmp_path: Path, indices: str) -> None:
    store = JobStore(tmp_path)
    job_id, job = store.create()
    path = job / "out" / "tiles" / f"tile_{indices}.glb"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"glTF")
    assert store.file(job_id, f"tiles/tile_{indices}.glb") is None


def test_old_jobs_are_pruned(tmp_path: Path, samples_dir: Path) -> None:
    from extrude.server import JobStore

    store = JobStore(tmp_path / "jobs")
    for _ in range(MAX_JOBS + 3):
        job_id, _path = store.create()
        store.finish(job_id)
    store.prune()
    assert len(list((tmp_path / "jobs").iterdir())) == MAX_JOBS


def test_job_being_created_is_never_pruned(tmp_path: Path) -> None:
    """Intercalación de la auditoría (H7), determinista: J existe pero aún no se ha encolado ni
    tiene estado cuando otros 10 trabajos terminan y se poda."""
    from extrude.server import JobStore

    store = JobStore(tmp_path / "jobs")
    job_j, path_j = store.create()  # carpeta creada; input y estado, todavía no
    for _ in range(MAX_JOBS):
        other, _path = store.create()
        store.update(other, state="done")
        store.finish(other)
    store.prune()
    assert path_j.is_dir()  # sigue ahí: estaba reservada desde create()
    assert store.active_count == 1

    store.finish(job_j)  # al terminar J, ya cuenta para la retención
    for _ in range(3):
        other, _path = store.create()
        store.finish(other)
    store.prune()
    assert not path_j.exists()  # el más antiguo de los terminados
    assert store.active_count == 0


def test_failed_write_does_not_leave_a_reserved_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from extrude import server as srv

    store = srv.JobStore(tmp_path / "jobs")

    def broken_write(self: Path, data: bytes) -> int:
        raise OSError("disco lleno")

    monkeypatch.setattr(Path, "write_bytes", broken_write)
    with pytest.raises(OSError):
        srv.start_job(store, b"0\nEOF\n", "plano.dxf")
    assert store.active_count == 0
    assert list((tmp_path / "jobs").iterdir()) == []
