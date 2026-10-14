"""Servidor `kali-mcp` de PentAgent — runtime determinista de tools en Kali.

Rol MCP (design.md "Doble rol MCP"):
  - Este proceso es el SERVIDOR `kali-mcp` que corre DENTRO del contenedor Kali.
    PentAgent (pentagent-core) lo consume como CLIENTE MCP vía `KALI_MCP_URL`.
  - NO es el servidor MCP que PentAgent *expone* a asistentes externos (ese vive
    en la API). No confundir.

Decisión de implementación (documentada — N12.4):
  Los servidores `kali-mcp` open-source existentes son puentes genéricos de
  "ejecuta cualquier comando en una terminal". Eso choca con la disciplina del
  MVP (R3/R4, scope-notes N2): la recolección v1 es *read-only*, determinista y
  acotada a Prowler; NO se expone ejecución de comandos arbitrarios. Por eso se
  implementa aquí un servidor MCP MÍNIMO sobre el SDK oficial `mcp` (FastMCP,
  transporte streamable-HTTP) que expone EXACTAMENTE la tool que el cliente de
  pentagent-core espera (`prowler_scan`) y una tool de información segura para el
  smoke test. Esto mantiene la frontera y encaja 1:1 con `KaliMcpClient` sin tocar
  los contratos.

Transporte: streamable-HTTP en `0.0.0.0:$KALI_MCP_PORT` (8900 por defecto), con el
endpoint MCP montado en la raíz (`/`) para que el cliente alcance el servidor por
`KALI_MCP_URL=http://kali:8900` sin configurar un path.

Disciplina arm64 (R3.2/R3.3, design.md 4.4): si Prowler no está disponible en esta
arquitectura, la tool `prowler_scan` NO finge: devuelve un error claro. El smoke
test (listar tools + `server_info`) refleja el estado real; la ausencia de una tool
esperada la detecta el cliente (`EXPECTED_TOOLS`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from typing import Any

# tool_streaming: primitivas de streaming en STDLIB PURA (ring buffer por
# execution_id + run_tool_streaming con muerte del grupo al expirar el timeout).
# Vive en módulo aparte a propósito: NO importa `mcp`/`anyio`, así su lógica se
# cubre con tests unitarios sin el SDK (ver docker/kali/tests/).
import tool_streaming

# Runner multi-herramienta genérico (N33), STDLIB PURA: registro por fuente
# (prowler/scoutsuite/cloudsplaining) + worker/finalize únicos. `prowler_scan` y
# `start_collection` delegan aquí (una ruta, no una copia por tool). A su vez reutiliza
# `prowler_artifact` (stage + puente SSE) para la fuente Prowler y `artifact_store`.
import collector_runner

# Runner AWS del foso OODA (F2), STDLIB PURA (sin `mcp`/`boto3`): construye y ejecuta el
# enum IAM read-only acotado (build_iam_enum_cmd + run_iam_enum) reutilizando
# `tool_streaming`. La tool `iam_enum` lanza este worker EN BACKGROUND (igual que las
# colecciones) y el cliente sondea con `get_scan_status`; su JSON crudo lo stagea el
# executor del core (plano de DATOS), por eso aquí NO se stagea.
import aws_exec

# Runner AWS del foso OODA (inc4b), STDLIB PURA (sin `mcp`/`boto3`): construye y ejecuta el
# simulate IAM read-only per-principal (build_iam_simulate_cmd + run_iam_simulate) reutilizando
# `tool_streaming`. Espejo de `aws_exec` para la tool `iam_simulate` (misma mecánica async).
import aws_simulate

# Runner de SHELL CRUDA (STDLIB PURA, sin `mcp`): ejecuta un comando arbitrario de la
# Kali vía `sh -c` reutilizando `tool_streaming` (kill de grupo/timeout/logs al ring).
# La tool `kali_exec` lanza este worker EN BACKGROUND (igual que iam_enum) y el cliente
# sondea con `get_scan_status` + `read_tool_logs`. Da al bucle OODA una Kali completa
# (encadenar, pipes, scripts propios, cualquier tool instalada). La AUTORIZACIÓN (scope/
# consentimiento/audit) la aplica el CORE ANTES de invocar; aquí solo se EJECUTA.
import shell_exec

# Plano de DATOS (N23), STDLIB PURA (sin `mcp`/`anyio`): servidor HTTP que SIRVE los
# artefactos staged al core por stream. (El staging/finalización vive en
# `collector_runner`/`prowler_artifact`, que `collector_runner` importa.)
import artifact_http

# SDK `mcp`: la imagen Kali fija `mcp<2` (1.x `FastMCP`, ver
# docker/kali/requirements.txt) a propósito, para hablar la MISMA era de protocolo
# que el cliente de pentagent-core. Este servidor usa la API 1.x `FastMCP`.
from mcp.server.fastmcp import FastMCP

# --- Configuración por entorno (overridable; defaults seguros) ---------------
HOST = os.environ.get("KALI_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("KALI_MCP_PORT", "8900"))
# Timeout de un escaneo Prowler (segundos). Evita colgar el servidor indefinidamente.
PROWLER_TIMEOUT = int(os.environ.get("PROWLER_TIMEOUT", "900"))

# Plano de DATOS (N23): el servidor HTTP de artefactos escucha en su propio puerto
# (DEDICADO, distinto del MCP 8900), sólo en la red interna docker (NO publicado al
# host). El token compartido es OBLIGATORIO (fail-closed): sin él no se arranca el
# canal de datos y el core cae al puente `read_scan_findings`.
ARTIFACTS_HOST = os.environ.get("KALI_ARTIFACTS_HOST", "0.0.0.0")
ARTIFACTS_PORT = int(os.environ.get("KALI_ARTIFACTS_PORT", "8901"))
ARTIFACTS_TOKEN = os.environ.get("KALI_ARTIFACTS_TOKEN", "")

# Nombre de la tool que el cliente de pentagent-core espera (PROWLER_TOOL_NAME).
# Si cambia aquí, cambia en packages/.../tools/kali_mcp.py (único punto de acople).
PROWLER_TOOL_NAME = "prowler_scan"

# Tool multi-herramienta (N33): lanza en vivo prowler/scoutsuite/cloudsplaining bajo el
# MISMO contrato no-bloqueante. `prowler_scan` queda como alias de delegación (compat).
# Si cambia aquí, cambia en packages/.../tools/kali_mcp.py (único punto de acople).
START_COLLECTION_TOOL_NAME = "start_collection"

# Tool genérica de lectura incremental de logs de cualquier ejecución (verbose en
# vivo). La consume FEAT-002 (API/UI) para mostrar la salida de Kali en directo.
READ_LOGS_TOOL_NAME = "read_tool_logs"

# Tool de sondeo del estado de un escaneo lanzado en background por `prowler_scan`.
# La consume FEAT-002 para resolver el flujo no-bloqueante (started -> running ->
# done/failed) por execution_id.
SCAN_STATUS_TOOL_NAME = "get_scan_status"

# Tool de lectura PAGINADA de los findings de un escaneo terminado. Existe para no
# superar el límite de 1 MiB de un único evento SSE del transporte streamable-http:
# `get_scan_status` al `done` ya NO lleva los findings inline (sólo `findings_count`)
# y el cliente los recupera por páginas con esta tool (mismo patrón cursor que los
# logs). La consume el core (kali<->core); no se expone en el servidor MCP público.
READ_FINDINGS_TOOL_NAME = "read_scan_findings"

# Tool interna kali<->core que CANCELA un escaneo en curso (N22.2): mata el grupo de
# proceso por execution_id reutilizando el kill existente de tool_streaming. La invoca
# el core (CoreServices.cancel_phase); NO se expone en el servidor MCP público.
CANCEL_SCAN_TOOL_NAME = "cancel_scan"

# Tool del runner AWS del foso OODA (F2): enum IAM read-only acotado, NO BLOQUEANTE (igual
# que las colecciones): lanza el enum en background por `execution_id` y devuelve en <2s
# con `{'execution_id','status':'started'}`; el cliente sondea con `get_scan_status` y lee
# la salida en vivo con `read_tool_logs`. Así el enum (p. ej. 153 roles) NO retiene el hilo
# de petición ni tumba el healthcheck del servidor. Al `done`, el JSON crudo del enum viaja
# inline en `output` (puente F2 ya registrado; stage-on-kali es F4) y lo stagea el executor
# del core (plano de DATOS). ACTIVE_SAFE: el gate de intrusividad del bucle OODA la autoriza
# (registro 'aws-exec' en config/mcp-servers.yaml).
IAM_ENUM_TOOL_NAME = "iam_enum"

# Tool del runner AWS del foso OODA (inc4b): simulate IAM read-only PER-PRINCIPAL. Dado el ARN
# de un principal de un candidato alcanzable, usa `iam:SimulatePrincipalPolicy` (API AWS
# READ-ONLY: NO modifica estado) para comprobar si ese principal PUEDE ejecutar un conjunto fijo
# de acciones de privesc clásicas. NO BLOQUEANTE EXACTAMENTE como `iam_enum`: lanza el worker en
# background por `execution_id` y devuelve en <2s con `{'execution_id','status':'started'}`; el
# cliente sondea con `get_scan_status` y lee la salida en vivo con `read_tool_logs`. Al `done`,
# el JSON crudo del simulate viaja inline en `output` (mismo puente que el enum) y lo stagea el
# executor del core (plano de DATOS). ACTIVE_SAFE: read-only pero acción activa contra AWS, mismo
# nivel que `iam_enum` (registro 'aws-exec' en config/mcp-servers.yaml).
IAM_SIMULATE_TOOL_NAME = "iam_simulate"

# Tool de SHELL CRUDA de la Kali (decisión del operador): ejecuta un comando arbitrario
# vía `sh -c` en el contenedor Kali. A diferencia de los runners deterministas/acotados
# (iam_enum/iam_simulate/prowler), NO restringe el comando: da al bucle OODA una Kali
# completa (herramientas instaladas, pipes, scripts propios). NO BLOQUEANTE como los
# demás runners (background + poll). INTRUSIVE: la ejecución de comandos arbitrarios es
# potencialmente intrusiva/destructiva, así que su registro MCP (config/mcp-servers.yaml)
# va a un nivel que el gate de intrusividad del core gobierna; el scope/consentimiento/
# audit los aplica el CORE ANTES de invocar, no esta tool.
KALI_EXEC_TOOL_NAME = "kali_exec"

# Variante BLOQUEANTE de la shell cruda para el OODA conducido por LLM: a diferencia de
# `kali_exec` (no bloqueante + poll), `kali_run` ejecuta el comando SINCRONAMENTE y
# DEVUELVE la salida completa en UNA sola llamada (con timeout). Pensada para que Hermes
# la use como una tool normal sin tener que orquestar el patrón started->poll->read.
# Comparte el MISMO runner (`shell_exec.run_shell`): NO duplica lógica, solo cambia la
# forma de entrega (bloqueante vs background). ADVERTENCIA HONESTA: este producto NO tiene
# core; NO hay gates de scope/consentimiento/audit antes de ejecutar.
KALI_RUN_TOOL_NAME = "kali_run"

# El endpoint MCP se monta en la raíz para que KALI_MCP_URL=http://kali:8900
# (sin path) funcione tal cual con el cliente (streamable-HTTP del SDK oficial).
mcp = FastMCP("kali-mcp", host=HOST, port=PORT, streamable_http_path="/")


def _prowler_binary() -> str | None:
    """Devuelve la ruta del binario `prowler` si está disponible, o None.

    No se finge disponibilidad (design.md 4.4): si no está, quien llame lo reporta.
    """
    return shutil.which("prowler")


@mcp.tool()
def server_info() -> dict[str, Any]:
    """Operación SEGURA y NO intrusiva para el smoke test (R3.2).

    No toca AWS ni ejecuta herramientas: solo informa del estado del runtime
    (arquitectura, si Prowler está presente y su versión). El cliente usa el
    *listado de tools* como smoke test principal; esta tool añade un chequeo
    seguro adicional si se quiere invocar.
    """
    prowler_path = _prowler_binary()
    version = None
    if prowler_path:
        try:
            proc = subprocess.run(  # noqa: S603 - binario fijo, sin input de usuario
                [prowler_path, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            version = (proc.stdout or proc.stderr).strip() or None
        except Exception:  # noqa: BLE001 - informativo, no fatal
            version = None
    # Disponibilidad REAL por collector (which de prowler/scout/cloudsplaining); no se
    # finge en arm64 (R3.2/4.4): lo que no esté se reporta ausente y su collection falla
    # claro en el core.
    collectors = collector_runner.available_collectors()
    return {
        "server": "kali-mcp",
        "machine": os.uname().machine,
        "prowler_available": prowler_path is not None,
        "prowler_version": version,
        "collectors": collectors,
        "tools": [
            PROWLER_TOOL_NAME,
            START_COLLECTION_TOOL_NAME,
            SCAN_STATUS_TOOL_NAME,
            READ_LOGS_TOOL_NAME,
            READ_FINDINGS_TOOL_NAME,
            CANCEL_SCAN_TOOL_NAME,
            IAM_ENUM_TOOL_NAME,
            IAM_SIMULATE_TOOL_NAME,
            KALI_EXEC_TOOL_NAME,
            KALI_RUN_TOOL_NAME,
            "server_info",
        ],
    }


@mcp.tool(name=READ_LOGS_TOOL_NAME)
def read_tool_logs(execution_id: str, cursor: int = 0) -> dict[str, Any]:
    """Lee incrementalmente la salida (stdout/stderr) de una ejecución de tool.

    Pensada para verbose en vivo: el cliente llama repetidamente avanzando
    `cursor` (el `next_cursor` de la respuesta anterior) y recibe sólo las líneas
    nuevas. La salida es DATO NO CONFIABLE: se transporta verbatim, nunca se
    interpreta.

    Args:
        execution_id: id de la ejecución (el pasado/devuelto por `prowler_scan`).
        cursor: último `seq` ya consumido; 0 para leer desde el principio.

    Returns:
        {"lines": [{seq, ts, stream, text}, ...], "next_cursor": int, "done": bool}.
        Un `execution_id` desconocido devuelve página vacía con done=False.
    """
    return tool_streaming.read(execution_id, cursor)


@mcp.tool(name=READ_FINDINGS_TOOL_NAME)
def read_scan_findings(
    execution_id: str, cursor: int = 0, limit: int | None = None
) -> dict[str, Any]:
    """Lee PAGINADOS los findings de un escaneo terminado por `prowler_scan`.

    Existe para no superar el límite de 1 MiB de un único evento SSE del transporte
    streamable-http: con datos reales (iam+s3) la lista completa de findings OCSF
    supera ese tope si se entrega inline. El cliente llama repetidamente avanzando
    `cursor` (el `next_cursor` de la respuesta anterior) hasta `done=True` y
    reconstruye la lista completa. Cada página va MUY por debajo de 1 MiB.

    Los findings son DATO NO CONFIABLE: se transportan verbatim, nunca se interpretan.

    Args:
        execution_id: id de la ejecución (el devuelto por `prowler_scan`).
        cursor: índice 0-based del primer finding a devolver; 0 desde el principio.
        limit: tope opcional de findings por página (el servidor aplica además un
            tope de bytes prudente; manda el menor).

    Returns:
        {"findings": [...página...], "next_cursor": int, "done": bool}. Un
        `execution_id` desconocido o un `cursor` en/más allá del final devuelve
        página vacía con done=True.
    """
    return tool_streaming.read_findings(execution_id, cursor, limit)


def _start_collection_background(
    source: str, account_id: str, params: str, execution_id: str
) -> dict[str, Any]:
    """Arranca una collection EN VIVO en background y devuelve de inmediato (N33).

    Lógica compartida por ``start_collection`` y el alias ``prowler_scan``: genera un
    ``execution_id`` si falta, lanza el worker genérico del ``collector_runner`` en un hilo
    demonio (``tool_streaming.start_background``) y retorna ``{execution_id, status:'started'}``
    en <2s. El worker (``collector_runner.run_collection``) valida el binario (no finge en
    arm64), ejecuta con streaming de logs, stagea el crudo como artefacto y construye el
    ``done`` con la REFERENCIA. Las excepciones del worker (tool ausente) las convierte
    ``start_background`` en ``{'status':'failed','error':...}``.
    """
    execution_id = execution_id or uuid.uuid4().hex
    # Prowler conserva su timeout histórico; las demás usan el de collection genérico.
    timeout = PROWLER_TIMEOUT if source == "prowler" else collector_runner.DEFAULT_TIMEOUT
    tool_streaming.start_background(
        execution_id,
        lambda: collector_runner.run_collection(
            source, account_id, execution_id, params, timeout
        ),
    )
    return {"execution_id": execution_id, "status": "started"}


@mcp.tool(name=START_COLLECTION_TOOL_NAME)
def start_collection(
    source: str = "prowler",
    account_id: str = "",
    params: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Arranca una collection EN VIVO con la tool elegida (read-only) y DEVUELVE ya (N33).

    Multi-herramienta bajo el MISMO contrato no-bloqueante que ``prowler_scan``: ``source``
    ∈ {prowler, scoutsuite, cloudsplaining}. Determinista (corre el binario real, NO el
    LLM); credenciales AWS read-only del entorno del contenedor; el scope ya lo valida
    PentAgent (scope-guard fail-closed) ANTES de llamar. El escaneo corre en background
    (``execution_id``); el cliente sondea con ``get_scan_status`` y lee logs con
    ``read_tool_logs``. El crudo se deposita en la capa de artefactos (plano de DATOS); el
    ``done`` sólo lleva la REFERENCIA. Si la tool no está disponible en esta arquitectura,
    la ejecución termina en ``failed`` con el motivo (no se finge).

    Args:
        source: tool a lanzar (``prowler`` por defecto, compat).
        account_id: cuenta AWS autorizada (target).
        params: parámetros extra opcionales (JSON string); reservado.
        execution_id: id para seguir salida/estado en vivo; si falta se genera.

    Returns:
        {'execution_id': str, 'status': 'started'} de inmediato (<2s).
    """
    return _start_collection_background(source, account_id, params, execution_id)


@mcp.tool(name=PROWLER_TOOL_NAME)
def prowler_scan(
    account_id: str, output_format: str = "json-ocsf", execution_id: str = ""
) -> dict[str, Any]:
    """Alias de ``start_collection(source='prowler')`` (compat con clientes existentes).

    Arranca un escaneo Prowler (read-only) en segundo plano y devuelve en <2s con
    ``{'execution_id','status':'started'}``. Es DELEGACIÓN, no una copia: usa el mismo
    worker/finalize genérico (``collector_runner``) y, para Prowler, el mismo
    ``prowler_artifact`` (stage + puente SSE de compat). ``output_format`` se acepta por
    contrato; el formato efectivo es OCSF.
    """
    # output_format se acepta por contrato pero no afecta (Prowler siempre OCSF); params
    # vacío (el builder de Prowler no usa params).
    return _start_collection_background("prowler", account_id, "", execution_id)


@mcp.tool(name=SCAN_STATUS_TOOL_NAME)
def get_scan_status(execution_id: str) -> dict[str, Any]:
    """Sondea el estado de un escaneo lanzado por `prowler_scan`.

    Returns el dict de estado por `execution_id`:
      - {'status':'running'} mientras el worker corre.
      - {'status':'done','returncode':int,'timed_out':False,'findings_count':int,
        'artifact':{execution_id,name,size,sha256,findings_count}} al OK. Los findings
        NO viajan inline (reventaría el límite de 1 MiB del evento SSE con datos
        reales): el core baja el artefacto CRUDO por el plano de DATOS (HTTP chunked)
        usando la REFERENCIA `artifact`. Puente de compat (N21): si no hay `artifact`
        (staging falló o kali antigua) se recuperan paginados con `read_scan_findings`.
      - {'status':'failed','returncode'?:int,'timed_out'?:bool,'error':str} si falla
        el worker o hay timeout.
      - {'status':'unknown'} si el execution_id no existe (no revienta).

    `error` es DATO NO CONFIABLE: se transporta verbatim.
    """
    return tool_streaming.get_status(execution_id)


@mcp.tool(name=CANCEL_SCAN_TOOL_NAME)
def cancel_scan(execution_id: str) -> dict[str, Any]:
    """Cancela un escaneo en curso lanzado por `prowler_scan` (N22.2).

    Mata el GRUPO de procesos del escaneo por `execution_id` (reutiliza el kill
    SIGTERM->SIGKILL ya existente, sin huérfanos — N18.3) y marca la cancelación. Es
    una tool interna kali<->core: la invoca el core (CoreServices.cancel_phase) cuando
    el usuario para una fase; NO ejecuta nada contra AWS.

    Returns:
        {'execution_id': str, 'status': 'cancelling'|'not_running'}. 'cancelling' si
        había un proceso vivo al que se le pidió el kill; 'not_running' si no (ya
        terminó o nunca arrancó). El worker publicará el estado terminal 'cancelled'
        vía `get_scan_status` cuando el proceso muera.
    """
    killed = tool_streaming.request_cancel(execution_id)
    return {
        "execution_id": execution_id,
        "status": "cancelling" if killed else "not_running",
    }


@mcp.tool(name=IAM_ENUM_TOOL_NAME)
def iam_enum(
    account_id: str, params: str = "", execution_id: str = ""
) -> dict[str, Any]:
    """Enum IAM read-only ACOTADO de la cuenta AWS autorizada (runner del foso, F2).

    Tool del bucle OODA (ACTIVE_SAFE; el gate de intrusividad del core la autoriza, no se
    autoriza a sí misma): el scope ya lo validó PentAgent ANTES de llamar. Es NO BLOQUEANTE,
    EXACTAMENTE como ``start_collection``: lanza ``aws_exec.run_iam_enum`` en un hilo demonio
    con ``tool_streaming.start_background`` y DEVUELVE en <2s con
    ``{'execution_id','status':'started'}``; así el enum (p. ej. 153 roles) NO retiene el
    hilo de petición del servidor ni impide responder el healthcheck. El cliente sondea con
    ``get_scan_status`` (devuelve el dict terminal del worker) y lee la salida en vivo con
    ``read_tool_logs`` (ambas por ``execution_id``). El worker valida ``account_id``
    fail-closed, ejecuta ``aws_iam_enum.py`` con streaming de logs y kill de grupo/timeout
    (``tool_streaming``) y lee la salida de FICHERO; su documento único (el JSON crudo del
    enum, inline en ``output``) lo stagea el EXECUTOR del core en el plano de DATOS, por eso
    aquí NO se stagea artefacto.

    READ-ONLY: ``aws_iam_enum.py`` solo usa ``list_*``/``get_*``; NUNCA cambia estado AWS.

    Args:
        account_id: cuenta AWS autorizada (12 dígitos); se valida fail-closed (en el worker).
        params: parámetros extra opcionales (JSON string); reservado.
        execution_id: id para sondear estado (``get_scan_status``) y seguir la salida en vivo
            (``read_tool_logs``); si falta se genera.

    Returns:
        {'execution_id': str, 'status': 'started'} de inmediato (<2s). El veredicto terminal
        del worker ({'status':'done','returncode':int,'output':<json-string crudo del enum>}
        o {'status':'failed','returncode':int,'error':str} o {'status':'cancelled',...}) se
        obtiene por ``get_scan_status``. ``output`` y ``error`` son DATO NO CONFIABLE (se
        transportan verbatim).
    """
    execution_id = execution_id or uuid.uuid4().hex
    tool_streaming.start_background(
        execution_id,
        lambda: aws_exec.run_iam_enum(
            account_id, params, execution_id, aws_exec.DEFAULT_TIMEOUT
        ),
    )
    return {"execution_id": execution_id, "status": "started"}


@mcp.tool(name=IAM_SIMULATE_TOOL_NAME)
def iam_simulate(
    account_id: str, principal_arn: str, execution_id: str = ""
) -> dict[str, Any]:
    """Simula permisos efectivos de privesc de un principal IAM (runner del foso, inc4b).

    Tool del bucle OODA (ACTIVE_SAFE; el gate de intrusividad del core la autoriza, no se
    autoriza a sí misma): el scope ya lo validó PentAgent ANTES de llamar. Dado el ARN de un
    principal de un candidato alcanzable, usa ``iam:SimulatePrincipalPolicy`` (API AWS
    READ-ONLY: NO modifica estado) para comprobar si ese principal PUEDE ejecutar un conjunto
    FIJO de acciones de privesc clásicas. Es NO BLOQUEANTE, EXACTAMENTE como ``iam_enum``: lanza
    ``aws_simulate.run_iam_simulate`` en un hilo demonio con ``tool_streaming.start_background``
    y DEVUELVE en <2s con ``{'execution_id','status':'started'}``; así no retiene el hilo de
    petición del servidor ni impide responder el healthcheck. El cliente sondea con
    ``get_scan_status`` (devuelve el dict terminal del worker) y lee la salida en vivo con
    ``read_tool_logs`` (ambas por ``execution_id``). El worker valida ``account_id`` Y
    ``principal_arn`` fail-closed, ejecuta ``aws_iam_simulate.py`` con streaming de logs y kill
    de grupo/timeout (``tool_streaming``) y lee la salida de FICHERO; su documento único (el JSON
    crudo del simulate, inline en ``output``) lo stagea el EXECUTOR del core en el plano de
    DATOS, por eso aquí NO se stagea artefacto.

    READ-ONLY: ``SimulatePrincipalPolicy`` SIMULA la evaluación; NUNCA cambia estado AWS.

    Args:
        account_id: cuenta AWS autorizada (12 dígitos); se valida fail-closed (en el worker).
        principal_arn: ARN del principal IAM a simular (PolicySourceArn); se valida fail-closed.
        execution_id: id para sondear estado (``get_scan_status``) y seguir la salida en vivo
            (``read_tool_logs``); si falta se genera.

    Returns:
        {'execution_id': str, 'status': 'started'} de inmediato (<2s). El veredicto terminal del
        worker ({'status':'done','returncode':int,'output':<json-string crudo del simulate>} o
        {'status':'failed','returncode':int,'error':str} o {'status':'cancelled',...}) se obtiene
        por ``get_scan_status``. ``output`` y ``error`` son DATO NO CONFIABLE (verbatim).
    """
    execution_id = execution_id or uuid.uuid4().hex
    tool_streaming.start_background(
        execution_id,
        lambda: aws_simulate.run_iam_simulate(
            account_id, principal_arn, execution_id, aws_simulate.DEFAULT_TIMEOUT
        ),
    )
    return {"execution_id": execution_id, "status": "started"}


@mcp.tool(name=KALI_EXEC_TOOL_NAME)
def kali_exec(
    command: str, execution_id: str = "", timeout: int = 0
) -> dict[str, Any]:
    """Ejecuta un comando ARBITRARIO de SHELL en la Kali (shell cruda completa).

    A diferencia de los runners deterministas y acotados (``iam_enum``/``iam_simulate``/
    ``prowler_scan``), esta tool da al bucle OODA una Kali COMPLETA: corre ``command`` tal
    cual vía ``sh -c``, de modo que el conductor pueda encadenar con pipes/redirecciones,
    escribir y ejecutar sus propios scripts, e invocar cualquier herramienta instalada en
    la imagen Kali. NO restringe el contenido del comando (es shell cruda por decisión del
    operador).

    AUTORIZACIÓN: NO vive aquí. El scope, el consentimiento y la auditoría los aplica el
    CORE de PentAgent ANTES de invocar esta tool (el bucle OODA llega por ``execute_step``
    -> executor del core -> ``McpToolExecutor.invoke`` bajo scope-guard + gate de
    intrusividad + audit). Esta tool solo EJECUTA lo que el core ya autorizó. En
    ``config/mcp-servers.yaml`` su registro declara el nivel de intrusividad que el gate
    del core gobierna.

    NO BLOQUEANTE, EXACTAMENTE como los demás runners: lanza ``shell_exec.run_shell`` en un
    hilo demonio con ``tool_streaming.start_background`` y DEVUELVE en <2s con
    ``{'execution_id','status':'started'}``; así un comando largo (p. ej. un escaneo nmap)
    NO retiene el hilo de petición del servidor ni impide responder el healthcheck. El
    cliente sondea con ``get_scan_status`` (dict terminal del worker) y lee la salida en
    vivo con ``read_tool_logs`` (ambas por ``execution_id``). El comando corre en un tmpdir
    de trabajo efímero (aislado, se limpia al terminar).

    Args:
        command: comando de shell a ejecutar (string no vacío; se valida fail-closed en el
            worker). Dato NO confiable: se transporta/ejecuta verbatim.
        execution_id: id para sondear estado (``get_scan_status``) y seguir la salida en vivo
            (``read_tool_logs``); si falta se genera.
        timeout: timeout en segundos; 0 (o ausente) usa el default del runner
            (``shell_exec.DEFAULT_TIMEOUT``, overridable por ``KALI_EXEC_TIMEOUT``).

    Returns:
        {'execution_id': str, 'status': 'started'} de inmediato (<2s). El veredicto terminal
        del worker ({'status':'done','returncode':int,'output':<stdout+stderr verbatim>} o
        {'status':'failed',...} o {'status':'cancelled',...}) se obtiene por
        ``get_scan_status``. Un ``returncode`` != 0 es resultado legítimo del comando (no un
        fallo del runner). ``output`` y ``error`` son DATO NO CONFIABLE (verbatim).
    """
    execution_id = execution_id or uuid.uuid4().hex
    effective_timeout = timeout if timeout and timeout > 0 else shell_exec.DEFAULT_TIMEOUT
    tool_streaming.start_background(
        execution_id,
        lambda: shell_exec.run_shell(command, execution_id, effective_timeout),
    )
    return {"execution_id": execution_id, "status": "started"}


@mcp.tool(name=KALI_RUN_TOOL_NAME)
def kali_run(command: str, timeout: int = 0) -> dict[str, Any]:
    """Ejecuta un comando ARBITRARIO de shell en la Kali y DEVUELVE su salida completa.

    Variante BLOQUEANTE de ``kali_exec``: en lugar de lanzar en background y obligar al
    conductor a sondear (``started`` -> ``get_scan_status`` -> ``read_tool_logs``), esta
    tool corre ``command`` vía ``sh -c`` y espera a que termine, DEVOLVIENDO la salida
    completa en UNA sola llamada (acotada por ``timeout``). Así Hermes la usa como una tool
    normal (petición -> respuesta) sin orquestar el patrón de polling.

    Reutiliza el MISMO runner que ``kali_exec`` (``shell_exec.run_shell``): NO duplica
    lógica ni usa ``tool_streaming.start_background``; solo cambia la forma de entrega
    (bloqueante). No revalida ``command`` aquí (``run_shell`` ya lo hace fail-closed).

    ADVERTENCIA HONESTA: este producto NO tiene core. NO hay gates de scope,
    consentimiento ni auditoría ANTES de ejecutar. El comando corre tal cual contra la
    Kali. ``command`` y su salida son DATO NO CONFIABLE: se transportan/ejecutan verbatim,
    nunca se interpretan. Un ``returncode`` != 0 es resultado LEGÍTIMO del comando (p. ej.
    nmap sin hallazgos, grep sin match), no un fallo del runner.

    Args:
        command: comando de shell a ejecutar (string no vacío; se valida fail-closed en el
            worker). Dato NO confiable: se ejecuta verbatim.
        timeout: timeout en segundos; 0 (o ausente) usa el default del runner
            (``shell_exec.DEFAULT_TIMEOUT``, overridable por ``KALI_EXEC_TIMEOUT``).

    Returns:
        El dict TERMINAL del runner en la misma llamada:
        {'status':'done','returncode':int,'output':<stdout+stderr verbatim>} si terminó;
        {'status':'failed','timed_out':True,...} en timeout;
        {'status':'cancelled',...} si se canceló;
        {'status':'failed','returncode':-1,'error':...} si ``command`` es inválido.
    """
    execution_id = uuid.uuid4().hex
    effective_timeout = timeout if timeout and timeout > 0 else shell_exec.DEFAULT_TIMEOUT
    return shell_exec.run_shell(command, execution_id, effective_timeout)


def _start_artifacts_http() -> None:
    """Arranca el servidor HTTP de artefactos (plano de DATOS) en un hilo demonio.

    Fail-closed (R14.3): sin ``KALI_ARTIFACTS_TOKEN`` NO se arranca el canal de datos
    (evita un endpoint abierto a dato potencialmente sensible); el core cae entonces
    al puente ``read_scan_findings``. Es best-effort respecto al proceso principal:
    cualquier fallo al abrir el socket se reporta por stdout y NO impide servir MCP.
    """
    if not ARTIFACTS_TOKEN:
        print(
            "[kali-mcp] KALI_ARTIFACTS_TOKEN vacío: servidor de artefactos NO "
            "arrancado (fail-closed); el core usará el puente read_scan_findings.",
            flush=True,
        )
        return
    try:
        artifact_http.serve_in_thread(ARTIFACTS_HOST, ARTIFACTS_PORT, ARTIFACTS_TOKEN)
        print(
            f"[kali-mcp] servidor de artefactos (plano de datos) en "
            f"{ARTIFACTS_HOST}:{ARTIFACTS_PORT}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - no debe impedir arrancar MCP
        print(f"[kali-mcp] no se pudo arrancar el servidor de artefactos: {exc}", flush=True)


if __name__ == "__main__":
    # Plano de DATOS primero (hilo demonio, no bloquea), luego el plano de CONTROL.
    _start_artifacts_http()
    # Transporte streamable-HTTP (el que consume KaliMcpClient por defecto).
    mcp.run(transport="streamable-http")
