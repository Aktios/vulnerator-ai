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
# Techo ABSOLUTO del tamaño serializado de UN único evento (fija review Finding "single SSE
# event has no absolute size limit"). El presupuesto de batch (DEFAULT_BYTE_BUDGET) solo
# retiene el evento siguiente cuando la página YA no está vacía; un ÚNICO evento sobre una
# página vacía se admitía por anti-starvation sin tope superior, de modo que un
# `tool.completed.preview`/`text` no confiable podía exceder el límite ~1 MiB de un mensaje
# del transporte streamable-http. Este techo acota ese caso: un evento cuyo JSON supere
# MAX_SINGLE_EVENT_BYTES NO se emite (ni verbatim ni como placeholder dentro de `events`).
#
# CONTRATO FAIL-LOUD (fija los blocking Findings de la revisión 4 — "oversized event
# placeholder breaks opaque verbatim pass-through" y "oversized terminal run.completed can
# return done:false"): un placeholder DENTRO de `events` violaba dos invariantes a la vez:
#   (a) descartaba el payload no confiable (`preview`/`text`/`delta`/`output` y cualquier
#       campo futuro) AVANZANDO el cursor, dejando el `seq` original irrecuperable — rompe el
#       pass-through opaco/verbatim/version-tolerante; y
#   (b) si el evento sobredimensionado era `run.completed`, el placeholder conservaba ese
#       `event` mientras la página cerraba con STOP_CAP (`done=false`), devolviendo un
#       marcador terminal con semántica no terminal.
# En su lugar, el batcher NO emite nada dentro de `events` para un evento sobredimensionado,
# NO avanza el cursor (el `seq` queda recuperable) y señala STOP_OVERSIZE con los metadatos
# del evento (seq/tipo/tamaño/límite). El servidor los traduce a un sobre de error DESNUDO
# fuera de `events` —fail-loud y terminal para la paginación, igual que un pre-evento
# run_not_found— sin fingir ser un evento Hermes ni consumir el cursor. Los eventos que SÍ
# caben bajo el techo se entregan COMPLETOS antes (si la página ya tenía eventos, se cierra
# primero y el sobredimensionado se reprocesa en la siguiente página vacía).
# 512 KiB deja margen holgado bajo 1 MiB tras el overhead del sobre MCP.
MAX_SINGLE_EVENT_BYTES = 512 * 1024
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
# El evento siguiente supera el techo absoluto por evento (MAX_SINGLE_EVENT_BYTES): la página
# se cierra SIN emitirlo y SIN avanzar el cursor (fail-loud, ver docstring de
# MAX_SINGLE_EVENT_BYTES). Los metadatos del evento quedan en SseBatcher.oversized para que el
# servidor los traduzca a un sobre de error desnudo. Es una parada "para esta página", NO
# terminal del run: la terminalidad real (si el oversized era run.completed) se recupera con
# get_run.
STOP_OVERSIZE = "oversize"


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

    Techo ABSOLUTO por evento (fija review Finding "single SSE event has no absolute size
    limit" y los blocking de la revisión 4): la anti-starvation anterior no acotaba el tamaño
    de ESE único evento, así que un evento no confiable (p. ej. ``tool.completed.preview``/
    ``text``) podía superar el límite ~1 MiB del transporte. Si el JSON de un evento supera
    ``max_single_event_bytes`` se aplica el contrato FAIL-LOUD (ver docstring de
    ``MAX_SINGLE_EVENT_BYTES``): NO se emite (ni verbatim —truncar un evento opaco violaría el
    pass-through— ni como placeholder dentro de ``events``, que descartaría el payload y
    avanzaría el cursor), NO se avanza ``next_cursor`` (el ``seq`` queda recuperable) y se
    señala ``STOP_OVERSIZE`` guardando los metadatos del evento en :attr:`oversized`
    (``event``/``run_id``/``timestamp``/``seq`` + tamaño/límite, SIN el payload). El servidor
    traduce eso a un sobre de error DESNUDO fuera de ``events``. Si la página YA tiene eventos
    normales, se cierra ANTES con ``STOP_CAP`` (se entregan completos) y el sobredimensionado
    se reprocesa en la siguiente página vacía.
    """

    def __init__(
        self,
        *,
        after_seq: int = 0,
        max_events: Optional[int] = None,
        byte_budget: int = DEFAULT_BYTE_BUDGET,
        max_single_event_bytes: int = MAX_SINGLE_EVENT_BYTES,
    ) -> None:
        self._after_seq = after_seq
        self._max = max_events if (max_events is not None and max_events > 0) else None
        self._byte_budget = byte_budget
        self._max_single = max_single_event_bytes
        self.events: list[dict[str, Any]] = []
        self.next_cursor = after_seq
        # Metadatos del evento que gatilló STOP_OVERSIZE (None salvo que un evento supere el
        # techo absoluto y la página esté vacía). NO contiene el payload no confiable: solo
        # ``event``/``run_id``/``timestamp``/``seq`` + ``serialized_bytes``/``limit_bytes``.
        self.oversized: Optional[dict[str, Any]] = None
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

    @staticmethod
    def _oversized_metadata(event: dict[str, Any], size: int, limit: int) -> dict[str, Any]:
        """Metadatos (SIN payload) del evento que supera el techo absoluto por evento.

        NO reenvía el contenido no confiable (``preview``/``text``/``delta``/``output`` ni
        campos futuros): solo los metadatos de wire (``event``/``run_id``/``timestamp``/
        ``seq``) y el tamaño/límite. El servidor los pone en un sobre de error DESNUDO fuera de
        ``events``. El ``seq`` NO avanza el cursor del batch: el evento queda recuperable (el
        operador puede inspeccionarlo o avanzar manualmente el cursor por encima de él).
        """
        meta: dict[str, Any] = {
            "serialized_bytes": size,
            "limit_bytes": limit,
        }
        for key in ("event", "run_id", "timestamp", "seq"):
            if key in event:
                meta[key] = event[key]
        return meta

    def _emit(self, event: dict[str, Any]) -> Optional[str]:
        """Intenta añadir ``event`` a la página. Devuelve una razón de parada o None.

        Mide el tamaño ANTES de añadir (fija el overshoot): si la página no vacía cruzaría el
        presupuesto, NO lo añade y devuelve ``STOP_CAP`` (el evento queda para el próximo
        batch). Un único evento oversized con la página vacía SÍ se añade (anti-starvation),
        y entonces se cierra la página tras él.

        Techo ABSOLUTO — contrato FAIL-LOUD (fija review Finding "single SSE event has no
        absolute size limit" y los blocking de la revisión 4): si el JSON del evento supera
        ``max_single_event_bytes`` NO se emite —ni verbatim (truncar un evento opaco violaría
        el pass-through) ni como placeholder dentro de ``events`` (descartaría el payload y
        avanzaría el cursor)—. Si la página YA tiene eventos normales se cierra ANTES con
        ``STOP_CAP`` (se entregan completos y el oversized se reprocesa en la siguiente página
        vacía). Con la página vacía: NO se añade nada a ``events``, NO se avanza ``next_cursor``
        (el ``seq`` queda recuperable), se guardan los metadatos en :attr:`oversized` y se
        devuelve ``STOP_OVERSIZE``. El servidor lo traduce a un sobre de error desnudo fuera de
        ``events``. Así un único evento no confiable nunca excede el límite del transporte y la
        terminalidad real (si el oversized era ``run.completed``) se recupera con ``get_run``,
        nunca se devuelve un marcador terminal con semántica no terminal.
        """
        size = len(json.dumps(event))
        if size > self._max_single:
            # El evento supera el techo absoluto: nunca se emite dentro de `events`. Si la
            # página YA tiene eventos, se cierra ANTES para entregarlos completos (el oversized
            # se reprocesa en la siguiente página vacía).
            if self.events:
                return STOP_CAP
            # Página vacía: fail-loud. Se guardan los metadatos SIN payload y SIN avanzar el
            # cursor (el seq queda recuperable); el servidor devuelve un sobre de error desnudo.
            self.oversized = self._oversized_metadata(event, size, self._max_single)
            return STOP_OVERSIZE
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
    max_single_event_bytes: int = MAX_SINGLE_EVENT_BYTES,
) -> tuple[list[dict[str, Any]], int, str, Optional[dict[str, Any]]]:
    """Batchea un ITERABLE de líneas en UNA página (helper puro para tests + ruta de error).

    Envuelve :class:`SseBatcher`: alimenta las líneas hasta que el batcher señala parada
    (``STOP_CAP``/``STOP_TERMINAL``/``STOP_OVERSIZE``) o se agota el iterable
    (``STOP_DRAINED``). Devuelve ``(events, next_cursor, stop_reason, oversized)`` donde
    ``oversized`` es ``None`` salvo que ``stop_reason`` sea ``STOP_OVERSIZE`` (entonces lleva
    los metadatos SIN payload del evento sobredimensionado). El corte por DEADLINE (stream
    silencioso) lo gestiona ``iter_events`` con cancelación async, no este helper síncrono.
    """
    batcher = SseBatcher(
        after_seq=after_seq,
        max_events=max_events,
        byte_budget=byte_budget,
        max_single_event_bytes=max_single_event_bytes,
    )
    for raw_line in lines:
        reason = batcher.feed(raw_line)
        if reason is not None:
            return batcher.events, batcher.next_cursor, reason, batcher.oversized
    reason = batcher.flush()
    if reason is not None:
        return batcher.events, batcher.next_cursor, reason, batcher.oversized
    return batcher.events, batcher.next_cursor, STOP_DRAINED, batcher.oversized


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
        ``anyio.move_on_after(wait_seconds)``. Así un stream SILENCIOSO (antes O después de
        ``: open``) se corta en el presupuesto wall-clock (frontera de batch NORMAL,
        ``done=false``), SIN esperar al read-timeout de httpx y SIN bloquear el event loop de
        streamable-HTTP. El deadline envuelve TAMBIÉN la ADQUISICIÓN de la respuesta
        (``client.stream(...)`` espera status+cabeceras) y el drenaje del cuerpo de error de
        un non-2xx (fija review Finding "SSE pre-header wait escapes the deadline"): un
        upstream que acepta la conexión pero retiene cabeceras/cuerpo queda acotado por
        ``wait_seconds``, no solo por el connect timeout.

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

        # max_single_event_bytes se lee del módulo en tiempo de LLAMADA (no del default del
        # __init__, que se liga al definir): así el techo es configurable/testeable.
        batcher = SseBatcher(
            after_seq=after_seq,
            max_events=max_events,
            max_single_event_bytes=MAX_SINGLE_EVENT_BYTES,
        )
        cap_reason: Optional[str] = None  # STOP_CAP/STOP_TERMINAL si un tope cerró la página
        hit_deadline = False
        http_error: Optional[dict[str, Any]] = None  # sobre {"error":...} de un non-2xx
        # Status de un non-2xx YA observado (cabeceras recibidas) ANTES de drenar su cuerpo. Se
        # guarda aparte para que, si el DRENAJE del cuerpo de error se pasa del deadline (fija
        # review NB "stalled error body downgrades to a deadline page"), se pueda recuperar la
        # clasificación de error en vez de degradar a una página de deadline vacía.
        pending_error_status: Optional[int] = None

        try:
            # Read-timeout de httpx = None: el corte REAL por tiempo lo hace
            # anyio.move_on_after (deadline wall-clock del batch), no el read-timeout, para no
            # confundir un stream LENTO/silencioso con un fallo de conexión. El connect timeout
            # sí es corto (fail-fast si :8642 no está).
            stream_timeout = httpx.Timeout(
                connect=min(self._timeout, 10.0),
                read=None, write=self._timeout, pool=self._timeout,
            )
            # El deadline wall-clock envuelve TODO el ciclo de vida del stream: la adquisición
            # de la respuesta (status+cabeceras), el drenaje del cuerpo de error en un non-2xx
            # y la lectura de líneas del cuerpo 2xx (fija review Finding "pre-header wait
            # escapes the deadline"). Si expira ANTES de tener respuesta/cabeceras,
            # cancelled_caught -> hit_deadline (frontera NORMAL, no error): el read-timeout
            # está deshabilitado, así que sin este scope un upstream que retiene cabeceras
            # colgaría indefinidamente.
            with anyio.move_on_after(effective_wait) as scope:
                async with httpx.AsyncClient(timeout=stream_timeout) as client:
                    async with client.stream("GET", url, headers=headers) as resp:
                        if not resp.is_success:
                            # Se registra el status ANTES de drenar: si el drenaje se pasa del
                            # deadline, se recupera la clasificación de error tras el scope (NB
                            # "stalled error body downgrades to a deadline page").
                            pending_error_status = resp.status_code
                            # Drena el cuerpo de error DENTRO del mismo deadline (un cuerpo que
                            # no cierra no puede mantener la llamada abierta más allá de él).
                            await resp.aread()
                            http_error = self._handle_response(resp)
                        else:
                            # Lectura asíncrona de líneas. Se para en cuanto el batcher señala
                            # un tope, SIN bufferizar el resto del stream.
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

        if http_error is not None:
            # non-2xx con cabeceras ya recibidas dentro del deadline: sobre de error completo.
            return [], after_seq, http_error, "error"
        if pending_error_status is not None:
            # Se observó un non-2xx (cabeceras recibidas) pero el DRENAJE de su cuerpo se pasó
            # del deadline (http_error quedó sin asignar y cancelled_caught ganaría). Antes esto
            # degradaba a una página de deadline vacía (done=false), invitando a un cliente
            # loop-until-done a reintentar el mismo non-2xx (NB "stalled error body downgrades
            # to a deadline page"). Se preserva la clasificación de error devolviendo el sobre
            # DESNUDO a partir del status ya conocido (sin cuerpo, porque el cuerpo se colgó),
            # terminal para la paginación como cualquier otro non-2xx.
            self._warn(
                "cuerpo de error upstream HTTP %s se pasó del deadline en %s; se reporta el "
                "status sin cuerpo" % (pending_error_status, url)
            )
            error = _error_envelope(
                "upstream HTTP %s (error body stalled past the %.3gs stream deadline; body "
                "withheld)" % (pending_error_status, effective_wait),
                code="upstream_error",
            )
            return [], after_seq, error, "error"
        if cap_reason == STOP_OVERSIZE:
            # Un evento SOBREDIMENSIONADO (sobre página vacía) cerró el batch: contrato
            # FAIL-LOUD. NO se devuelve el evento (ni placeholder), NO se avanza el cursor
            # (next_cursor sigue en after_seq -> recuperable) y se emite un sobre de error
            # DESNUDO con los metadatos SIN payload. El servidor lo reenvía desnudo (terminal
            # para la paginación), igual que un run_not_found pre-evento. La terminalidad real
            # (si el oversized era run.completed) se recupera con get_run_status.
            meta = batcher.oversized or {}
            seq = meta.get("seq")
            etype = meta.get("event")
            error = _error_envelope(
                "a single SSE event (seq=%s, type=%s) serialized to %s bytes, exceeding the "
                "per-event ceiling of %s bytes; it was NOT forwarded (payload withheld to stay "
                "under the transport message limit) and the cursor was NOT advanced. Fetch the "
                "run with get_run_status to recover terminal state, or advance 'cursor' past "
                "seq=%s to skip it." % (
                    seq, etype, meta.get("serialized_bytes"), meta.get("limit_bytes"), seq,
                ),
                code="oversized_event",
                type_="oversized_event",
            )
            # Se adjuntan los metadatos (SIN payload) para que el cliente los inspeccione.
            error["error"]["oversized_event"] = meta
            return [], after_seq, error, "error"
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
