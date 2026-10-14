"""Tests de la tool BLOQUEANTE ``kali_run`` (STDLIB PURA, SIN importar el servidor).

Restricción dura del entorno: en el venv local hay ``mcp`` 2.x pero
``kali_mcp_server.py`` exige ``mcp<2`` (FastMCP 1.x), así que ``import kali_mcp_server``
FALLA. Por eso estos tests NO importan el servidor: validan (a) la LÓGICA bloqueante vía
``shell_exec.run_shell`` (que es lo que ``kali_run`` llama SINCRONAMENTE) y (b) la forma
del servidor leyendo su TEXTO fuente (open().read() + aserciones), nunca importándolo.

Siguen el patrón de ``tests/test_shell_exec.py``: ``sys.path.insert`` del padre, import de
``shell_exec``/``tool_streaming`` y monkeypatch de ``run_tool_streaming`` con un fake que
vuelca líneas al ring.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_PARENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PARENT))

import shell_exec  # noqa: E402
import tool_streaming  # noqa: E402

# Ruta del servidor cuyo TEXTO (no import) se inspecciona en los tests de forma.
_SERVER_PATH = _PARENT / "kali_mcp_server.py"


def _fake_runner(returncode=0, timed_out=False, cancelled=False, lines=None):
    """Fake de ``run_tool_streaming``: vuelca ``lines`` al ring y retorna el estado.

    ``lines`` es una lista de (stream, text) que se escribe al ring del ``execution_id``
    para que ``run_shell`` las recoja como salida del comando.
    """

    def _run(cmd, execution_id, timeout=0, env=None, cwd=None):
        for stream, text in lines or []:
            tool_streaming.append(execution_id, stream, text)
        return (returncode, timed_out, cancelled)

    return _run


def test_blocking_call_returns_full_output_in_one_call(monkeypatch):
    # kali_run es BLOQUEANTE: llama run_shell SINCRONAMENTE (con un execution_id uuid) y
    # obtiene el dict terminal con la salida completa del ring en UNA sola llamada.
    runner = _fake_runner(returncode=0, lines=[("stdout", "line-1"), ("stdout", "line-2")])
    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", runner)

    result = shell_exec.run_shell("nmap -sV localhost", uuid.uuid4().hex, 600)
    assert result["status"] == "done"
    assert result["returncode"] == 0
    assert "line-1" in result["output"]
    assert "line-2" in result["output"]


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_invalid_command_fails_without_running(monkeypatch, bad):
    called = {"ran": False}

    def _should_not_run(*args, **kwargs):
        called["ran"] = True
        return (0, False, False)

    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", _should_not_run)
    result = shell_exec.run_shell(bad, uuid.uuid4().hex, 600)
    assert result["status"] == "failed"
    assert result["returncode"] == -1
    assert called["ran"] is False  # fail-closed: no se ejecutó nada


def test_server_text_declares_kali_run_blocking_and_keeps_all_tools():
    # Se valida la FORMA del servidor por su TEXTO (NO import: mcp<2 no está en el venv).
    source = _SERVER_PATH.read_text(encoding="utf-8")

    # (1) kali_run existe y referencia el runner compartido run_shell.
    assert "def kali_run" in source
    assert "shell_exec.run_shell" in source

    # (2) kali_run aparece listada entre las tools de server_info.
    assert "KALI_RUN_TOOL_NAME" in source
    assert 'KALI_RUN_TOOL_NAME = "kali_run"' in source

    # (3) El cuerpo de kali_run es BLOQUEANTE: NO LLAMA a start_background (la mención
    # en el docstring explica justo que NO lo usa, por eso se busca la LLAMADA concreta).
    body_start = source.index("def kali_run")
    body = source[body_start : source.index("def _start_artifacts_http")]
    assert "tool_streaming.start_background(" not in body
    assert "shell_exec.run_shell" in body

    # (4) server_info SIGUE listando las tools existentes (no se quitó ninguna).
    for name in (
        "PROWLER_TOOL_NAME",
        "START_COLLECTION_TOOL_NAME",
        "IAM_ENUM_TOOL_NAME",
        "IAM_SIMULATE_TOOL_NAME",
        "KALI_EXEC_TOOL_NAME",
    ):
        assert name in source
