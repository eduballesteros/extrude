from __future__ import annotations

from pathlib import Path

import pytest
from sample_dxf import write_samples


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("samples")
    write_samples(out)
    return out
