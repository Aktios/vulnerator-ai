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
  - FRONTERA DE URL: el ``run_id`` es DATO NO CONFIABLE. Se codifica como UN ÚNICO segmento
    de path (``encode_run_id_segment``), rechazando delimitadores de ruta (``/`` ``\\`` ``?``
    ``#`` y ``..``) incluso tras decodificar, de modo que un id malicioso no pueda saltar a
    otra ruta de Hermes (p. ej. ``../runs`` para crear un run sin el scope advisory).

SDK-free a propósito: NO importa `mcp`, de modo que el parser SSE + cursor, la codificación
del run_id y el mapeo de errores se cubren con tests unitarios sin el SDK (igual que
`tool_streaming`/`scope_guard` viven aparte de `kali_mcp_server`). Dependencias no-stdlib:
``httpx`` (stream SSE del hop a :8642, cliente ASYNC con deadline) y ``anyio`` (cancelación
por deadline del stream; ver requirements.txt).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Optional
from urllib.parse import quote, unquote

import anyio
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

# Razones de parada del batcher SSE (design §4.1). Distinguen un corte por TOPE (cap/
# deadline -> hay más datos pendientes, el batch NO es terminal) de un DRENADO real del
# stream (upstream cerró la conexión), que es la ÚNICA condición que habilita la
# confirmación de terminalidad por `get_run` en el servidor.
STOP_CAP = "cap"            # max_events o presupuesto de bytes agotado
STOP_DEADLINE = "deadline"  # presupuesto wall-clock agotado (stream silencioso)
STOP_TERMINAL = "terminal"  # se vio el evento run.completed
STOP_DRAINED = "drained"    # el iterador de líneas se agotó (upstream cerró el stream)


def _error_envelope(message: str, code: str, type_: str = "upstream_error") -> dict[str, Any]:
    """Construye un sobre de error OpenAI-style homogéneo (igual forma que el de Hermes).

    `message` es DATO NO CONFIABLE si proviene de upstream; aquí solo se encapsula, no se
    interpreta. El wrapper lo reenvía verbatim al cliente MCP.
    """
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


class RunIdError(ValueError):
    """El ``run_id`` no es un segmento de path seguro (contiene delimitadores de ruta)."""


# Delimitadores que, tras decodificar, permitirían a un run_id saltar a otra ruta/host de
# Hermes o inyectar query/fragment. Se rechazan explícitamente (defensa en profundidad
# sobre el quote por-segmento), incluido ``..`` (segmento de recorrido).
_FORBIDDEN_IN_RUN_ID = ("/", "\\", "?", "#")


def encode_run_id_segment(run_id: str) -> str:
    """Codifica ``run_id`` como UN ÚNICO segmento de path seguro (fija review Finding `run_id`).

    El ``run_id`` es DATO NO CONFIABLE: interpolarlo crudo en la URL deja que
    ``steer_run(run_id="../runs?x", ...)`` normalice a ``POST /v1/runs`` (creando un run sin
    el scope advisory) o que alcance otra ruta de Hermes. Para evitarlo:

      1. Se DECODIFICA el valor (``unquote``) y se comprueba que NI el crudo NI el decodificado
         contienen un delimitador de ruta (``/`` ``\\`` ``?`` ``#``) ni ``..`` ni NUL. Así se
         rechaza también la variante percent-encoded (``%2F`` -> ``/`` tras decodificar).
      2. Se ``quote`` con ``safe=""`` para que el resultado sea un único segmento (cualquier
         carácter especial queda percent-encoded y NO cambia de ruta).

    No valida el formato ``run_`` (Hermes es dueño del espacio de ids; version-tolerancia):
    solo garantiza que el valor no es sintaxis de URL que cambie de ruta. Lanza
    ``RunIdError`` si el id no es un segmento seguro.
    """
    if not isinstance(run_id, str) or not run_id:
        raise RunIdError("run_id vacío o no-string")
    # Se mira tanto el crudo como el decodificado: un cliente podría enviar ya el separador
    # literal o su forma percent-encoded.
    decoded = unquote(run_id)
    for candidate in (run_id, decoded):
        if "\x00" in candidate:
            raise RunIdError("run_id contiene NUL")
        if ".." in candidate:
            raise RunIdError("run_id contiene '..' (recorrido de ruta)")
        for bad in _FORBIDDEN_IN_RUN_ID:
            if bad in candidate:
                raise RunIdError("run_id contiene delimitador de ruta prohibido: %r" % bad)
    # safe="": todo lo demás se percent-encodea; el resultado es un único segmento de path.
    return quote(run_id, safe="")


class SseBatcher:
    """Batcher SSE PURO e INCREMENTAL (sin socket): acumula UNA página de eventos.

    Se le alimentan líneas de wire UNA A UNA con :meth:`feed`; cada ``feed`` devuelve una
    razón de parada (``STOP_CAP``/``STOP_TERMINAL``) en cuanto la página debe cerrarse, o
    ``None`` si puede seguir aceptando líneas. Es la pieza de mayor valor de test unitario:
    se le dan líneas crudas (incluido el comentario ``: open``, ``data:`` multilínea y un
    evento malformado) y expone ``events``/``next_cursor`` ya filtrados por cursor. NO abre
    red: ``iter_events`` le pasa las líneas que lee de la red y puede PARAR en cuanto el
    batcher señala un tope (sin bufferizar el stream entero en memoria).

    El servidor usa la razón de parada para decidir ``done`` (design §4.1): SOLO
    ``STOP_TERMINAL`` o ``STOP_DRAINED`` (iterador agotado) terminan la paginación;
    ``STOP_CAP``/``STOP_DEADLINE`` significan "hay más datos, sigue paginando" -> ``done=false``.

    Protocolo de wire observado (hermes-agent 0.22.0, investigación §10.4):
      - El campo SSE ``event:`` NO se usa; el tipo vive dentro del JSON (campo ``event``).
      - Cada mensaje es ``id: <n>`` + ``data: <json>``; el stream abre con ``: open``.
      - Cada evento JSON trae ``event`` (tipo), ``run_id``, ``timestamp`` (float), ``seq``
        (int monótono). ``seq`` ES EL CURSOR.

    Reglas:
      - Se ignoran comentarios (``:`` al inicio, p. ej. ``: open``) y la línea ``id:`` (se
        usa el ``seq`` del JSON, autoritativo). La línea en blanco es frontera de evento.
      - Se acumulan las líneas ``data:`` del evento actual (SSE permite ``data:`` multilínea).
      - En la frontera de evento se hace ``json.loads``. Si falla el parseo o falta/!int el
        ``seq`` -> SE OMITE ese evento (best-effort, como read_findings salta líneas
        corruptas); nunca se rompe el stream por un payload malformado/oversized.
      - ``seq <= after_seq`` -> se omite (ya entregado): dedup entre páginas.

    Presupuesto de bytes (fija review Finding "byte budget overshoots"): el tamaño del evento
    se mide ANTES de añadirlo. Si al añadirlo la página YA NO VACÍA cruzaría ``byte_budget``,
    se cierra la página SIN incluirlo (``STOP_CAP``) y queda para la siguiente (no se pierde:
    su ``seq`` no avanza). Un ÚNICO evento mayor que ``byte_budget`` con la página vacía SÍ se
    incluye (anti-starvation deliberada) y cierra la página tras él, de modo que un batch
    devuelva como mucho UN evento por encima del presupuesto, nunca dos. 256 KiB deja margen
    bajo el 1 MiB del transporte incluso para ese único evento + overhead del sobre MCP.
    """

    def __init__(
        self,
        *,
        after_seq: int = 0,
        max_events: Optional[int] = None,
        byte_budget: int = DEFAULT_BYTE_BUDGET,
    ) -> None:
        self._after_seq = after_seq
        self._max = max_events if (max_events is not None and max_events > 0) else None
        self._byte_budget = byte_budget
        self.events: list[dict[str, Any]] = []
        self.next_cursor = after_seq
        self._bytes_seen = 0
        self._data_parts: list[str] = []

    def _parse(self) -> Optional[dict[str, Any]]:
        """Procesa el evento acumulado en el buffer. Devuelve el dict a emitir o None."""
        if not self._data_parts:
            return None
        raw = "\n".join(self._data_parts)
        self._data_parts = []
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            return None  # best-effort: evento malformado se omite
        if not isinstance(event, dict):
            return None
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            return None  # sin cursor usable -> se omite
        if seq <= self._after_seq:
            return None  # ya entregado (dedup entre páginas)
        return event

    def _emit(self, event: dict[str, Any]) -> Optional[str]:
        """Intenta añadir ``event`` a la página. Devuelve una razón de parada o None.

        Mide el tamaño ANTES de añadir (fija el overshoot): si la página no vacía cruzaría el
        presupuesto, NO lo añade y devuelve ``STOP_CAP`` (el evento queda para el próximo
        batch). Un único evento oversized con la página vacía SÍ se añade (anti-starvation),
        y entonces se cierra la página tras él.
        """
        size = len(json.dumps(event))
        if self.events and self._bytes_seen + size > self._byte_budget:
            return STOP_CAP  # cerrar ANTES de cruzar el tope; el evento no se incluye
        self.events.append(event)
        self._bytes_seen += size
        seq = event["seq"]
        if seq > self.next_cursor:
            self.next_cursor = seq
        if event.get("event") == TERMINAL_EVENT:
            return STOP_TERMINAL
        if self._max is not None and len(self.events) >= self._max:
            return STOP_CAP
        if self._bytes_seen >= self._byte_budget:
            # Un único evento ya por encima del presupuesto (o justo en el límite): se incluyó
            # por anti-starvation, pero se cierra la página aquí para no acumular un segundo.
            return STOP_CAP
        return None

    def feed(self, raw_line: str) -> Optional[str]:
        """Alimenta UNA línea de wire. Devuelve una razón de parada o None (sigue aceptando)."""
        line = raw_line.rstrip("\n").rstrip("\r")
        if line == "":
            event = self._parse()
            if event is not None:
                return self._emit(event)
            return None
        if line.startswith(":"):
            return None  # comentario (p. ej. ": open")
        if line.startswith("id:"):
            return None  # id de wire: ignorado, se usa el seq del JSON
        if line.startswith("data:"):
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            self._data_parts.append(payload)
            return None
        # Cualquier otra línea (p. ej. "event:") se ignora: el tipo vive en el JSON.
        return None

    def flush(self) -> Optional[str]:
        """Procesa el último evento SIN frontera en blanco (stream drenado por upstream)."""
        event = self._parse()
        if event is not None:
            return self._emit(event)
        return None


def batch_sse_events(
    lines: Iterable[str],
    *,
    after_seq: int = 0,
    max_events: Optional[int] = None,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
) -> tuple[list[dict[str, Any]], int, str]:
    """Batchea un ITERABLE de líneas en UNA página (helper puro para tests + ruta de error).

    Envuelve :class:`SseBatcher`: alimenta las líneas hasta que el batcher señala parada
    (``STOP_CAP``/``STOP_TERMINAL``) o se agota el iterable (``STOP_DRAINED``). Devuelve
    ``(events, next_cursor, stop_reason)``. El corte por DEADLINE (stream silencioso) lo
    gestiona ``iter_events`` con cancelación async, no este helper síncrono.
    """
    batcher = SseBatcher(after_seq=after_seq, max_events=max_events, byte_budget=byte_budget)
    for raw_line in lines:
        reason = batcher.feed(raw_line)
        if reason is not None:
            return batcher.events, batcher.next_cursor, reason
    reason = batcher.flush()
    if reason is not None:
        return batcher.events, batcher.next_cursor, reason
    return batcher.events, batcher.next_cursor, STOP_DRAINED


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

    def _run_path(self, run_id: str, suffix: str = "") -> dict[str, Any] | str:
        """Construye ``/v1/runs/<seg>[/suffix]`` con el run_id como UN segmento seguro.

        Devuelve el path (str) o, si el run_id no es un segmento seguro, un sobre de error
        ``invalid_request`` (fail-closed, SIN llamada upstream). El llamador comprueba el tipo.
        """
        try:
            seg = encode_run_id_segment(run_id)
        except RunIdError as exc:
            self._warn("run_id rechazado como segmento de path: %s" % exc)
            return _error_envelope(
                "'run_id' no es un identificador válido", code="invalid_request",
                type_="invalid_request_error",
            )
        return "/v1/runs/%s%s" % (seg, suffix)

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
        NUNCA del string ``status``. El ``run_id`` se codifica como un segmento seguro.
        """
        path = self._run_path(run_id)
        if isinstance(path, dict):
            return path
        return self._request("GET", path)

    def steer_run(self, run_id: str, input: str) -> dict[str, Any]:
        """POST /v1/runs/{run_id}/steer. El 409 run_not_accepting_steer se reenvía verbatim (§10.6)."""
        path = self._run_path(run_id, "/steer")
        if isinstance(path, dict):
            return path
        return self._request("POST", path, json_body={"input": input})

    def stop_run(self, run_id: str) -> dict[str, Any]:
        """POST /v1/runs/{run_id}/stop -> objeto run completo (terminal NO garantizado 'cancelled'; §10.5)."""
        path = self._run_path(run_id, "/stop")
        if isinstance(path, dict):
            return path
        return self._request("POST", path)

    async def iter_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        max_events: Optional[int] = None,
        wait_seconds: Optional[float] = None,
    ) -> tuple[list[dict[str, Any]], int, Optional[dict[str, Any]], str]:
        """GET /v1/runs/{run_id}/events (SSE). Devuelve UNA página batcheada (ASYNC).

        Es ASYNC y con deadline propio (fija review Finding "quiet SSE wait"): abre
        ``httpx.AsyncClient.stream`` y lee líneas de forma asíncrona bajo un
        ``anyio.move_on_after(wait_seconds)``. Así un stream SILENCIOSO tras ``: open`` se
        corta en el presupuesto wall-clock (frontera de batch NORMAL, ``done=false``), SIN
        esperar al read-timeout de httpx y SIN bloquear el event loop de streamable-HTTP.

        Un corte por deadline es DISTINTO de un fallo de conexión/HTTP:
          - deadline/cap -> ``stop_reason`` ``STOP_DEADLINE``/``STOP_CAP`` y ``error=None``
            (el cliente sigue paginando).
          - non-2xx o excepción de conexión -> ``error`` es el sobre ``{"error": {...}}``
            COMPLETO (fija review Finding "streaming errors lose the envelope"): se preserva
            la MISMA forma que request/response, no un ``_error`` sintético.

        Returns:
            ``(events, next_cursor, error, stop_reason)``. Si ``error`` no es None, es el
            sobre de error completo y ``stop_reason`` es ``"error"``.
        """
        path = self._run_path(run_id, "/events")
        if isinstance(path, dict):
            return [], after_seq, path, "error"  # run_id inválido: sobre invalid_request

        url = "%s%s" % (self._base_url, path)
        headers = self._headers()

        if wait_seconds is not None and wait_seconds > 0:
            effective_wait = wait_seconds
        else:
            effective_wait = DEFAULT_WAIT_SECONDS

        batcher = SseBatcher(after_seq=after_seq, max_events=max_events)
        cap_reason: Optional[str] = None  # STOP_CAP/STOP_TERMINAL si un tope cerró la página
        hit_deadline = False

        try:
            # Read-timeout de httpx = None: el corte REAL por tiempo lo hace
            # anyio.move_on_after (deadline wall-clock del batch), no el read-timeout, para no
            # confundir un stream LENTO/silencioso con un fallo de conexión. El connect timeout
            # sí es corto (fail-fast si :8642 no está).
            stream_timeout = httpx.Timeout(
                connect=min(self._timeout, 10.0),
                read=None, write=self._timeout, pool=self._timeout,
            )
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    if not resp.is_success:
                        await resp.aread()
                        error = self._handle_response(resp)
                        return [], after_seq, error, "error"
                    # Lectura asíncrona de líneas bajo deadline wall-clock. Al expirar, se
                    # cancela el aiter (frontera de batch NORMAL, no error). Se para también
                    # en cuanto el batcher señala un tope, SIN bufferizar el resto del stream.
                    with anyio.move_on_after(effective_wait) as scope:
                        async for line in resp.aiter_lines():
                            cap_reason = batcher.feed(line)
                            if cap_reason is not None:
                                break
                    if scope.cancelled_caught:
                        hit_deadline = True
        except httpx.HTTPError as exc:
            # Fallo de conexión/HTTP: sobre {"error": {...}} COMPLETO (misma forma que el
            # resto del cliente), nunca un dict sintético {"_error": ...}. Los eventos
            # parciales ya acumulados en el batcher se devuelven igualmente (no se pierden).
            error = self._unreachable(exc)
            return batcher.events, batcher.next_cursor, error, "error"

        if cap_reason is not None:
            # Un tope (max_events/byte_budget) o el evento terminal cerró la página.
            return batcher.events, batcher.next_cursor, None, cap_reason
        if hit_deadline:
            # El deadline wall-clock cortó un stream aún abierto: frontera de batch NORMAL.
            # NO es STOP_DRAINED (podría haber más eventos tras el silencio), así que el
            # servidor NO debe confirmar terminalidad a partir de este batch.
            return batcher.events, batcher.next_cursor, None, STOP_DEADLINE
        # El aiter se agotó: upstream cerró el stream. Procesa el último evento sin frontera.
        drain_reason = batcher.flush()
        stop_reason = drain_reason if drain_reason is not None else STOP_DRAINED
        return batcher.events, batcher.next_cursor, None, stop_reason
