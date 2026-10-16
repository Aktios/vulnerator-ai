"""Cliente FINO de la API de runs de Hermes en `:8642` (hop upstream del wrapper MCP).

Este módulo habla con la API OpenAI-compatible de runs que sirve Hermes
(`nousresearch/hermes-agent`) en `:8642`. NO contiene lógica de pentest: solo traduce las
cinco operaciones del ciclo de vida (crear/estado/eventos/steer/stop) + `health` a
llamadas HTTP, poniendo el Bearer de PASS-THROUGH leído de `HERMES_API_SERVER_KEY` en cada
petición. El wrapper MCP (``vulnerator_mcp_server.py``) delega aquí.

Disciplina (igual que docker/kali/*):
  - DATO NO CONFIABLE: el objeto run, los eventos y todos sus campos (`delta`/`text`/
    `preview`/`output`/`message`) se reenvían VERBATIM; nunca se interpretan ni se usan
    para construir una acción. Pueden contener salida cruda de herramientas e incluso
    instrucciones embebidas (prompt-injection): se transportan como datos, no como órdenes.
  - VERSION-TOLERANTE: el contrato de `:8642` es de Hermes, no nuestro. No se hace un
    parseo de esquema fijo; el objeto run se pasa OPACO. La ÚNICA cadena de wire fija en el
    flujo de control es el tipo de evento terminal ``"run.completed"`` (para reconocer el
    fin del stream al paginar). La versión upstream se LEE de `GET /health` y se reporta
    verbatim; nunca se ramifica por ella en código.
  - SIN registro de runs ni caché de eventos: stateless; Hermes es dueño del `run_id`. El
    cursor del stream es el campo `seq` (int monótono) que trae cada evento.

SDK-free a propósito: NO importa `mcp`/`anyio`, de modo que el parser SSE + cursor y el
mapeo de errores se cubren con tests unitarios sin el SDK (igual que `tool_streaming`/
`scope_guard` viven aparte de `kali_mcp_server`). Única dependencia no-stdlib: `httpx`
(justificada por el stream SSE; ver requirements.txt).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Iterable, Iterator, Optional

import httpx

# Tipo del evento terminal del ciclo de vida (único string de wire fijo en control de
# flujo; version-tolerancia, ver docstring del módulo).
TERMINAL_EVENT = "run.completed"

# Topes del batch de `stream_run_events` (ver design §4.1). Se definen aquí porque el
# parser SSE los aplica; el servidor MCP reutiliza estos defaults.
DEFAULT_MAX_EVENTS = 200
# Presupuesto de bytes por batch: muy por debajo del tope de 1 MiB de un único evento SSE
# del transporte streamable-http (mismo motivo por el que existe read_scan_findings en
# Kali). 256 KiB deja margen amplio tras el overhead del sobre MCP.
DEFAULT_BYTE_BUDGET = 256 * 1024
# Presupuesto wall-clock por batch: una llamada a la tool nunca cuelga al cliente esperando
# un stream silencioso.
DEFAULT_WAIT_SECONDS = 25.0


def _error_envelope(message: str, code: str, type_: str = "upstream_error") -> dict[str, Any]:
    """Construye un sobre de error OpenAI-style homogéneo (igual forma que el de Hermes).

    `message` es DATO NO CONFIABLE si proviene de upstream; aquí solo se encapsula, no se
    interpreta. El wrapper lo reenvía verbatim al cliente MCP.
    """
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


def parse_sse_events(
    lines: Iterable[str],
    *,
    after_seq: int = 0,
    max_events: Optional[int] = None,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
    wait_seconds: Optional[float] = None,
    _clock: Any = time.monotonic,
) -> Iterator[dict[str, Any]]:
    """Parser SSE PURO (sin socket): convierte líneas de wire en dicts de evento.

    Es la pieza de mayor valor de test unitario: se le alimentan líneas crudas (incluido el
    comentario ``: open``, `data:` multilínea y un evento malformado) y produce los dicts de
    evento ya filtrados por cursor. NO abre red: ``iter_events`` le pasa el iterador de
    líneas de ``httpx``.

    Protocolo de wire observado (hermes-agent 0.22.0, investigación §10.4):
      - El campo SSE ``event:`` NO se usa; el tipo vive dentro del JSON (campo ``event``).
      - Cada mensaje es ``id: <n>`` + ``data: <json>``; el stream abre con ``: open``.
      - Cada evento JSON trae ``event`` (tipo), ``run_id``, ``timestamp`` (float), ``seq``
        (int monótono). ``seq`` ES EL CURSOR.

    Reglas:
      - Se ignoran líneas en blanco (frontera de evento), comentarios (``:`` al inicio, p.
        ej. ``: open``) y la línea ``id:`` (se usa el ``seq`` del JSON, autoritativo).
      - Se acumulan las líneas ``data:`` del evento actual (SSE permite ``data:`` multilínea).
      - En la frontera de evento se hace ``json.loads``. Si falla el parseo o falta/!int el
        ``seq`` -> SE OMITE ese evento (best-effort, como read_findings salta líneas
        corruptas); nunca se rompe el stream por un payload malformado/oversized.
      - ``seq <= after_seq`` -> se omite (ya entregado): dedup entre páginas.
      - En otro caso se hace ``yield`` del dict de evento COMPLETO sin tocar (opaco).

    Condiciones de parada (frontera de batch; NO son error):
      - ``max_events`` alcanzado.
      - presupuesto de bytes (``byte_budget``) de eventos serializados acumulados superado.
      - ``wait_seconds`` (float) wall-clock agotado.
      - evento terminal visto (``event == "run.completed"``): se hace yield y se para.
      - fin del iterador de líneas (stream cerrado por upstream).
    """
    effective_max = max_events if (max_events is not None and max_events > 0) else None
    deadline = None
    if wait_seconds is not None and wait_seconds > 0:
        deadline = _clock() + wait_seconds

    emitted = 0
    bytes_seen = 0
    data_parts: list[str] = []

    def _flush() -> Optional[dict[str, Any]]:
        """Procesa el evento acumulado. Devuelve el dict a emitir o None (omitir)."""
        if not data_parts:
            return None
        raw = "\n".join(data_parts)
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            return None  # best-effort: evento malformado se omite
        if not isinstance(event, dict):
            return None
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            return None  # sin cursor usable -> se omite
        if seq <= after_seq:
            return None  # ya entregado (dedup entre páginas)
        return event

    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")

        if line == "":
            # Frontera de evento: procesa lo acumulado.
            event = _flush()
            data_parts = []
            if event is None:
                # Nada que emitir: si ya se agotó el tiempo, corta aquí (no hay evento a
                # medias que perder).
                if deadline is not None and _clock() >= deadline:
                    return
                continue
            bytes_seen += len(json.dumps(event))
            yield event
            emitted += 1
            if event.get("event") == TERMINAL_EVENT:
                return
            if effective_max is not None and emitted >= effective_max:
                return
            if bytes_seen >= byte_budget:
                return
            # El corte por tiempo se evalúa DESPUÉS de emitir un evento completo, de modo que
            # un evento ya totalmente bufferizado nunca se pierde por la deadline.
            if deadline is not None and _clock() >= deadline:
                return
            continue

        if line.startswith(":"):
            continue  # comentario (p. ej. ": open")
        if line.startswith("id:"):
            continue  # id de wire: ignorado, se usa el seq del JSON
        if line.startswith("data:"):
            # SSE: tras "data:" se quita UN espacio opcional.
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            data_parts.append(payload)
            continue
        # Cualquier otra línea (p. ej. "event:") se ignora: el tipo vive en el JSON.

    # Fin del iterador o corte por tiempo: procesa el último evento sin frontera en blanco.
    event = _flush()
    if event is not None:
        yield event


class HermesApiClient:
    """Cliente FINO de la API de runs de Hermes en `:8642`.

    Construido con la URL base y el Bearer de pass-through. Cada método pone
    ``Authorization: Bearer <api_key>`` (+ ``Content-Type: application/json`` en cuerpos).
    Los retornos son el payload UPSTREAM reenviado opaco, o un sobre de error estructurado
    ``{"error": {...}}`` (ver mapeo en cada método). Version-tolerante: no parsea esquema
    fijo; la terminalidad se deriva de los booleanos del objeto run, nunca del string
    ``status``.
    """

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    # ------------------------------------------------------------------ helpers

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": "Bearer %s" % self._api_key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _handle_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Mapea una respuesta HTTP a dict (payload opaco o sobre de error).

        - 2xx con JSON -> se devuelve el JSON tal cual (opaco).
        - non-2xx con sobre de error JSON (``{"error": {...}}``) -> se devuelve verbatim.
        - non-2xx sin sobre parseable -> sobre sintetizado ``upstream_error`` con un
          snippet corto del cuerpo (recortado; no se interpola payload grande).
        - 2xx sin JSON parseable -> sobre ``upstream_error`` (forma inesperada).
        """
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = None

        if resp.is_success:
            if isinstance(body, dict):
                return body
            snippet = (resp.text or "")[:200]
            self._warn("respuesta 2xx no-JSON de %s: %s" % (resp.url, snippet))
            return _error_envelope(
                "upstream returned a non-JSON 2xx body: %s" % snippet,
                code="upstream_error",
            )

        # non-2xx
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = body["error"].get("code", "upstream_error")
            self._warn("error upstream %s (%s) en %s" % (resp.status_code, code, resp.url))
            return body  # sobre OpenAI-style verbatim (run_not_found/run_not_accepting_steer/...)

        snippet = (resp.text or "")[:200]
        self._warn("non-2xx %s sin sobre en %s: %s" % (resp.status_code, resp.url, snippet))
        return _error_envelope(
            "upstream HTTP %s: %s" % (resp.status_code, snippet),
            code="upstream_error",
        )

    def _unreachable(self, exc: Exception) -> dict[str, Any]:
        """Sobre estructurado cuando no se puede alcanzar `:8642` (DNS/refused/TLS)."""
        self._warn("Hermes inalcanzable en %s: %s" % (self._base_url, exc))
        return _error_envelope(
            "cannot reach Hermes at %s: %s" % (self._base_url, exc),
            code="upstream_unreachable",
            type_="upstream_unreachable",
        )

    @staticmethod
    def _warn(message: str) -> None:
        """Log a stderr de una línea; nunca interpola payloads grandes no confiables."""
        sys.stderr.write("[vulnerator-mcp] %s\n" % message)

    def _request(self, method: str, path: str, *, json_body: Any = None,
                 extra_headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        url = "%s%s" % (self._base_url, path)
        headers = self._headers(json_body=json_body is not None)
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = httpx.request(
                method, url, headers=headers, json=json_body, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            return self._unreachable(exc)
        return self._handle_response(resp)

    # ------------------------------------------------------------------ API

    def health(self) -> dict[str, Any]:
        """GET /health -> {status, platform, version} (investigación §10.1).

        ``version`` se reporta al cliente VERBATIM y NUNCA se usa para ramificar en código
        (version-tolerancia). Es el único sitio donde se expone una versión upstream.
        """
        return self._request("GET", "/health")

    def create_run(
        self,
        input: str,
        *,
        session_id: Optional[str] = None,
        instructions: Optional[str] = None,
        previous_response_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /v1/runs -> {run_id, status, replayed} (investigación §10.3).

        Envía ``Idempotency-Key`` como CABECERA (no en el cuerpo) solo cuando se pasa no
        vacía. Los opcionales vacíos/None se OMITEN del cuerpo JSON.
        ``conversation_history`` se OMITE a propósito del wrapper (design §3): la
        continuidad se expresa con ``previous_response_id``/``session_id``.
        """
        body: dict[str, Any] = {"input": input}
        if session_id:
            body["session_id"] = session_id
        if instructions:
            body["instructions"] = instructions
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        extra = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request("POST", "/v1/runs", json_body=body, extra_headers=extra)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """GET /v1/runs/{run_id} -> objeto hermes.run completo (opaco; §10.5).

        La terminalidad se lee de los booleanos ``completed``/``interrupted`` del objeto,
        NUNCA del string ``status``.
        """
        return self._request("GET", "/v1/runs/%s" % run_id)

    def steer_run(self, run_id: str, input: str) -> dict[str, Any]:
        """POST /v1/runs/{run_id}/steer. El 409 run_not_accepting_steer se reenvía verbatim (§10.6)."""
        return self._request("POST", "/v1/runs/%s/steer" % run_id, json_body={"input": input})

    def stop_run(self, run_id: str) -> dict[str, Any]:
        """POST /v1/runs/{run_id}/stop -> objeto run completo (terminal NO garantizado 'cancelled'; §10.5)."""
        return self._request("POST", "/v1/runs/%s/stop" % run_id)

    def iter_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        max_events: Optional[int] = None,
        wait_seconds: Optional[float] = None,
    ) -> Iterator[dict[str, Any]]:
        """GET /v1/runs/{run_id}/events (SSE). Hace yield de dicts de evento con seq > after_seq.

        Abre ``httpx.stream`` y delega el parseo en ``parse_sse_events`` (puro). Una
        excepción de conexión se traduce a UN único evento-error estructurado
        (``upstream_unreachable``) y se corta; un timeout de lectura es frontera de batch
        normal (no error), gestionado por ``wait_seconds`` dentro del parser.
        """
        url = "%s/v1/runs/%s/events" % (self._base_url, run_id)
        headers = self._headers()
        try:
            with httpx.stream("GET", url, headers=headers, timeout=self._timeout) as resp:
                if not resp.is_success:
                    # Lee el cuerpo para mapear el sobre de error y emítelo como un evento-error.
                    resp.read()
                    yield {"_error": self._handle_response(resp).get("error")}
                    return
                yield from parse_sse_events(
                    resp.iter_lines(),
                    after_seq=after_seq,
                    max_events=max_events,
                    wait_seconds=wait_seconds,
                )
        except httpx.HTTPError as exc:
            yield {"_error": self._unreachable(exc)["error"]}
