"""Tests de regresion de la SELECCION DE PROVEEDOR LLM en el arranque del compose.

El servicio ooda-agent elige proveedor en su script de arranque (`sh -c`): por defecto OpenRouter
(fail-closed si falta la key) y, de forma opt-in, Ollama cuando OLLAMA_BASE_URL esta puesta. Esta
logica es config declarativa en docker-compose.yml (variables de entorno + el `command`), no codigo
Python del repo; estos tests afirman su CONTRATO parseando el compose para que no regrese en silencio.

Protegen:
  - OLLAMA_BASE_URL / OLLAMA_MODEL declaradas como OPCIONALES (forma ${...:-}) y OPENROUTER_API_KEY
    ya NO en la forma fail-closed `:?` a nivel compose.
  - el script ramifica en OLLAMA_BASE_URL (rama Ollama => model.provider / model.base_url) y la rama
    OpenRouter re-crea un guard fail-closed que sale != 0 si OPENROUTER_API_KEY esta vacia.
  - el aviso D1 cuando OLLAMA_MODEL esta vacio en modo Ollama.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_ooda_agent(compose_path: Path) -> dict:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    return compose["services"]["ooda-agent"]


def _startup_script(compose_path: Path) -> str:
    command = _load_ooda_agent(compose_path)["command"]
    return command[-1] if isinstance(command, list) else str(command)


# --- Entorno: proveedores opcionales, OpenRouter ya no fail-closed en compose ---


def test_ollama_env_vars_declared_optional(compose_path: Path) -> None:
    # Las variables de Ollama se declaran OPCIONALES (forma ${VAR:-}), nunca fail-closed.
    env = _load_ooda_agent(compose_path)["environment"]
    assert env["OLLAMA_BASE_URL"] == "${OLLAMA_BASE_URL:-}"
    assert env["OLLAMA_MODEL"] == "${OLLAMA_MODEL:-}"


def test_openrouter_api_key_not_fail_closed_in_compose(compose_path: Path) -> None:
    # OPENROUTER_API_KEY ya NO usa la forma fail-closed `:?` a nivel compose (para que el modo
    # Ollama pueda arrancar sin key). El default `:-` deja el guard fail-closed al script.
    env = _load_ooda_agent(compose_path)["environment"]
    assert env["OPENROUTER_API_KEY"] == "${OPENROUTER_API_KEY:-}"
    assert ":?" not in env["OPENROUTER_API_KEY"], "OPENROUTER_API_KEY no debe ser fail-closed en compose"


# --- Script de arranque: ramificacion de proveedor ---


def test_startup_branches_on_ollama_base_url(compose_path: Path) -> None:
    # Ramifica en OLLAMA_BASE_URL ($$ es el escape del compose para $ en el shell del contenedor).
    script = _startup_script(compose_path)
    assert 'if [ -n "$$OLLAMA_BASE_URL" ]; then' in script


def test_ollama_branch_sets_provider_and_base_url(compose_path: Path) -> None:
    # La rama Ollama apunta Hermes al endpoint OpenAI-compatible (provider custom + base_url).
    script = _startup_script(compose_path)
    assert "hermes config set model.provider custom" in script
    assert 'hermes config set model.base_url "$$OLLAMA_BASE_URL"' in script


def test_openrouter_branch_is_fail_closed(compose_path: Path) -> None:
    # La rama OpenRouter re-crea el guard fail-closed: key vacia => aborta con salida != 0.
    script = _startup_script(compose_path)
    assert 'if [ -z "$$OPENROUTER_API_KEY" ]; then' in script
    assert "exit 1" in script


def test_empty_ollama_model_warning_present(compose_path: Path) -> None:
    # Aviso D1: OLLAMA_BASE_URL puesta pero OLLAMA_MODEL vacia => warning NO fatal a stderr.
    script = _startup_script(compose_path)
    assert "OLLAMA_MODEL" in script
    # El aviso va a stderr (>&2) y NO aborta (no exit en esa rama).
    assert ">&2" in script
    assert "WARNING" in script
