"""Tests del cliente :8642 y el parser SSE (`docker/mcp/hermes_api_client.py`).

NO importan `mcp`/FastMCP: solo el módulo SDK-free `hermes_api_client` + httpx + pytest
(mismo patrón que `docker/kali/tests/test_scope_guard.py`). Cubren:
  - El parser SSE puro: wire `: open`/`id:`/`data:` (incl. multilínea), cursor por `seq`,
    dedup entre páginas, skip best-effort de eventos malformados, y los topes de batch
    (max_events, presupuesto de bytes, wait_seconds) + corte en el evento terminal.
  - HermesApiClient con httpx.MockTransport: Bearer puesto, Idempotency-Key solo cuando se
    da, y el mapeo de cada forma de error (sobre JSON, non-2xx sin sobre, fallo de conexión).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

# El módulo bajo prueba vive en docker/mcp/ (el servidor MCP NO se importa: arrastra `mcp`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hermes_api_client as hac  # noqa: E402


# ------------------------------------------------------------------ parser SSE


def _sse_message(seq: int, event: str = "message.delta", **extra) -> str:
    """Construye un mensaje SSE `id:`+`data:` con un evento JSON de `seq`/`event`."""
    payload = {"event": event, "run_id": "run_x", "timestamp": 1.0, "seq": seq}
    payload.update(extra)
    return "id: %d\ndata: %s\n\n" % (seq, json.dumps(payload))


def _lines(raw: str):
    """Itera líneas como lo haría httpx.Response.iter_lines (sin el '\\n' final)."""
    return iter(raw.split("\n"))


def test_parser_basic_wire_and_cursor():
    raw = ": open\n\n" + _sse_message(1) + _sse_message(2) + _sse_message(3)
    events = list(hac.parse_sse_events(_lines(raw)))
    assert [e["seq"] for e in events] == [1, 2, 3]
    # Opaco: el dict de evento se entrega sin tocar.
    assert events[0]["event"] == "message.delta"
    assert events[0]["run_id"] == "run_x"


def test_parser_multiline_data():
    # SSE permite `data:` multilínea: cada línea `data:` se une con '\n'. Se usa un JSON
    # pretty-printed (los '\n' entre elementos estructurales son JSON válido tras el join).
    payload = {"event": "message.interim", "run_id": "run_x", "timestamp": 1.0, "seq": 5,
               "text": "a"}
    dumped = json.dumps(payload, indent=2)  # multilínea, válido
    data_lines = "".join("data: %s\n" % ln for ln in dumped.split("\n"))
    raw = "id: 5\n" + data_lines + "\n"
    events = list(hac.parse_sse_events(_lines(raw)))
    assert len(events) == 1 and events[0]["seq"] == 5 and events[0]["text"] == "a"


def test_parser_dedup_by_after_seq():
    raw = _sse_message(1) + _sse_message(2) + _sse_message(3)
    events = list(hac.parse_sse_events(_lines(raw), after_seq=2))
    assert [e["seq"] for e in events] == [3]


def test_parser_skips_malformed_and_missing_seq():
    good = _sse_message(1)
    bad_json = "id: 2\ndata: {not json}\n\n"
    no_seq = "id: 3\ndata: %s\n\n" % json.dumps({"event": "x", "run_id": "r"})
    good2 = _sse_message(4)
    events = list(hac.parse_sse_events(_lines(good + bad_json + no_seq + good2)))
    assert [e["seq"] for e in events] == [1, 4]


def test_parser_stops_on_terminal_event():
    raw = _sse_message(1) + _sse_message(2, event=hac.TERMINAL_EVENT) + _sse_message(3)
    events = list(hac.parse_sse_events(_lines(raw)))
    # Se para TRAS emitir el evento terminal; el seq 3 no se entrega.
    assert [e["seq"] for e in events] == [1, 2]
    assert events[-1]["event"] == hac.TERMINAL_EVENT


def test_parser_max_events_cap():
    raw = "".join(_sse_message(i) for i in range(1, 11))
    events = list(hac.parse_sse_events(_lines(raw), max_events=3))
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_parser_byte_budget_cap():
    # Un presupuesto de bytes diminuto corta tras el primer evento.
    raw = "".join(_sse_message(i, text="x" * 50) for i in range(1, 6))
    events = list(hac.parse_sse_events(_lines(raw), byte_budget=10))
    assert len(events) == 1 and events[0]["seq"] == 1


def test_parser_wait_seconds_boundary():
    # Reloj simulado: la deadline se cruza antes de procesar la segunda frontera.
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    raw = _sse_message(1) + _sse_message(2) + _sse_message(3)
    events = list(hac.parse_sse_events(
        _lines(raw), wait_seconds=1.0, _clock=lambda: next(ticks)
    ))
    # Al menos el primero se emite; el corte por tiempo impide procesar todos.
    assert events and events[0]["seq"] == 1
    assert len(events) < 3


# ------------------------------------------------------------------ HermesApiClient


def _client_with(handler):
    """HermesApiClient cuyo httpx usa un MockTransport con `handler`.

    Se parchea httpx.request / httpx.stream para inyectar el transporte mock, ya que el
    cliente usa las funciones de módulo de httpx (no una instancia inyectada).
    """
    transport = httpx.MockTransport(handler)
    return transport


@pytest.fixture()
def patch_httpx(monkeypatch):
    """Devuelve una función que instala un handler mock sobre httpx.request/stream."""
    def _install(handler):
        transport = httpx.MockTransport(handler)

        def _request(method, url, **kwargs):
            with httpx.Client(transport=transport) as c:
                return c.request(method, url, **kwargs)

        def _stream(method, url, **kwargs):
            # Devuelve un context manager de stream sobre el transporte mock.
            c = httpx.Client(transport=transport)
            return c.stream(method, url, **kwargs)

        monkeypatch.setattr(hac.httpx, "request", _request)
        monkeypatch.setattr(hac.httpx, "stream", _stream)
    return _install


def test_health_sets_bearer(patch_httpx):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok", "platform": "hermes-agent",
                                         "version": "0.22.0"})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "SECRET")
    out = client.health()
    assert out["platform"] == "hermes-agent" and out["version"] == "0.22.0"
    assert captured["auth"] == "Bearer SECRET"


def test_create_run_idempotency_header_only_when_given(patch_httpx):
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("Idempotency-Key"))
        return httpx.Response(200, json={"run_id": "run_1", "status": "started",
                                         "replayed": False})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    client.create_run("hola")
    client.create_run("hola", idempotency_key="abc")
    assert seen_headers[0] is None
    assert seen_headers[1] == "abc"


def test_create_run_omits_empty_optionals(patch_httpx):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "run_1", "status": "started",
                                         "replayed": False})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    client.create_run("hola", session_id=None, instructions="", previous_response_id="prev")
    body = bodies[0]
    assert body["input"] == "hola"
    assert body["previous_response_id"] == "prev"
    assert "session_id" not in body and "instructions" not in body
    # conversation_history NUNCA se envía (omisión deliberada del wrapper).
    assert "conversation_history" not in body


def test_error_envelope_forwarded_verbatim(patch_httpx):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Run not found: run_x",
                                                   "type": "invalid_request_error",
                                                   "param": None, "code": "run_not_found"}})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    out = client.get_run("run_x")
    assert out["error"]["code"] == "run_not_found"
    assert out["error"]["message"] == "Run not found: run_x"


def test_non_2xx_without_envelope_synthesizes_upstream_error(patch_httpx):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>boom</html>")

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    out = client.get_run("run_x")
    assert out["error"]["code"] == "upstream_error"
    assert "500" in out["error"]["message"]


def test_connection_failure_maps_to_unreachable(patch_httpx):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    out = client.health()
    assert out["error"]["code"] == "upstream_unreachable"
    assert "cannot reach Hermes" in out["error"]["message"]


def test_steer_409_forwarded(patch_httpx):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {
            "message": "Run is not currently accepting steer input: run_x",
            "type": "invalid_request_error", "param": None,
            "code": "run_not_accepting_steer"}})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    out = client.steer_run("run_x", "stop")
    assert out["error"]["code"] == "run_not_accepting_steer"
