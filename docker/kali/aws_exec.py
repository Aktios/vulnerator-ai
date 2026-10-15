"""Runner AWS (STDLIB PURA) — construye y ejecuta el enum IAM read-only.

Es la LÓGICA testeable del runner ``iam_enum`` que expone ``kali_mcp_server`` como
``@mcp.tool`` SÍNCRONA. Igual que ``collector_runner`` / ``tool_streaming``, vive en un
módulo aparte que usa SOLO la librería estándar (sin ``mcp``, sin ``boto3``) para poder
cubrirse con tests unitarios en el venv de dev, donde ni el SDK ni boto3 están instalados.

Qué provee:
  - ``build_iam_enum_cmd(account_id, params, outfile)``: construye el argv (sin shell)
    ``[python3, aws_iam_enum.py, --account, <id>, --out, <outfile>]``. Valida ``account_id``
    con forma de cuenta AWS (12 dígitos) y RECHAZA fail-closed lo mal formado (nunca se
    interpola entrada sin validar en un comando).
  - ``run_iam_enum(account_id, params, execution_id, timeout)``: crea un tmpdir, construye
    el cmd, lo ejecuta con ``tool_streaming.run_tool_streaming`` (kill de grupo/timeout/logs
    al ring), lee el fichero de salida y devuelve ``{'status':'done',...,'output':<json-str>}``
    o ``{'status':'failed',...,'error':<diagnóstico>}``; limpia el tmpdir en ``finally``.

Es SÍNCRONO de un solo tiro (a diferencia del collector async): el enum IAM acotado es
corto y su JSON único viaja inline en ``output``; aquí NO se stagea artefacto.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import tool_streaming

# Script boto3 THIN que ejecuta el enum real. Vive junto a este módulo en la imagen
# (COPY en el Dockerfile); se invoca como subproceso, NO se importa (boto3 no está en
# el venv de dev).
IAM_ENUM_SCRIPT = "aws_iam_enum.py"

# Timeout por defecto del enum IAM (segundos). El enum acotado es corto; aun así se acota
# para no colgar el servidor. Overridable por entorno.
DEFAULT_TIMEOUT = int(os.environ.get("AWS_IAM_ENUM_TIMEOUT", "300"))

# Nombre del fichero de salida dentro del tmpdir efímero.
_OUTFILE_NAME = "iam-enum.json"

# Forma de un ID de cuenta AWS: exactamente 12 dígitos (fail-closed, N-untrusted).
_ACCOUNT_RE = re.compile(r"^\d{12}$")


class AwsExecError(ValueError):
    """Entrada mal formada para el runner AWS (fail-closed; no se ejecuta nada)."""


def _validate_account_id(account_id: str) -> str:
    """Valida ``account_id`` con forma de cuenta AWS (12 dígitos) o lanza ``AwsExecError``.

    N-untrusted: el ``account_id`` llega del CONTROL (potencialmente no confiable); nunca se
    interpola en un comando sin validar. Fail-closed: cualquier cosa que no sea 12 dígitos
    se rechaza con un error claro, sin ejecutar el subproceso.
    """
    if not isinstance(account_id, str) or not _ACCOUNT_RE.match(account_id.strip()):
        raise AwsExecError(
            f"account_id inválido: {account_id!r} (se esperan 12 dígitos de cuenta AWS)."
        )
    return account_id.strip()


def _script_path() -> str:
    """Ruta absoluta del script de enum, resuelta junto a este módulo (robusto al cwd)."""
    return str(Path(__file__).resolve().parent / IAM_ENUM_SCRIPT)


def build_iam_enum_cmd(account_id: str, params: Any, outfile: str) -> list[str]:
    """Construye el argv (sin shell) del enum IAM; valida ``account_id`` fail-closed.

    ``params`` se acepta por contrato (reservado para acotaciones futuras); hoy no altera
    el comando. Devuelve ``[python3, <ruta aws_iam_enum.py>, --account, <id>, --out, <out>]``.
    """
    account = _validate_account_id(account_id)
    return [
        "python3",
        _script_path(),
        "--account",
        account,
        "--out",
        outfile,
    ]


def _ring_tail(execution_id: str, max_lines: int = 20) -> str:
    """Últimas líneas de salida de la ejecución (dato NO confiable, verbatim)."""
    page = tool_streaming.read(execution_id, 0)
    lines = page.get("lines", [])
    tail = lines[-max_lines:]
    return "\n".join(f"[{line['stream']}] {line['text']}" for line in tail)


def run_iam_enum(
    account_id: str,
    params: Any = "",
    execution_id: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Ejecuta el enum IAM read-only y devuelve el JSON crudo (SÍNCRONO, un solo tiro).

    Flujo: valida ``account_id`` (fail-closed) -> crea un tmpdir efímero -> construye el
    cmd -> ejecuta con ``tool_streaming.run_tool_streaming`` (kill de grupo/timeout, logs al
    ring por ``execution_id``) -> lee el fichero de salida. Devuelve
    ``{'status':'done','returncode':rc,'output':<json-string>}`` si hay salida legible, o
    ``{'status':'failed','returncode':rc,'error':<diagnóstico con cola del ring>}`` en
    timeout/sin salida. Limpia el tmpdir en ``finally``. El JSON crudo viaja inline en
    ``output``; aquí NO se stagea artefacto.
    """
    try:
        account = _validate_account_id(account_id)
    except AwsExecError as exc:
        return {"status": "failed", "returncode": -1, "error": str(exc)}

    workdir = tempfile.mkdtemp(prefix="aws-iam-enum-")
    try:
        outfile = str(Path(workdir) / _OUTFILE_NAME)
        cmd = build_iam_enum_cmd(account, params, outfile)
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
                    f"iam_enum excedió el timeout ({timeout}s). "
                    f"Últimas líneas: {_ring_tail(execution_id)}"
                ),
            }
        output = _read_output(outfile)
        if output is None:
            return {
                "status": "failed",
                "returncode": returncode,
                "error": (
                    f"iam_enum no produjo salida legible (exit={returncode}): "
                    f"{_ring_tail(execution_id)}"
                ),
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

    No se interpreta el contenido (dato NO confiable): se devuelve verbatim.
    """
    path = Path(outfile)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text if text.strip() else None
