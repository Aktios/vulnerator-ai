"""Tests stdlib-only del runner de SHELL CRUDA (`shell_exec.py`).

Ejercitan la LÓGICA del runner SIN ejecutar comandos reales (``run_tool_streaming`` se
sustituye por un fake). Fijan que:
  - ``run_shell`` con un runner de fixture devuelve ``done`` con ``output`` = salida del ring;
  - un ``command`` inválido (no-string / vacío) -> ``failed`` SIN ejecutar nada (fail-closed);
  - timeout -> ``failed`` con diagnóstico; cancelación -> ``cancelled``;
  - un ``returncode`` != 0 es ``done`` (resultado legítimo del comando, no fallo del runner);
  - el comando se pasa a ``sh -c`` verbatim (shell cruda, no se restringe el contenido).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shell_exec  # noqa: E402
import tool_streaming  # noqa: E402


def _fake_runner(returncode=0, timed_out=False, cancelled=False, lines=None):
    """Fake de ``run_tool_streaming``: registra el cmd, vuelca ``lines`` al ring y retorna.

    ``lines`` es una lista de (stream, text) que se escribe al ring del ``execution_id`` para
    que ``_ring_output`` las recoja como salida del comando.
    """
    captured = {"cmd": None}

    def _run(cmd, execution_id, timeout=0, env=None, cwd=None):
        captured["cmd"] = cmd
        for stream, text in lines or []:
            tool_streaming.append(execution_id, stream, text)
        return (returncode, timed_out, cancelled)

    return _run, captured


def test_run_shell_passes_command_to_sh_c_verbatim(monkeypatch):
    runner, captured = _fake_runner(lines=[("stdout", "hello")])
    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", runner)

    result = shell_exec.run_shell("echo hello | tr a-z A-Z", "exec-sh-1")
    assert result["status"] == "done"
    assert result["returncode"] == 0
    # Shell cruda: el comando va TAL CUAL a `sh -c` (no se restringe pipes/encadenado).
    assert captured["cmd"] == ["sh", "-c", "echo hello | tr a-z A-Z"]
    # output = salida acumulada del ring (verbatim).
    assert "hello" in result["output"]


def test_run_shell_nonzero_returncode_is_done_not_failed(monkeypatch):
    # Un comando que termina con rc!=0 (p. ej. grep sin match) NO es fallo del runner.
    runner, _ = _fake_runner(returncode=1, lines=[("stderr", "no match")])
    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", runner)

    result = shell_exec.run_shell("grep nope /etc/hosts", "exec-sh-rc")
    assert result["status"] == "done"
    assert result["returncode"] == 1
    assert "no match" in result["output"]


@pytest.mark.parametrize("bad", ["", "   ", None, 123, ["ls"]])
def test_run_shell_rejects_invalid_command_without_running(monkeypatch, bad):
    called = {"ran": False}

    def _should_not_run(*args, **kwargs):
        called["ran"] = True
        return (0, False, False)

    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", _should_not_run)
    result = shell_exec.run_shell(bad, "exec-sh-bad")
    assert result["status"] == "failed"
    assert called["ran"] is False  # fail-closed: no se lanzó `sh -c`
    assert "command inválido" in result["error"]


def test_run_shell_timeout_reports_failed(monkeypatch):
    runner, _ = _fake_runner(returncode=-1, timed_out=True)
    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", runner)

    result = shell_exec.run_shell("sleep 999", "exec-sh-timeout", timeout=5)
    assert result["status"] == "failed"
    assert result["timed_out"] is True
    assert "timeout" in result["error"].lower()


def test_run_shell_cancelled(monkeypatch):
    runner, _ = _fake_runner(returncode=-1, cancelled=True)
    monkeypatch.setattr(shell_exec.tool_streaming, "run_tool_streaming", runner)

    result = shell_exec.run_shell("sleep 999", "exec-sh-cancel")
    assert result["status"] == "cancelled"
    assert result["cancelled"] is True
