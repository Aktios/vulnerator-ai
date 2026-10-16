"""Tests del cliente :8642 y el batcher SSE (`docker/mcp/hermes_api_client.py`).

NO importan `mcp`/FastMCP: solo el módulo SDK-free `hermes_api_client` + httpx + anyio +
pytest (mismo patrón que `docker/kali/tests/test_scope_guard.py`). Cubren:
  - El batcher SSE puro (`batch_sse_events`): wire `: open`/`id:`/`data:` (incl. multilínea),
    cursor por `seq`, dedup entre páginas, skip best-effort de eventos malformados, y los
    topes de batch (max_events, presupuesto de bytes SIN overshoot) + razón de parada
    (STOP_CAP/STOP_TERMINAL/STOP_DRAINED).
  - `encode_run_id_segment`: el run_id (dato no confiable) se codifica como UN segmento y se
    rechaza cualquier intento de recorrido/salto de ruta (fija review Finding "run_id route
    escape"), incluida la variante percent-encoded; y que un run_id de recorrido NO toca red.
  - `iter_events` (async, httpx.AsyncClient + MockTransport): stream/dedup, corte en el
    evento terminal, y preservación del sobre {"error": {...}} COMPLETO en non-2xx/invalid.
  - HermesApiClient con httpx.MockTransport: Bearer puesto, Idempotency-Key solo cuando se
    da, el mapeo de cada forma de error (sobre JSON, non-2xx sin sobre, fallo de conexión), y
    que la ruta construida es el target exacto esperado.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
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
    events, next_cursor, reason = hac.batch_sse_events(_lines(raw))
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert next_cursor == 3
    assert reason == hac.STOP_DRAINED  # el iterador se agotó sin evento terminal
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
    events, _, _ = hac.batch_sse_events(_lines(raw))
    assert len(events) == 1 and events[0]["seq"] == 5 and events[0]["text"] == "a"


def test_parser_dedup_by_after_seq():
    raw = _sse_message(1) + _sse_message(2) + _sse_message(3)
    events, next_cursor, _ = hac.batch_sse_events(_lines(raw), after_seq=2)
    assert [e["seq"] for e in events] == [3]
    assert next_cursor == 3


def test_parser_skips_malformed_and_missing_seq():
    good = _sse_message(1)
    bad_json = "id: 2\ndata: {not json}\n\n"
    no_seq = "id: 3\ndata: %s\n\n" % json.dumps({"event": "x", "run_id": "r"})
    good2 = _sse_message(4)
    events, _, _ = hac.batch_sse_events(_lines(good + bad_json + no_seq + good2))
    assert [e["seq"] for e in events] == [1, 4]


def test_parser_stops_on_terminal_event():
    raw = _sse_message(1) + _sse_message(2, event=hac.TERMINAL_EVENT) + _sse_message(3)
    events, next_cursor, reason = hac.batch_sse_events(_lines(raw))
    # Se para TRAS emitir el evento terminal; el seq 3 no se entrega.
    assert [e["seq"] for e in events] == [1, 2]
    assert events[-1]["event"] == hac.TERMINAL_EVENT
    assert reason == hac.STOP_TERMINAL
    assert next_cursor == 2


def test_parser_max_events_cap():
    raw = "".join(_sse_message(i) for i in range(1, 11))
    events, next_cursor, reason = hac.batch_sse_events(_lines(raw), max_events=3)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert reason == hac.STOP_CAP  # cap -> hay más datos, el servidor NO declara done
    assert next_cursor == 3


def test_parser_byte_budget_cap_single_event():
    # Un presupuesto de bytes diminuto deja pasar UN único evento (anti-starvation) y corta.
    raw = "".join(_sse_message(i, text="x" * 50) for i in range(1, 6))
    events, _, reason = hac.batch_sse_events(_lines(raw), byte_budget=10)
    assert len(events) == 1 and events[0]["seq"] == 1
    assert reason == hac.STOP_CAP


def test_parser_byte_budget_closes_page_before_overshoot():
    # Fija review Finding "byte budget overshoots by one event": con DOS eventos grandes y un
    # presupuesto que cabe el primero pero no la suma, el segundo NO se incluye (se mide ANTES
    # de añadir). El batch devuelve solo el primero y el segundo queda para la próxima página.
    big = "y" * 200
    one = _sse_message(1, text=big)
    two = _sse_message(2, text=big)
    size_one = len(json.dumps(json.loads(one.split("data: ", 1)[1].split("\n\n")[0])))
    # Presupuesto: cabe el primero, pero el primero+segundo lo cruza.
    budget = size_one + 10
    events, next_cursor, reason = hac.batch_sse_events(
        _lines(one + two), byte_budget=budget
    )
    assert [e["seq"] for e in events] == [1]  # el segundo NO se incluyó (sin overshoot)
    assert next_cursor == 1  # el seq del segundo NO avanza: se re-leerá en la próxima página
    assert reason == hac.STOP_CAP


def test_parser_byte_budget_two_small_events_both_fit():
    # Control: si ambos caben, los dos salen (no se corta de más).
    one = _sse_message(1, text="a")
    two = _sse_message(2, text="b")
    events, _, reason = hac.batch_sse_events(_lines(one + two), byte_budget=10_000)
    assert [e["seq"] for e in events] == [1, 2]
    assert reason == hac.STOP_DRAINED


# ------------------------------------------------------------------ techo absoluto por evento


def test_parser_oversized_single_event_is_placeholdered():
    # Fija el blocking Finding "single SSE event has no absolute size limit": un ÚNICO evento
    # cuyo JSON supera el techo absoluto NO se emite verbatim (su payload no confiable podría
    # exceder el límite ~1 MiB del transporte). Se emite un placeholder cursor-safe pequeño que
    # conserva los metadatos de wire + un marcador de truncado, SIN el payload.
    huge = "Z" * 5_000
    raw = _sse_message(7, event="tool.completed", preview=huge)
    events, next_cursor, reason = hac.batch_sse_events(
        _lines(raw), max_single_event_bytes=1_000
    )
    assert len(events) == 1
    ev = events[0]
    # El payload no confiable NO se reenvía.
    assert "preview" not in ev
    assert huge not in json.dumps(ev)
    # El placeholder es PEQUEÑO (muy por debajo del techo) y marca el truncado.
    assert len(json.dumps(ev)) < 1_000
    assert ev["vulnerator_mcp_oversized"]["serialized_bytes"] > 1_000
    assert ev["vulnerator_mcp_oversized"]["limit_bytes"] == 1_000
    # Metadatos de wire preservados -> el cursor AVANZA (no se atasca la paginación).
    assert ev["event"] == "tool.completed"
    assert ev["seq"] == 7
    assert next_cursor == 7
    assert reason == hac.STOP_CAP


def test_parser_oversized_event_after_normal_closes_page_first():
    # Si la página YA tiene un evento normal, el oversized NO se mezcla: se cierra la página
    # ANTES (STOP_CAP) y su seq NO avanza, para reprocesarlo con la página vacía (y entonces
    # placeholderearlo). Así nunca se pierde ni se mezcla con eventos normales.
    small = _sse_message(1, text="ok")
    huge = _sse_message(2, event="tool.completed", preview="Q" * 5_000)
    events, next_cursor, reason = hac.batch_sse_events(
        _lines(small + huge), max_single_event_bytes=1_000
    )
    assert [e["seq"] for e in events] == [1]  # solo el normal
    assert next_cursor == 1  # el oversized queda pendiente (se reprocesa la próxima página)
    assert reason == hac.STOP_CAP
    # Página siguiente (empezando tras el seq 1): el oversized se placeholderea.
    events2, next_cursor2, reason2 = hac.batch_sse_events(
        _lines(huge), after_seq=1, max_single_event_bytes=1_000
    )
    assert len(events2) == 1
    assert "vulnerator_mcp_oversized" in events2[0]
    assert events2[0]["seq"] == 2 and next_cursor2 == 2


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


# ------------------------------------------------------------------ run_id como segmento


def test_encode_run_id_segment_normal():
    # Un id normal pasa tal cual (es ya un segmento seguro).
    assert hac.encode_run_id_segment("run_36998a15") == "run_36998a15"


def test_encode_run_id_segment_percent_encodes_specials():
    # Un espacio u otros caracteres se percent-encodean (siguen siendo UN segmento).
    assert hac.encode_run_id_segment("run with space") == "run%20with%20space"


@pytest.mark.parametrize("evil", [
    "../runs?ignored",       # el PoC del review: saltaría a POST /v1/runs
    "../runs",
    "a/b",                   # slash crudo
    "a\\b",                  # backslash
    "run?x=1",               # query
    "run#frag",              # fragment
    "%2e%2e%2fruns",         # ".." + "/" percent-encoded -> se decodifica y se rechaza
    "%2fetc%2fpasswd",       # "/" percent-encoded
    "run\x00id",             # NUL
])
def test_encode_run_id_segment_rejects_route_escape(evil):
    with pytest.raises(hac.RunIdError):
        hac.encode_run_id_segment(evil)


def test_run_id_route_escape_is_blocked_before_network(patch_httpx):
    # Fija el blocking Finding "run_id route escape": un run_id de recorrido NO debe tocar la
    # red; debe devolver invalid_request SIN hacer la petición (no crear un run via steer).
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"run_id": "run_leak", "status": "started"})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    out = client.steer_run("../runs?ignored", "please create a run")
    assert out["error"]["code"] == "invalid_request"
    assert called["n"] == 0  # NUNCA se llamó a upstream


def test_run_id_safe_hits_exact_target(patch_httpx):
    # "Test the final request target": un run_id normal construye la ruta esperada.
    targets = []

    def handler(request: httpx.Request) -> httpx.Response:
        targets.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    patch_httpx(handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    client.get_run("run_abc")
    client.stop_run("run_abc")
    assert targets[0] == "http://up:8642/v1/runs/run_abc"
    assert targets[1] == "http://up:8642/v1/runs/run_abc/stop"


# ------------------------------------------------------------------ iter_events (async)


def _async_mock_client(hac_module, monkeypatch, handler):
    """Parchea httpx.AsyncClient para que iter_events use un MockTransport async."""
    orig_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(hac_module.httpx, "AsyncClient", _factory)


def test_iter_events_non_2xx_preserves_full_envelope(monkeypatch):
    # Fija el blocking Finding "streaming errors lose the common error envelope": un run
    # desconocido en el endpoint de eventos devuelve el sobre {"error": {...}} COMPLETO como
    # `error`, con stop_reason "error" (misma forma que request/response).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {
            "message": "Run not found: run_x", "type": "invalid_request_error",
            "param": None, "code": "run_not_found"}})

    _async_mock_client(hac, monkeypatch, handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    events, next_cursor, error, reason = anyio.run(
        lambda: client.iter_events("run_x", after_seq=0)
    )
    assert events == [] and next_cursor == 0
    assert reason == "error"
    assert error == {"error": {"message": "Run not found: run_x",
                               "type": "invalid_request_error", "param": None,
                               "code": "run_not_found"}}


def test_iter_events_streams_and_dedup(monkeypatch):
    body = ": open\n\n" + _sse_message(1) + _sse_message(2) + _sse_message(3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    _async_mock_client(hac, monkeypatch, handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    events, next_cursor, error, reason = anyio.run(
        lambda: client.iter_events("run_x", after_seq=1)
    )
    assert error is None
    assert [e["seq"] for e in events] == [2, 3]
    assert next_cursor == 3
    assert reason == hac.STOP_DRAINED


def test_iter_events_terminal_event_stops(monkeypatch):
    body = _sse_message(1) + _sse_message(2, event=hac.TERMINAL_EVENT) + _sse_message(3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    _async_mock_client(hac, monkeypatch, handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    events, _, error, reason = anyio.run(
        lambda: client.iter_events("run_x", after_seq=0)
    )
    assert error is None
    assert [e["seq"] for e in events] == [1, 2]  # para en el terminal
    assert reason == hac.STOP_TERMINAL


def test_iter_events_deadline_covers_pre_header_stall(monkeypatch):
    # Fija el blocking Finding "SSE pre-header wait escapes the deadline": si el upstream
    # ACEPTA la conexión pero RETIENE las cabeceras más allá de wait_seconds, iter_events debe
    # cortar por el deadline wall-clock (STOP_DEADLINE, frontera NORMAL sin error), NO colgar.
    # El handler async duerme más que wait_seconds ANTES de devolver la respuesta/cabeceras.
    async def handler(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5.0)  # retiene cabeceras (read-timeout está deshabilitado)
        return httpx.Response(200, text=_sse_message(1),
                              headers={"content-type": "text/event-stream"})

    _async_mock_client(hac, monkeypatch, handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    events, next_cursor, error, reason = anyio.run(
        lambda: client.iter_events("run_x", after_seq=0, wait_seconds=0.2)
    )
    # Cortó por el deadline durante la adquisición de cabeceras: sin eventos, SIN error.
    assert events == []
    assert error is None
    assert reason == hac.STOP_DEADLINE
    assert next_cursor == 0


def test_iter_events_invalid_run_id_no_network(monkeypatch):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, text="")

    _async_mock_client(hac, monkeypatch, handler)
    client = hac.HermesApiClient("http://up:8642", "K")
    events, _, error, reason = anyio.run(
        lambda: client.iter_events("../runs", after_seq=0)
    )
    assert reason == "error"
    assert error["error"]["code"] == "invalid_request"
    assert called["n"] == 0
