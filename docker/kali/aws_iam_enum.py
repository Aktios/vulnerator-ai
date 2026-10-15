"""Script de enumeración IAM read-only para el runner AWS.

BINARIO determinista que corre en el venv del servidor Kali (boto3 llega vía Prowler). NO
decide nada: enumera IAM de forma ACOTADA y SOLO-LECTURA y escribe UN único documento JSON
en la ruta de ``--out``. El runner STDLIB (``aws_exec.py``) lo invoca como subproceso con
``tool_streaming`` (kill de grupo/timeout, logs al ring).

Disciplina de seguridad:
  - READ-ONLY: SOLO ``list_*``/``get_*``. NUNCA create/put/attach/delete: no cambia el
    estado de la cuenta AWS. Las credenciales read-only vienen del entorno del contenedor
    (igual que Prowler/ScoutSuite).
  - ACOTADO: enumera roles/usuarios/grupos/políticas (ámbito ``Local`` = gestionadas por
    el cliente) con nombres+ARNs y relaciones de adjunción. NO vuelca
    ``get-account-authorization-details`` completo (eso es trabajo de Cloudsplaining):
    aquí se busca el mapa IAM para el bucle OODA, no el dump exhaustivo de documentos.

THIN a propósito (KISS): sin lógica que pida test unitario; la mecánica testeable
(construcción del comando, ejecución, lectura de salida, diagnóstico) vive en
``aws_exec.py`` (STDLIB puro). Este fichero solo se valida con ``ast.parse`` (no se
importa en los tests: boto3 no está en el venv de dev).
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3  # provisto por el venv del servidor (Prowler lo arrastra)


def _paginate(client, method_name: str, result_key: str, **kwargs) -> list:
    """Recorre todas las páginas de un ``list_*`` y acumula ``result_key`` (read-only)."""
    paginator = client.get_paginator(method_name)
    items: list = []
    for page in paginator.paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return items


def _simplify(entity: dict, keys: tuple[str, ...]) -> dict:
    """Proyecta solo los campos solicitados de una entidad IAM (acota el volumen)."""
    return {k: entity.get(k) for k in keys if k in entity}


def enumerate_iam(client) -> dict:
    """Enumera IAM ACOTADO y read-only: roles/usuarios/grupos/políticas + adjunciones.

    SOLO ``list_*``/``get_*``. Para cada principal se listan las políticas gestionadas
    adjuntas (ARNs) y los nombres de políticas inline; para los usuarios, además su
    pertenencia a grupos. Las políticas se limitan al ámbito ``Local`` (gestionadas por
    el cliente) para no arrastrar el catálogo entero de políticas AWS-managed.
    """
    roles = _paginate(client, "list_roles", "Roles")
    users = _paginate(client, "list_users", "Users")
    groups = _paginate(client, "list_groups", "Groups")
    # Scope ``Local`` = políticas gestionadas por el cliente; evita arrastrar el catálogo
    # entero de políticas AWS-managed (acotación).
    policies = _paginate(client, "list_policies", "Policies", Scope="Local")

    roles_out = []
    for role in roles:
        name = role["RoleName"]
        attached = client.list_attached_role_policies(RoleName=name).get(
            "AttachedPolicies", []
        )
        inline = client.list_role_policies(RoleName=name).get("PolicyNames", [])
        roles_out.append(
            {
                **_simplify(role, ("RoleName", "Arn", "RoleId", "Path", "CreateDate")),
                "attached_policies": [p.get("PolicyArn") for p in attached],
                "inline_policies": inline,
            }
        )

    users_out = []
    for user in users:
        name = user["UserName"]
        attached = client.list_attached_user_policies(UserName=name).get(
            "AttachedPolicies", []
        )
        inline = client.list_user_policies(UserName=name).get("PolicyNames", [])
        user_groups = client.list_groups_for_user(UserName=name).get("Groups", [])
        users_out.append(
            {
                **_simplify(user, ("UserName", "Arn", "UserId", "Path", "CreateDate")),
                "attached_policies": [p.get("PolicyArn") for p in attached],
                "inline_policies": inline,
                "groups": [g.get("GroupName") for g in user_groups],
            }
        )

    groups_out = []
    for group in groups:
        name = group["GroupName"]
        attached = client.list_attached_group_policies(GroupName=name).get(
            "AttachedPolicies", []
        )
        inline = client.list_group_policies(GroupName=name).get("PolicyNames", [])
        groups_out.append(
            {
                **_simplify(group, ("GroupName", "Arn", "GroupId", "Path", "CreateDate")),
                "attached_policies": [p.get("PolicyArn") for p in attached],
                "inline_policies": inline,
            }
        )

    policies_out = [
        _simplify(
            p,
            ("PolicyName", "Arn", "PolicyId", "Path", "AttachmentCount", "IsAttachable"),
        )
        for p in policies
    ]

    return {
        "roles": roles_out,
        "users": users_out,
        "groups": groups_out,
        "policies": policies_out,
        "counts": {
            "roles": len(roles_out),
            "users": len(users_out),
            "groups": len(groups_out),
            "policies": len(policies_out),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enum IAM read-only acotado (JSON a --out)")
    parser.add_argument("--account", required=True, help="ID de la cuenta AWS autorizada")
    parser.add_argument("--out", required=True, help="Fichero donde escribir el JSON único")
    args = parser.parse_args(argv)

    session = boto3.session.Session()
    iam = session.client("iam")

    result = enumerate_iam(iam)
    document = {"account": args.account, "iam": result}

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(document, fh, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
