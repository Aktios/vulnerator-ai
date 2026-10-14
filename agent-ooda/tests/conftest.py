"""Fixtures de los tests declarativos de agent-ooda (perfil Hermes OODA directo a Kali, FEAT-003).

Resuelven las rutas de los ficheros a parsear RELATIVAS a la raíz del worktree (dos niveles
por encima de este fichero: agent-ooda/tests/ -> raíz), para que `pytest agent-ooda/tests/`
funcione desde la raíz del repo sin depender del cwd.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# agent-ooda/tests/conftest.py -> parents[2] = raíz del worktree.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def profile_config_path(repo_root: Path) -> Path:
    return repo_root / "agent-ooda" / "profile" / "config.yaml"


@pytest.fixture(scope="session")
def compose_path(repo_root: Path) -> Path:
    return repo_root / "docker-compose.yml"
