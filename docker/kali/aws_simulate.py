"""Runner AWS del foso OODA (STDLIB PURA) — construye y ejecuta el simulate IAM (inc4b).

Rol (architecture §1/§5): es la LÓGICA testeable del runner ``iam_simulate`` que expone
``kali_mcp_server`` como ``@mcp.tool`` ASÍNCRONA. Espejo de ``aws_exec.py`` (el runner del
``iam_enum``): vive en un módulo aparte que usa SOLO la librería estándar (sin ``mcp``, sin
``boto3``) para poder cubrirse con tests unitarios en el venv de dev, donde ni el SDK ni
boto3 están instalados. REUTILIZA ``tool_streaming`` (igual que ``aws_exec``); no duplica
mecánica de ejecución/logs/kill.

Qué provee:
  - ``build_iam_simulate_cmd(account_id, principal_arn, outfile)``: construye el argv (sin
    shell) ``[python3, aws_iam_simulate.py, --account, <id>, --principal-arn, <arn>, --out,
    <outfile>]``. Valida ``account_id`` (12 dígitos) Y ``principal_arn`` (forma
    ``arn:aws:iam::<acct>:...``) y RECHAZA fail-closed lo mal formado (N-untrusted: nunca se
    interpola entrada sin validar en un comando).
  - ``run_iam_simulate(account_id, principal_arn, execution_id, timeout)``: crea un tmpdir,
    construye el cmd, lo ejecuta con ``tool_streaming.run_tool_streaming`` (kill de
    grupo/timeout/logs al ring), lee el fichero de salida y devuelve
    ``{'status':'done',...,'output':<json-str>}`` o ``{'status':'failed',...,'error':...}``;
    limpia el tmpdir en ``finally``.

Es SÍNCRONO de un solo tiro (como ``aws_exec``): el simulate acotado es corto y su JSON único
lo stagea el EXECUTOR del core. A diferencia del enum, el simulate PUEDE fallar de forma
ESTRUCTURADA: si la credencial read-only no puede ni llamar a ``SimulatePrincipalPolicy``, el
script THIN escribe un JSON con un ``error`` claro (no revienta); este runner lo detecta y
devuelve ``failed`` (fail-closed), propagando ese ``error`` VERBATIM (dato NO confiable, F0.8).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import tool_streaming

# Script boto3 THIN que ejecuta el simulate real. Vive junto a este módulo en la imagen
# (COPY en el Dockerfile); se invoca como subproceso, NO se importa (boto3 no está en el
# venv de dev).
IAM_SIMULATE_SCRIPT = "aws_iam_simulate.py"

# Timeout por defecto del simulate IAM (segundos). El simulate acotado (9 acciones sobre un
# principal) es CORTO; se acota igualmente para no colgar el servidor. Overridable por entorno.
DEFAULT_TIMEOUT = int(os.environ.get("AWS_IAM_SIMULATE_TIMEOUT", "120"))

# Nombre del fichero de salida dentro del tmpdir efímero.
_OUTFILE_NAME = "iam-simulate.json"

# Forma de un ID de cuenta AWS: exactamente 12 dígitos (fail-closed, N-untrusted).
_ACCOUNT_RE = re.compile(r"^\d{12}$")

# Forma de un ARN de principal IAM: ``arn:aws:iam::<12 dígitos>:<recurso>`` (role/user/group).
# El conjunto de caracteres del recurso excluye metacaracteres de shell (defensa N-untrusted,
# aunque el argv va sin shell). El grupo de cuenta se compara luego con ``account_id``.
_PRINCIPAL_ARN_RE = re.compile(r"^arn:aws:iam::(\d{12}):[A-Za-z0-9+=,.@/_-]+$")


class AwsSimulateError(ValueError):
    """Entrada mal formada para el runner AWS (fail-closed; no se ejecuta nada)."""


def _validate_account_id(account_id: str) -> str:
    """Valida ``account_id`` con forma de cuenta AWS (12 dígitos) o lanza ``AwsSimulateError``."""
    if not isinstance(account_id, str) or not _ACCOUNT_RE.match(account_id.strip()):
        raise AwsSimulateError(
            f"account_id inválido: {account_id!r} (se esperan 12 dígitos de cuenta AWS)."
        )
    return account_id.strip()


def _validate_principal_arn(principal_arn: str, account_id: str) -> str:
    """Valida ``principal_arn`` (forma IAM) y que su cuenta coincida con ``account_id``.

    N-untrusted: el ARN llega del CONTROL (derivado de un candidato del grafo, dato NO
    confiable); nunca se interpola en un comando sin validar. Fail-closed: cualquier cosa que
    no sea un ARN IAM de ESTA cuenta se rechaza con un error claro, sin ejecutar el subproceso.
    """
    if not isinstance(principal_arn, str):
        raise AwsSimulateError(f"principal_arn inválido: {principal_arn!r} (se esperaba un ARN IAM).")
    match = _PRINCIPAL_ARN_RE.match(principal_arn.strip())
    if not match:
        raise AwsSimulateError(
            f"principal_arn inválido: {principal_arn!r} "
            "(se esperaba 'arn:aws:iam::<cuenta>:<recurso>')."
        )
    if match.group(1) != account_id.strip():
        raise AwsSimulateError(
            f"principal_arn fuera de la cuenta autorizada: {principal_arn!r} "
            f"(cuenta del scope: {account_id!r})."
        )
    return principal_arn.strip()


def _script_path() -> str:
    """Ruta absoluta del script de simulate, resuelta junto a este módulo (robusto al cwd)."""
    return str(Path(__file__).resolve().parent / IAM_SIMULATE_SCRIPT)


def build_iam_simulate_cmd(account_id: str, principal_arn: str, outfile: str) -> list[str]:
    """Construye el argv (sin shell) del simulate IAM; valida entradas fail-closed.

    Devuelve ``[python3, <ruta aws_iam_simulate.py>, --account, <id>, --principal-arn, <arn>,
    --out, <out>]``.
    """
    account = _validate_account_id(account_id)
    principal = _validate_principal_arn(principal_arn, account)
    return [
        "python3",
        _script_path(),
        "--account",
        account,
        "--principal-arn",
        principal,
        "--out",
        outfile,
    ]


def _ring_tail(execution_id: str, max_lines: int = 20) -> str:
    """Últimas líneas de salida de la ejecución (dato NO confiable, verbatim)."""
    page = tool_streaming.read(execution_id, 0)
    lines = page.get("lines", [])
    tail = lines[-max_lines:]
    return "\n".join(f"[{line['stream']}] {line['text']}" for line in tail)


def run_iam_simulate(
    account_id: str,
    principal_arn: str,
    execution_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ejecuta el simulate IAM read-only y devuelve el JSON crudo (SÍNCRONO, un solo tiro).

    Flujo: valida ``account_id`` + ``principal_arn`` (fail-closed) -> crea un tmpdir efímero ->
    construye el cmd -> ejecuta con ``tool_streaming.run_tool_streaming`` (kill de
    grupo/timeout, logs al ring por ``execution_id``) -> lee el fichero de salida. Devuelve
    ``{'status':'done','returncode':rc,'output':<json-string>}`` si hay salida legible SIN un
    ``error`` estructurado; ``{'status':'failed',...,'error':...}`` en timeout, sin salida, o si
    el JSON trae un ``error`` (p. ej. ``AccessDenied`` del propio Simulate). Limpia el tmpdir en
    ``finally``. El JSON crudo (``output``) lo stagea el EXECUTOR del core (plano de DATOS).
    """
    try:
        account = _validate_account_id(account_id)
        principal = _validate_principal_arn(principal_arn, account)
    except AwsSimulateError as exc:
        return {"status": "failed", "returncode": -1, "error": str(exc)}

    workdir = tempfile.mkdtemp(prefix="aws-iam-simulate-")
    try:
        outfile = str(Path(workdir) / _OUTFILE_NAME)
        cmd = build_iam_simulate_cmd(account, principal, outfile)
        returncode, timed_out, cancelled = tool_streaming.run_tool_streaming(
            cmd, execution_id, timeout=timeout, cwd=workdir
        )
        if cancelled:
            return {"status": "cancelled", "cancelled": True, "returncode": returncode}
        if timed_out:
            return {
                "status": "failed",
                "timed_out": True,
                "returncode": returncode,
                "error": (
                    f"iam_simulate excedió el timeout ({timeout}s). "
                    f"Últimas líneas: {_ring_tail(execution_id)}"
                ),
            }
        output = _read_output(outfile)
        if output is None:
            return {
                "status": "failed",
                "returncode": returncode,
                "error": (
                    f"iam_simulate no produjo salida legible (exit={returncode}): "
                    f"{_ring_tail(execution_id)}"
                ),
            }
        error = _structured_error(output)
        if error:
            # Fallo ESTRUCTURADO del simulate (p. ej. AccessDenied del propio Simulate): el
            # script THIN lo escribió como ``error`` en el JSON. Se propaga VERBATIM (F0.8:
            # dato NO confiable; se lee el estado, no se interpreta como instrucción).
            return {
                "status": "failed",
                "returncode": returncode,
                "error": f"iam_simulate: {error}",
            }
        return {
            "status": "done",
            "returncode": returncode,
            "output": output,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _read_output(outfile: str) -> str | None:
    """Lee el JSON de salida como texto crudo; ``None`` si no existe o está vacío.

    No se interpreta el contenido (dato NO confiable): se devuelve verbatim para que el
    executor del core lo stagee en el plano de DATOS.
    """
    path = Path(outfile)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text if text.strip() else None


def _structured_error(output: str) -> str | None:
    """Devuelve el ``error`` de nivel superior del JSON de salida, o ``None`` si no hay.

    Lectura de ESTADO del plano de control (no interpretación de instrucciones, F0.8): el
    script THIN escribe ``{"error": "..."}`` cuando el propio Simulate falla fail-closed. Si el
    output no es un JSON-objeto o no trae ``error``, se trata como éxito (``None``).
    """
    try:
        doc = json.loads(output)
    except (ValueError, TypeError):
        return None
    if isinstance(doc, dict):
        err = doc.get("error")
        if err:
            return str(err)
    return None
