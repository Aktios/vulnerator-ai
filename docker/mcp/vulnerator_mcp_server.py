"""Servidor `vulnerator-mcp` — front door MCP sobre la API de runs de Hermes (`:8642`).

Este proceso es el SERVIDOR MCP que CONSUMEN los clientes externos (Claude Code / Kiro en
local vía stdio; consumidores remotos/containerizados como BugTraceAI vía streamable-HTTP).
ENVUELVE —no reemplaza— la API HTTP de runs de Hermes: expone SOLO el ciclo de vida de un
run (disparar/observar/dirigir/parar) como tools MCP, para que el cliente externo sea un
disparador/observador y NUNCA el cerebro. NO expone `/v1/chat/completions` ni ningún
`kali_run` crudo.

Lo que este wrapper NO hace (importante): NO impone scope, NO añade seguridad, NO valida
consentimiento ni audita. Solo añade ALCANZABILIDAD. ``scope`` es un argumento REQUERIDO
pero ADVISORY que se dobla dentro del prompt (claridad del operador, no un guard). La capa
Kali SÍ tiene un scope-guard DETERMINISTA OPT-IN (v1.3.0, vía ``scope.json``) que el
operador puede activar para defensa en profundidad sobre acciones de shell cruda; eso vive
en otra capa y NO es un gate de la API de runs de `:8642`.

DATO NO CONFIABLE: el objeto run, los eventos y todos sus campos se reenvían VERBATIM; nunca
se interpretan como instrucciones (`agent-ooda/context/AGENTS.md`). VERSION-TOLERANTE: la
terminalidad se deriva de los booleanos ``completed``/``interrupted`` del objeto run (y del
evento SSE ``run.completed``), NUNCA del string ``status`` ni de la ``version``, que se
reenvían opacos.

Transportes (uno/una imagen, dos modos, design §6):
  - stdio (default de proceso): ruta local recomendada de superficie-cero (sin listener, sin
    token, sin puerto). El cliente arranca el proceso (``docker run -i``).
  - streamable-http: para consumidores in-docker/remotos. FAIL-CLOSED en el token de entrada
    (``VULNERATOR_MCP_INBOUND_TOKEN``): NO arranca sin él. Bind interno a la red docker; sin
    publicación al host por defecto (ver docker-compose.mcp.yml).
"""

from __future__ import annotations

import functools
import hmac
import os
import sys
from typing import Any, Optional

# anyio.to_thread.run_sync: offload de las llamadas HTTP SÍNCRONAS (bloqueantes) del cliente
# :8642 a un hilo worker, para que NO bloqueen el event loop de streamable-http (fija review
# Finding "synchronous HTTP inside the async tool blocks the event loop"). El SDK `mcp` 1.x
# ejecuta el cuerpo de una tool SÍNCRONA INLINE en el event loop (no la delega a un hilo:
# ver func_metadata.call_fn_with_arg_validation), así que una tool bloqueante con un timeout
# de 30 s congelaría todo el tráfico MCP y /healthz. Por eso TODAS las tools son `async` y
# delegan la llamada bloqueante con `anyio.to_thread.run_sync`.
import anyio

# Cliente FINO de :8642 (SDK-free): health/create_run/get_run/steer/stop + stream SSE con
# cursor por `seq`. Reenvía payloads opacos y mapea errores a sobres estructurados.
from hermes_api_client import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_WAIT_SECONDS,
    STOP_DRAINED,
    STOP_TERMINAL,
    HermesApiClient,
)

# Lógica PURA (SDK-free): scope-folding, validación de entrada y la decisión de `done`.
# Vive aparte para testearse sin importar FastMCP (igual que scope_guard/tool_streaming en Kali).
from mcp_tool_logic import (
    clamp_cursor,
    decide_stream_done,
    fold_scope_into_input,
    normalize_optional_positive,
    validate_run_id,
    validate_start_pentest,
    validate_steer,
)

# SDK `mcp`: se fija mcp>=1.28,<2 (API 1.x de FastMCP), misma era de protocolo que el
# contenedor Kali vivo (mcp==1.30.0). Se importa aquí; por eso este módulo NO se importa en
# los tests unitarios de lógica pura (que importan mcp_tool_logic/hermes_api_client).
from mcp.server.fastmcp import Context, FastMCP

# --- Configuración por entorno (overridable; defaults seguros) ---------------
HOST = os.environ.get("VULNERATOR_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("VULNERATOR_MCP_PORT", "8700"))
# URL del hop upstream a Hermes. En compose se alcanza por DNS de servicio (ooda-agent).
HERMES_API_BASE_URL = os.environ.get("HERMES_API_BASE_URL", "http://ooda-agent:8642")
# Bearer de PASS-THROUGH upstream (NO se acuña un segundo secreto para el hop a :8642).
HERMES_API_SERVER_KEY = os.environ.get("HERMES_API_SERVER_KEY", "")
# Transporte: stdio (default de proceso) | streamable-http (lo fija el servicio opt-in).
TRANSPORT = os.environ.get("VULNERATOR_MCP_TRANSPORT", "stdio")
# Token de ENTRADA (hop 2: clientes -> vulnerator-mcp). OBLIGATORIO en streamable-http
# (fail-closed). Distinto de HERMES_API_SERVER_KEY: protege otra frontera (§8.3).
INBOUND_TOKEN = os.environ.get("VULNERATOR_MCP_INBOUND_TOKEN", "")

# Ruta de salud DEDICADA (exacta): el endpoint MCP se monta en "/", así que el carve-out de
# auth de entrada es SOLO para esta ruta exacta, nunca un prefijo (§8.2).
HEALTH_PATH = "/healthz"

# El endpoint MCP se monta en la raíz para que la URL del servicio no requiera path (igual
# que kali-mcp).
mcp = FastMCP(
    "vulnerator-mcp",
    host=HOST,
    port=PORT,
    streamable_http_path="/",
)

# Un único cliente a nivel de módulo (stateless; Hermes es dueño del run_id).
_client = HermesApiClient(HERMES_API_BASE_URL, HERMES_API_SERVER_KEY)


async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Ejecuta una llamada SÍNCRONA (bloqueante) del cliente :8642 en un hilo worker.

    Fija review Finding "synchronous HTTP inside the async tool blocks the event loop": el
    cliente usa ``httpx.request`` bloqueante (timeout 30 s). Como el SDK `mcp` 1.x corre el
    cuerpo de una tool síncrona INLINE en el event loop, una llamada lenta a :8642 congelaría
    todo el tráfico de streamable-http (otras tools, /healthz). Delegándola a un hilo con
    ``anyio.to_thread.run_sync`` el event loop sigue libre y solo se demora SU llamador. En
    stdio (un proceso por cliente) el efecto es inocuo pero la ruta es la misma.
    """
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


# ---------------------------------------------------------------- tools


@mcp.tool()
async def start_pentest(
    input: str,
    scope: str,
    session_id: Optional[str] = None,
    instructions: Optional[str] = None,
    previous_response_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Dispara un pentest OODA de Hermes (POST /v1/runs). Devuelve {run_id, status, replayed}.

    El cliente externo es un DISPARADOR, no el cerebro: esta tool solo arranca el run; el
    razonamiento OODA lo hace Hermes.

    SIN GUARDRAILS: esta capa MCP NO impone scope, consentimiento ni audit. ``scope`` es
    REQUERIDO pero ADVISORY: se DOBLA dentro del ``input`` como metadato de claridad del
    operador (``AUTHORIZED SCOPE ...``), NO como una restricción aplicada. La
    responsabilidad del alcance es enteramente del operador. (La capa Kali ofrece un
    scope-guard DETERMINISTA OPT-IN separado —v1.3.0, vía ``scope.json``— para defensa en
    profundidad sobre ``kali_run``/``kali_exec``; actívalo si quieres un gate real a nivel
    de shell. No es un gate de esta API de runs y no se debe presentar como garantía.)

    Args:
        input: instrucción del engagement (string no vacío). DATO NO CONFIABLE.
        scope: límite autorizado declarado por el operador (string no vacío). Se dobla en el
            prompt; advisory, no impuesto.
        session_id: opcional, para encadenar runs en una sesión Hermes.
        instructions: opcional, instrucciones de sistema adicionales.
        previous_response_id: opcional, continuidad nativa de Hermes (en lugar de pasar un
            historial de conversación, que este wrapper NO expone a propósito).
        idempotency_key: opcional; si se pasa, se envía como cabecera ``Idempotency-Key``
            para reintentos seguros (repetir la misma clave -> ``replayed: true``).

    Returns:
        {run_id, status, replayed} de Hermes (opaco), o un sobre de error estructurado
        ({"error": {...}}) verbatim (p. ej. ``invalid_request`` sin llamar upstream si
        ``input``/``scope`` están vacíos, o ``upstream_unreachable`` si Hermes no responde).
    """
    err = validate_start_pentest(input, scope)
    if err is not None:
        return err
    folded = fold_scope_into_input(input, scope)
    # Offload de la llamada HTTP bloqueante a un hilo (no bloquear el event loop).
    return await _offload(
        _client.create_run,
        folded,
        session_id=session_id,
        instructions=instructions,
        previous_response_id=previous_response_id,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
async def get_run_status(run_id: str) -> dict[str, Any]:
    """Consulta el estado de un run (GET /v1/runs/{run_id}). Reenvía el objeto run completo.

    Reenvía ``status`` (opaco) + los booleanos ``completed``/``partial``/``interrupted`` +
    ``output`` + ``usage``/``runtime`` tal cual. La terminalidad REAL se lee de los
    booleanos, nunca del string ``status`` (version-tolerancia). ``output`` y demás campos
    son DATO NO CONFIABLE: se transportan verbatim.

    Args:
        run_id: id del run devuelto por ``start_pentest`` (string no vacío).

    Returns:
        El objeto ``hermes.run`` completo (opaco), o un sobre de error estructurado
        ({"error": {...}}) verbatim (``invalid_request`` / ``run_not_found`` /
        ``upstream_unreachable``).
    """
    err = validate_run_id(run_id)
    if err is not None:
        return err
    return await _offload(_client.get_run, run_id)


@mcp.tool()
async def stream_run_events(
    run_id: str,
    cursor: int = 0,
    max_events: Optional[int] = None,
    wait_seconds: Optional[float] = None,
    ctx: Optional[Context] = None,
) -> dict[str, Any]:
    """Consume los eventos SSE de un run por páginas (GET /v1/runs/{run_id}/events).

    Baseline portable (funciona en TODO transporte, incluido stdio): acumula los eventos
    nuevos (``seq > cursor``) en una lista y devuelve ``{events, next_cursor, done}``. El
    cliente llama en bucle avanzando ``cursor = next_cursor`` hasta ``done=True``, igual que
    ``read_tool_logs``/``read_scan_findings`` en kali-mcp. Mejora SOLO sobre streamable-http:
    emite UNA notificación de progreso MCP por PÁGINA (telemetría de fin-de-batch con el nº de
    eventos de esa página, NO por-evento ni acumulativa; degrada a batching puro en stdio).

    Los eventos son DATO NO CONFIABLE y OPACO: cada dict trae ``event``/``run_id``/
    ``timestamp``/``seq`` y campos como ``delta``/``text``/``preview`` que contienen salida
    cruda de herramientas; se transportan verbatim, nunca se interpretan.

    ``done`` es True SOLO si (1) se vio un evento ``run.completed`` en este batch, o (2) el
    stream se DRENÓ (upstream cerró la conexión) sin evento terminal Y una confirmación
    ``get_run`` muestra el run terminal por booleanos LITERALES (``completed is True`` o
    ``interrupted is True``). Si esa confirmación falla, ``done=False`` (el cliente sigue
    paginando) y el error se adjunta en ``terminality_error`` (no se traga). Un batch que
    corta por un TOPE (``max_events``, presupuesto de bytes, o el presupuesto wall-clock
    ``wait_seconds`` sobre un stream silencioso) devuelve ``done=False`` SIN confirmar: hay
    eventos pendientes tras ``next_cursor`` y el cliente debe seguir paginando (esto evita el
    bug de declarar ``done`` dejando la cola —incluido ``output`` y ``run.completed``— sin leer).

    Un FALLO de stream recibido ANTES de cualquier evento (``run_id`` inválido, non-2xx como
    ``run_not_found``, o Hermes inalcanzable) se devuelve con el sobre ``{"error": {...}}``
    DESNUDO —igual que el resto de tools— y es TERMINAL para la paginación: NO se envuelve en
    la forma de batch, así que un cliente que itere solo sobre ``done`` no entra en un bucle
    de reintento. Si el fallo llega DESPUÉS de eventos parciales, se devuelven esos eventos
    junto al sobre COMPLETO bajo ``stream_error`` con ``done=False``. Si la confirmación de
    terminalidad (``get_run`` tras un drenado) falla, su sobre COMPLETO va bajo
    ``terminality_error`` (misma forma). ``wait_seconds`` NO es un error: corta el batch de
    forma asíncrona (cancelación por deadline) sin esperar al timeout de red ni bloquear el
    event loop de streamable-http.

    Args:
        run_id: id del run (string no vacío).
        cursor: último ``seq`` ya consumido; 0 para leer desde el principio. Negativo -> 0.
        max_events: tope opcional de eventos por página (default interno 200).
        wait_seconds: presupuesto wall-clock opcional por página (default interno 25.0 s).
        ctx: inyectado por FastMCP; usado para progreso en streamable-http (guardado).

    Returns:
        {"events": [...opaco...], "next_cursor": int, "done": bool
         [, "terminality_error": {"error": {...}}]}, o
        {"events": [...], "next_cursor": int, "done": false, "stream_error": {"error": {...}}}
        si el fallo llega tras eventos parciales, o el sobre de error DESNUDO
        ({"error": {...}}) verbatim ante validación fallida o un fallo pre-evento (p. ej.
        ``run_id`` inválido o ``run_not_found``) — terminal para la paginación.
    """
    err = validate_run_id(run_id)
    if err is not None:
        return err

    start_cursor = clamp_cursor(cursor)
    eff_max = normalize_optional_positive(max_events)
    if eff_max is None:
        eff_max = DEFAULT_MAX_EVENTS
    eff_wait = normalize_optional_positive(wait_seconds)
    if eff_wait is None:
        eff_wait = DEFAULT_WAIT_SECONDS

    events, next_cursor, error, stop_reason = await _client.iter_events(
        run_id,
        after_seq=start_cursor,
        max_events=int(eff_max),
        wait_seconds=float(eff_wait),
    )

    if error is not None:
        code = error.get("error", {}).get("code") if isinstance(error, dict) else None
        # Fail-closed ANTES de tocar la red (run_id inválido) o CUALQUIER non-2xx/inalcanzable
        # recibido ANTES de ningún evento: se devuelve el sobre {"error": {...}} DESNUDO,
        # igual que el resto de tools (get_run_status/steer_run/...). Así un run_not_found en
        # el endpoint de eventos NO se envuelve en la forma de batch {events,next_cursor,done}
        # (fija review Finding "unknown-run stream error invites a paging spin": un cliente
        # que itere solo sobre `done` ya no reintenta en bucle; el sobre es terminal).
        if not events:
            return error
        # Si ya habían llegado eventos parciales antes del fallo, se preservan junto al sobre
        # COMPLETO bajo stream_error (misma forma que el resto de errores). done=False.
        return {
            "events": events,
            "next_cursor": next_cursor,
            "done": False,
            "stream_error": error,
        }

    # Progreso (solo streamable-http). TELEMETRÍA DE FIN-DE-BATCH, no por-evento: se emite UNA
    # notificación con el nº de eventos de ESTA página (no acumulativo, sin `total`) justo
    # antes de devolver el resultado. El cliente percibe que una página trajo N eventos; para
    # progreso por-evento tendría que paginar. Guardado con try/except para degradar a batching
    # puro en stdio o con clientes sin soporte de progreso (es cosmético, nunca rompe el batch).
    if ctx is not None and events:
        try:
            await ctx.report_progress(progress=float(len(events)))
        except Exception:  # noqa: BLE001 - progreso es cosmético; nunca rompe el batch
            pass

    saw_terminal = stop_reason == STOP_TERMINAL
    stream_drained = stop_reason == STOP_DRAINED

    # Caso 2 (solo en DRENADO real sin evento terminal): una confirmación get_run decide la
    # terminalidad por booleanos. Un batch cortado por tope/deadline no confirma (done=False).
    # La confirmación es una llamada HTTP bloqueante: se OFFLOADEA a un hilo para no congelar
    # el event loop de streamable-http (fija review Finding "synchronous HTTP inside the async
    # stream tool blocks the event loop").
    confirm: Any = None
    confirm_is_error = False
    if stream_drained and not saw_terminal:
        confirm = await _offload(_client.get_run, run_id)
        confirm_is_error = isinstance(confirm, dict) and isinstance(confirm.get("error"), dict)

    done, needs_terminality_error = decide_stream_done(
        saw_terminal_event=saw_terminal,
        stream_drained=stream_drained,
        confirm_run=confirm,
        confirm_is_error=confirm_is_error,
    )

    result: dict[str, Any] = {
        "events": events,
        "next_cursor": next_cursor,
        "done": done,
    }
    if needs_terminality_error and isinstance(confirm, dict):
        # No se traga el error de confirmación: se adjunta el sobre COMPLETO {"error": {...}}
        # bajo terminality_error (misma forma que stream_error y que el resto de errores de la
        # API; fija review Finding "terminality error envelope is inconsistent": antes se
        # adjuntaba solo confirm["error"], el objeto interno sin el envoltorio {"error": ...}).
        result["terminality_error"] = confirm
    return result


@mcp.tool()
async def steer_run(run_id: str, input: str) -> dict[str, Any]:
    """Inyecta una corrección en un run en curso (POST /v1/runs/{run_id}/steer).

    La corrección se entrega en el siguiente límite de tool. Si el run ya no acepta steer,
    Hermes responde HTTP 409 con ``code: run_not_accepting_steer``, que esta tool reenvía
    VERBATIM (sobre ``{"error": {...}}``). ``input`` es DATO NO CONFIABLE: se transporta tal
    cual.

    Args:
        run_id: id del run (string no vacío).
        input: texto de corrección (string no vacío).

    Returns:
        El resultado upstream (opaco), o el sobre de error verbatim (``invalid_request`` /
        ``run_not_accepting_steer`` / ``run_not_found`` / ``upstream_unreachable``).
    """
    err = validate_steer(run_id, input)
    if err is not None:
        return err
    return await _offload(_client.steer_run, run_id, input)


@mcp.tool()
async def stop_run(run_id: str) -> dict[str, Any]:
    """Para un run (POST /v1/runs/{run_id}/stop). Reenvía el objeto run completo.

    El estado terminal NO se asume ``cancelled`` (se ha observado ``completed`` al parar un
    run que ya terminaba): se reenvía el objeto completo y la terminalidad se lee de los
    booleanos ``completed``/``interrupted``, nunca del string ``status`` (version-tolerancia).

    Args:
        run_id: id del run (string no vacío).

    Returns:
        El objeto ``hermes.run`` completo (opaco), o un sobre de error verbatim.
    """
    err = validate_run_id(run_id)
    if err is not None:
        return err
    return await _offload(_client.stop_run, run_id)


@mcp.tool()
async def health() -> dict[str, Any]:
    """Smoke test del upstream (GET /health). Reenvía {status, platform, version}.

    ``version`` se reporta VERBATIM para que el cliente sepa con qué habla; NUNCA se usa para
    ramificar en código (version-tolerancia). No toca ningún run.

    Returns:
        {status, platform, version} de Hermes (opaco), o un sobre de error estructurado
        (``upstream_unreachable``) si Hermes no responde.
    """
    return await _offload(_client.health)


# ---------------------------------------------------------------- arranque / transporte


def _build_authenticated_app(inbound_token: str) -> Any:
    """Construye la app streamable-http con ruta de salud + middleware Bearer.

    IMPORTANTE: ``streamable_http_app()`` devuelve una instancia NUEVA en cada llamada, así
    que se llama UNA sola vez y se opera sobre esa misma app (registrar /healthz + envolver
    con el middleware). De lo contrario la ruta y el middleware caerían en apps distintas.

    - Ruta de salud DEDICADA y EXACTA ``/healthz``: el endpoint MCP se monta en "/", así que
      el healthcheck NO sondea "/" (es el endpoint MCP; dejarlo abierto expondría la
      superficie MCP sin auth). Es el ÚNICO carve-out del middleware (match exacto).
    - Middleware Bearer: cada petición debe traer
      ``Authorization: Bearer <VULNERATOR_MCP_INBOUND_TOKEN>``; cualquier otra cosa -> 401. La
      comparación es constante en tiempo (``hmac.compare_digest``, como
      ``artifact_http._token_ok``). El carve-out es la ruta EXACTA ``/healthz`` (nunca un
      prefijo: "/" es el endpoint MCP y debe quedar autenticado).
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    app = mcp.streamable_http_app()

    async def _healthz(_request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "vulnerator-mcp"})

    app.router.routes.append(Route(HEALTH_PATH, _healthz, methods=["GET"]))

    async def _auth(request: Any, call_next: Any) -> Any:
        # Carve-out EXACTO: solo /healthz pasa sin token (igualdad, nunca startswith).
        if request.url.path == HEALTH_PATH:
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        expected = "Bearer %s" % inbound_token
        if not hmac.compare_digest(header, expected):
            return JSONResponse(
                {"error": {"message": "unauthorized", "type": "auth_error",
                           "param": None, "code": "unauthorized"}},
                status_code=401,
            )
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=_auth)
    return app


def main() -> None:
    """Selecciona el transporte y arranca el servidor.

    - streamable-http: FAIL-CLOSED en VULNERATOR_MCP_INBOUND_TOKEN (sin token -> stderr +
      sys.exit(1), igual que artifact_http.make_server se niega sin KALI_ARTIFACTS_TOKEN).
      Luego sirve la app ASGI con el middleware Bearer (constante en tiempo) por delante.
    - stdio: superficie de red cero; no requiere token de entrada.
    """
    if TRANSPORT == "streamable-http":
        if not INBOUND_TOKEN:
            sys.stderr.write(
                "ERROR: VULNERATOR_MCP_INBOUND_TOKEN vacío: el transporte "
                "streamable-http NO arranca sin token de entrada (fail-closed). "
                "Genera uno con 'openssl rand -hex 32' y ponlo en .env.\n"
            )
            sys.exit(1)
        import uvicorn

        app = _build_authenticated_app(INBOUND_TOKEN)
        uvicorn.run(app, host=HOST, port=PORT)
    else:
        # stdio: el cliente (Claude Code / Kiro) arranca el proceso por stdin/stdout.
        mcp.run()


if __name__ == "__main__":
    main()
