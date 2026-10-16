"""Lógica PURA del wrapper MCP (sin `mcp`/`httpx`): scope-folding + validación de entrada.

Vive aparte de ``vulnerator_mcp_server.py`` a propósito (igual que ``scope_guard``/
``tool_streaming`` viven aparte de ``kali_mcp_server``): ese módulo importa `mcp` (FastMCP),
que no está en el venv de dev. Estas funciones son composición de strings y validación de
forma, así que se cubren con tests unitarios SIN el SDK.

Disciplina: ``scope`` es REQUERIDO pero ADVISORY. El wrapper NO impone scope ni añade
seguridad; solo DOBLA el scope dentro del prompt como metadato de claridad del operador.
``input``/``scope``/``run_id`` son DATO NO CONFIABLE: aquí solo se validan de forma
(presencia/no-vacío) y, en el caso de ``scope``, se concatenan al prompt verbatim.
"""

from __future__ import annotations

from typing import Any, Optional

# Prefijo con el que se dobla el scope dentro del input (metadato ADVISORY, no un guard).
SCOPE_PREFIX = "AUTHORIZED SCOPE (operator-declared, advisory):"


def invalid_request(message: str) -> dict[str, Any]:
    """Sobre de error OpenAI-style para fallos de validación LOCALES (sin llamar upstream)."""
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_request",
        }
    }


def fold_scope_into_input(input: str, scope: str) -> str:
    """Dobla ``scope`` dentro del ``input`` como una línea delimitada (design §5).

    NO impone nada: es un afordance de claridad para el operador que conduce el cliente MCP.
    Formato: una línea ``AUTHORIZED SCOPE ...: <scope>``, línea en blanco, luego el input
    original verbatim.
    """
    return "%s %s\n\n%s" % (SCOPE_PREFIX, scope, input)


def validate_start_pentest(input: Any, scope: Any) -> Optional[dict[str, Any]]:
    """Valida ``input``/``scope`` de ``start_pentest``. Devuelve un sobre de error o None.

    Ambos requeridos, string no vacío (tras strip). Vacío/no-string -> ``invalid_request``
    SIN llamada upstream (fail-closed). La longitud NO se capa (Hermes es dueño de los
    límites del prompt).
    """
    if not isinstance(input, str) or not input.strip():
        return invalid_request("'input' es requerido y no puede estar vacío")
    if not isinstance(scope, str) or not scope.strip():
        return invalid_request("'scope' es requerido y no puede estar vacío (advisory)")
    return None


def validate_run_id(run_id: Any) -> Optional[dict[str, Any]]:
    """Valida que ``run_id`` sea un string no vacío. Devuelve un sobre de error o None.

    NO valida el formato ``run_`` (Hermes es dueño del espacio de ids): se reenvía y se deja
    que upstream responda ``run_not_found`` si no existe.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        return invalid_request("'run_id' es requerido y no puede estar vacío")
    return None


def validate_steer(run_id: Any, input: Any) -> Optional[dict[str, Any]]:
    """Valida ``run_id`` + ``input`` de ``steer_run``. Devuelve un sobre de error o None."""
    err = validate_run_id(run_id)
    if err is not None:
        return err
    if not isinstance(input, str) or not input.strip():
        return invalid_request("'input' es requerido y no puede estar vacío")
    return None


def clamp_cursor(cursor: Any) -> int:
    """Normaliza el cursor a un int >= 0 (negativo/no-int -> 0)."""
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        return 0
    return cursor


def normalize_optional_positive(value: Any) -> Optional[Any]:
    """Normaliza max_events/wait_seconds: no-positivo o no-numérico -> None (usa el default interno)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return value
    return None
