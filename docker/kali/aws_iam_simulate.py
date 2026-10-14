"""Script THIN de simulación de permisos efectivos IAM (read-only) para el foso (inc4b).

Rol (architecture §1/§5 — plano de DATOS): BINARIO determinista que corre en el venv del
servidor Kali (boto3 llega vía Prowler). NO decide nada: dado UN principal IAM concreto,
comprueba con ``iam:SimulatePrincipalPolicy`` (API AWS **READ-ONLY**: NO modifica estado)
si ese principal PUEDE ejecutar un conjunto FIJO de acciones de privesc clásicas, y escribe
UN único documento JSON en la ruta de ``--out``. El runner STDLIB (``aws_simulate.py``) lo
invoca como subproceso con ``tool_streaming`` (kill de grupo/timeout, logs al ring) y stagea
su salida como artefacto; el crudo viaja por el plano de DATOS, nunca inline por el CONTROL.

Es el ANÁLOGO per-candidato de ``aws_iam_enum.py`` (que es recon account-wide): aquí se
confirma la EXPLOTABILIDAD efectiva de un principal, más fuerte que la mera enumeración. El
salto a ``EXPLOITED``/E4 NO lo decide este script ni el bucle: lo decide el confirmador de
dominio AWS en el core (inc4c), que leerá estas decisiones.

Disciplina de seguridad (R3/R4, scope-notes):
  - READ-ONLY: ``SimulatePrincipalPolicy`` SIMULA la evaluación de políticas; NO ejecuta las
    acciones ni cambia el estado de la cuenta. Las credenciales read-only vienen del entorno
    del contenedor (igual que Prowler/ScoutSuite); el scope ya lo validó el core ANTES de invocar.
  - FAIL-CLOSED: si el propio ``SimulatePrincipalPolicy`` devuelve ``AccessDenied`` (o cualquier
    otro error del cliente), NO se revienta con un traceback: se escribe un JSON con un ``error``
    claro y ``simulation`` vacío, y el runner lo surfacea como fallo (dato NO confiable, F0.8).

THIN a propósito (N13 KISS): sin lógica que pida test unitario; la mecánica testeable
(construcción del comando, ejecución, lectura de salida, diagnóstico) vive en
``aws_simulate.py`` (STDLIB puro). Este fichero solo se valida con ``ast.parse`` (no se
importa en los tests: boto3 no está en el venv de dev).
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3  # provisto por el venv del servidor (Prowler lo arrastra)

# Conjunto FIJO de acciones de PRIVESC clásicas de IAM que se simulan por principal. Son las
# primitivas con las que un principal con permisos excesivos escala a admin (asumir roles,
# pasar roles a servicios, crear credenciales, adjuntar/escribir políticas, versionar una
# política gestionada, reescribir la trust policy de un rol). Es un conjunto CERRADO y
# documentado: la técnica declara expectativas acotadas, no un barrido arbitrario.
PRIVESC_ACTIONS: tuple[str, ...] = (
    "sts:AssumeRole",
    "iam:PassRole",
    "iam:CreateAccessKey",
    "iam:AttachUserPolicy",
    "iam:AttachRolePolicy",
    "iam:PutUserPolicy",
    "iam:PutRolePolicy",
    "iam:CreatePolicyVersion",
    "iam:UpdateAssumeRolePolicy",
)


def simulate_principal(client, principal_arn: str, actions: tuple[str, ...]) -> dict:
    """Simula ``actions`` sobre ``principal_arn`` y devuelve ``{accion: EvalDecision}``.

    SOLO ``iam:SimulatePrincipalPolicy`` (read-only). ``EvalDecision`` ∈ {``allowed``,
    ``explicitDeny``, ``implicitDeny``}. Se pagina por si la respuesta llega troceada.
    """
    decisions: dict = {}
    paginator = client.get_paginator("simulate_principal_policy")
    for page in paginator.paginate(
        PolicySourceArn=principal_arn, ActionNames=list(actions)
    ):
        for result in page.get("EvaluationResults", []):
            decisions[result.get("EvalActionName")] = result.get("EvalDecision")
    return decisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Simula permisos efectivos de privesc de un principal IAM (read-only, JSON a --out)"
    )
    parser.add_argument("--account", required=True, help="ID de la cuenta AWS autorizada")
    parser.add_argument(
        "--principal-arn",
        required=True,
        dest="principal_arn",
        help="ARN del principal IAM a simular (PolicySourceArn)",
    )
    parser.add_argument("--out", required=True, help="Fichero donde escribir el JSON único")
    args = parser.parse_args(argv)

    session = boto3.session.Session()
    iam = session.client("iam")

    document: dict = {
        "account": args.account,
        "principal_arn": args.principal_arn,
        "actions": list(PRIVESC_ACTIONS),
    }
    try:
        document["simulation"] = simulate_principal(iam, args.principal_arn, PRIVESC_ACTIONS)
    except Exception as exc:  # noqa: BLE001 - AccessDenied u otro: fail-closed, NO revienta
        # El propio Simulate puede estar denegado para la credencial read-only. Se deja
        # constancia clara (al ring por stderr y en el JSON) sin tumbar el proceso; el runner
        # lo surfacea como 'failed' leyendo este 'error'.
        document["error"] = f"SimulatePrincipalPolicy no disponible: {exc}"
        document["simulation"] = {}
        print(document["error"], file=sys.stderr, flush=True)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(document, fh, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
