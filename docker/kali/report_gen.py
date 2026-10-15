"""Generador del informe formal de pentest (Markdown) desde el store de hallazgos.

Produce el ENTREGABLE al cerrar un engagement, complementando a ``ooda-report-handoff``
(que solo da el resumen conversacional DURANTE el engagement). LEE el registro durable de
hallazgos (``finding_store``) en vez de reconstruir desde la conversación, y escribe un
informe Markdown que se deja como ARTEFACTO en el plano de datos (``/artifacts``), para
descargarlo por el canal HTTP (:8901) sin sacar nada por el MCP.

Patrón igual que el resto de scripts horneados (Prowler/Pacu): STDLIB PURA, se invoca con
``kali_run`` (``python3 report_gen.py --engagement <id>``). La LÓGICA de render es testeable
sin el SDK (``render_markdown`` sobre una lista de hallazgos); ``main`` encadena
leer-del-store -> render -> stage.

Estructura del informe (destilada de plantillas públicas de pentest, con atribución —
Theophilus/athena-pentest y Marmeus/pentesting-report-generator): engagement details,
executive summary (no técnico), tabla de severidades, metodología (OODA/PTES/OWASP),
findings con CVSS 3.1 + CWE y evidencia reproducible, remediación. CVSS/severidad solo para
hallazgos CONFIRMADOS (coherente con "no hay hallazgo sin evidencia").

Dato NO confiable: la evidencia de cada hallazgo (comando/salida) viene de tools contra un
target real. Aquí se RENDERIZA verbatim dentro de bloques de código; nunca se interpreta.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import artifact_store
import finding_store

# Nombre canónico del informe dentro del directorio del engagement (artefacto descargable).
REPORT_NAME = "pentest-report.md"

# Orden de presentación de los estados y su etiqueta humana.
_STATE_LABEL = {
    "confirmed": "Confirmado",
    "partial": "Parcial",
    "hypothesis": "Hipótesis",
    "discarded": "Descartado",
}
_STATE_ORDER = ["confirmed", "partial", "hypothesis", "discarded"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fence(text: str) -> str:
    """Envuelve texto (dato no confiable) en un bloque de código verbatim.

    Usa una valla de 4 backticks para no romperse si el contenido trae ``` dentro.
    """
    body = text if isinstance(text, str) else str(text)
    return "````\n" + body.rstrip("\n") + "\n````"


def _evidence_block(evidence: dict) -> list[str]:
    """Renderiza la evidencia de un hallazgo (comando/returncode/salida o ref a artefacto)."""
    if not isinstance(evidence, dict) or not evidence:
        return ["_Sin evidencia registrada._"]
    out: list[str] = []
    command = evidence.get("command")
    if command:
        out.append("**Comando:**")
        out.append(_fence(str(command)))
    if "returncode" in evidence:
        out.append(f"**Return code:** `{evidence.get('returncode')}`")
    output = evidence.get("output")
    if output:
        out.append("**Salida (verbatim, dato no confiable):**")
        out.append(_fence(str(output)))
    ref = evidence.get("artifact_ref")
    if isinstance(ref, dict) and ref:
        exec_id = ref.get("execution_id", "")
        name = ref.get("name", "")
        out.append(f"**Artefacto:** `{exec_id}/{name}` (descargable por el canal de datos).")
    if not out:
        # Evidencia con forma desconocida: se vuelca verbatim para no perder información.
        out.append(_fence(repr(evidence)))
    return out


def _finding_section(index: int, finding: dict) -> list[str]:
    """Renderiza un hallazgo individual como sección Markdown."""
    title = finding.get("title", "(sin título)")
    state = finding.get("state", "")
    lines = [f"### F{index}. {title}", ""]

    meta_rows = [
        ("Estado", _STATE_LABEL.get(state, state or "—")),
        ("Fase OODA", finding.get("phase") or "—"),
        ("Técnica ATT&CK", finding.get("technique_id") or "—"),
        ("Target", finding.get("target") or "—"),
        ("Confianza", finding.get("confidence") or "—"),
    ]
    # CVSS/CWE solo tienen sentido en hallazgos confirmados (disciplina "no hay hallazgo
    # sin evidencia"): se muestran si el agente los registró.
    if state == "confirmed":
        meta_rows.append(("CVSS 3.1", finding.get("cvss") or "— (pendiente de puntuar)"))
        meta_rows.append(("CWE", finding.get("cwe") or "—"))

    lines.append("| Campo | Valor |")
    lines.append("|---|---|")
    for key, value in meta_rows:
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines.append("**Evidencia:**")
    lines.append("")
    lines.extend(_evidence_block(finding.get("evidence", {})))
    lines.append("")

    remediation = finding.get("remediation")
    if remediation:
        lines.append("**Remediación:**")
        lines.append("")
        lines.append(str(remediation))
        lines.append("")

    change = finding.get("change")
    if isinstance(change, dict) and change:
        tag = change.get("tag", "")
        revert = change.get("revert", "")
        lines.append("**Cambio introducido en la cuenta/target (trazabilidad):**")
        lines.append("")
        if tag:
            lines.append(f"- Tag: `{tag}`")
        if revert:
            lines.append("- Reversión:")
            lines.append(_fence(str(revert)))
        lines.append("")

    return lines


def render_markdown(engagement_id: str, findings: list[dict], generated_at: str | None = None) -> str:
    """Renderiza el informe Markdown completo a partir de la lista de hallazgos.

    Función PURA y testeable (no toca el store ni el FS): recibe los hallazgos ya leídos y
    devuelve el texto del informe. Agrupa por estado (confirmados primero), con tabla de
    severidades por estado y las secciones de metodología/remediación.
    """
    generated_at = generated_at or _now_iso()

    # Agrupa por estado conservando el orden de inserción dentro de cada grupo.
    by_state: dict[str, list[dict]] = {s: [] for s in _STATE_ORDER}
    extra: list[dict] = []
    for f in findings:
        state = f.get("state")
        if state in by_state:
            by_state[state].append(f)
        else:
            extra.append(f)

    lines: list[str] = []
    lines.append(f"# Informe de pentest — engagement `{engagement_id}`")
    lines.append("")
    lines.append(f"- **Generado:** {generated_at}")
    lines.append(f"- **Total de hallazgos registrados:** {len(findings)}")
    lines.append("")

    # --- Resumen ejecutivo (no técnico) ---
    confirmed = by_state["confirmed"]
    lines.append("## Resumen ejecutivo")
    lines.append("")
    if confirmed:
        lines.append(
            f"Durante el engagement se confirmaron **{len(confirmed)}** hallazgo(s) con "
            "evidencia reproducible. El detalle técnico, la evidencia y la remediación de "
            "cada uno figuran más abajo. Los elementos marcados como hipótesis o parciales "
            "requieren verificación adicional y no constituyen explotación confirmada."
        )
    else:
        lines.append(
            "No se confirmó ningún hallazgo explotable con evidencia reproducible en el "
            "alcance y la ventana de este engagement. Esto NO equivale a un veredicto de "
            "\"sistema seguro\": es la ausencia de hallazgos confirmados con los métodos y el "
            "tiempo empleados. Los elementos registrados como hipótesis o parciales quedan "
            "documentados para seguimiento."
        )
    lines.append("")

    # --- Tabla de severidades (por estado) ---
    lines.append("## Resumen por estado")
    lines.append("")
    lines.append("| Estado | Nº |")
    lines.append("|---|---|")
    for state in _STATE_ORDER:
        lines.append(f"| {_STATE_LABEL[state]} | {len(by_state[state])} |")
    if extra:
        lines.append(f"| Otros | {len(extra)} |")
    lines.append("")

    # --- Metodología ---
    lines.append("## Metodología")
    lines.append("")
    lines.append(
        "El engagement se condujo con un bucle **OODA** (Observe-Orient-Decide-Act), "
        "orientando por tácticas y técnicas **MITRE ATT&CK** y apoyándose en las prácticas "
        "de **PTES** y **OWASP** según la superficie (red, cloud, web). Cada hallazgo exige "
        "evidencia reproducible del comando ejecutado; no hay hallazgo confirmado sin prueba."
    )
    lines.append("")

    # --- Hallazgos (confirmados primero) ---
    lines.append("## Hallazgos")
    lines.append("")
    counter = 1
    any_rendered = False
    for state in _STATE_ORDER:
        group = by_state[state]
        if not group:
            continue
        any_rendered = True
        lines.append(f"### {_STATE_LABEL[state]} ({len(group)})")
        lines.append("")
        for f in group:
            lines.extend(_finding_section(counter, f))
            counter += 1
    for f in extra:
        any_rendered = True
        lines.extend(_finding_section(counter, f))
        counter += 1
    if not any_rendered:
        lines.append("_No hay hallazgos registrados para este engagement._")
        lines.append("")

    # --- Nota de alcance / limitaciones ---
    lines.append("## Alcance y limitaciones")
    lines.append("")
    lines.append(
        "Este informe refleja únicamente los hallazgos registrados durante el engagement "
        "contra targets autorizados por el operador. La responsabilidad del alcance recae "
        "en el operador. La evidencia incluida es dato crudo de las herramientas y se "
        "presenta verbatim."
    )
    lines.append("")

    return "\n".join(lines)


def generate(engagement_id: str) -> dict:
    """Lee TODO el store del engagement, renderiza el informe y lo stagea como artefacto.

    Devuelve la entrada de meta del artefacto staged (``{name, size, sha256, ...}``) más el
    recuento de hallazgos. El Markdown queda descargable por el canal de datos en
    ``<engagement_id>/pentest-report.md``.
    """
    page = finding_store.read(engagement_id, cursor=0, limit=None)
    findings = page.get("findings", [])
    markdown = render_markdown(engagement_id, findings)
    entry = artifact_store.put_bytes(
        engagement_id,
        REPORT_NAME,
        markdown.encode("utf-8"),
        {"kind": "pentest-report", "engagement_id": engagement_id, "findings": len(findings)},
    )
    return {"status": "done", "artifact": entry, "findings_count": len(findings)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera el informe Markdown de pentest desde el store de hallazgos."
    )
    parser.add_argument("--engagement", required=True, help="engagement_id del store de hallazgos")
    args = parser.parse_args(argv)
    try:
        result = generate(args.engagement)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entry = result["artifact"]
    # Línea de salida legible para el bucle OODA (la ve por kali_run).
    print(
        f"informe generado: {args.engagement}/{entry['name']} "
        f"({entry['size']} bytes, {result['findings_count']} hallazgos, "
        f"sha256={entry['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
