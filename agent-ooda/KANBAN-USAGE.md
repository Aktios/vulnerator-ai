# Kanban Usage Guide — Vulnerator-AI

## Resumen ejecutivo

Kanban en Hermes Agent es un sistema de tracking durable basado en SQLite (~/.hermes/kanban.db) que permite:

- **Crash recovery:** el estado sobrevive reinicios del contenedor.
- **DAG de dependencias:** tareas bloqueadas hasta que sus parents estén completed.
- **Reanudación sin pérdida de contexto:** `kanban_list` recarga el estado completo del engagement.

**Decisión tomada:** Opción A (OODA control total + Kanban como tracking). El conductor OODA sigue ejecutando su bucle OBSERVE→ORIENT→DECIDE→ACT, y opcionalmente registra el progreso en Kanban para auditoría/reanudación.

---

## Cuándo usar Kanban vs todo_list

| Criterio | Usa `todo_list` | Usa `kanban_*` |
|----------|----------------|----------------|
| Duración del engagement | <1h (CTF rápido) | >1h (pentest largo, múltiples sessions) |
| Riesgo de interrupción | Bajo (session continua) | Alto (crashes, pausas operador) |
| Paralelización de targets | No (1 target) | Sí (múltiples targets /24, Cloud) |
| Auditoría post-engagement | No crítica | Crítica (DAG completo del workflow) |

**Ambos NO son excluyentes:** puedes usar Kanban para el DAG del engagement (OBSERVE→ORIENT→...) y `todo_list` para subtareas efímeras dentro de cada fase.

---

## Tres vías de acceso a Kanban

### 1. Tool calls directos (recomendado para OODA)

El conductor OODA llama las funciones Python directamente:

```python
# Crear board al inicio del engagement
kanban_create(
    goal="OBSERVE: recon inicial — nmap, fingerprinting, enumeración básica",
    board="engagement-10.196.1.1",
    assignee="default"
)
# Salida: {"task_id": "1a2b3c4d", "status": "pending"}

# Listar tareas del board
kanban_list(board="engagement-10.196.1.1")
# Salida: [{"task_id": "1a2b3c4d", "goal": "OBSERVE: ...", "status": "pending", ...}]

# Empezar a trabajar la tarea (marca como claimed+in_progress)
kanban_work(task_id="1a2b3c4d")

# Completar tarea
kanban_complete(task_id="1a2b3c4d")
```

### 2. Comandos `/kanban` en TUI/chat

Desde el TUI de Hermes o el chat web (Dashboard :9119), el operador puede ejecutar:

```bash
# Crear tarea
/kanban create --goal "OBSERVE: recon inicial" --board engagement-10.196.1.1

# Listar tareas
/kanban list --board engagement-10.196.1.1

# Ver detalles de una tarea
/kanban view --task-id 1a2b3c4d

# Completar tarea (si eres el assignee)
/kanban complete --task-id 1a2b3c4d
```

### 3. CLI `hermes kanban` (fuera del contenedor)

Desde el host, con acceso al volumen de Hermes:

```bash
# Listar todos los boards
docker exec ooda-agent hermes kanban list

# Ver tareas de un board
docker exec ooda-agent hermes kanban list --board engagement-10.196.1.1

# Crear tarea (requiere --profile si no estás en default)
docker exec ooda-agent hermes -p default kanban create \
  --goal "OBSERVE: recon inicial" \
  --board engagement-10.196.1.1
```

---

## Workflow OODA + Kanban (Opción A)

**Objetivo:** el OODA mantiene su bucle autónomo, Kanban registra el progreso para auditoría/reanudación.

### Inicio del engagement

```python
# 1. Crear board con nombre descriptivo
board_name = f"engagement-{target_ip_or_id}"

# 2. Crear cadena de fases con dependencias
observe_id = kanban_create(
    goal="OBSERVE: recon inicial — nmap, fingerprinting, enumeración",
    board=board_name,
    assignee="default"
)["task_id"]

orient_id = kanban_create(
    goal="ORIENT: correlacionar hallazgos, identificar CVEs, priorizar vectores",
    board=board_name,
    assignee="default",
    depends_on=[observe_id]
)["task_id"]

decide_id = kanban_create(
    goal="DECIDE: seleccionar vector de explotación con justificación",
    board=board_name,
    assignee="default",
    depends_on=[orient_id]
)["task_id"]

act_id = kanban_create(
    goal="ACT: ejecutar exploit, verificar escalada, capturar flag",
    board=board_name,
    assignee="default",
    depends_on=[decide_id]
)["task_id"]

report_id = kanban_create(
    goal="REPORT: registrar evidencias finales, entregar informe",
    board=board_name,
    assignee="default",
    depends_on=[act_id]
)["task_id"]
```

### Durante el bucle OODA

```python
# Al entrar en una fase (OBSERVE)
kanban_work(task_id=observe_id)
# → status cambia a "in_progress"

# ... ejecutas tus steps de OBSERVE (nmap, curl, whatweb, etc.)

# Al completar fase con evidencia
kanban_complete(task_id=observe_id)
# → status cambia a "completed", desbloquea ORIENT
```

### Si el bucle itera (ACT falla → volver a ORIENT)

```python
# 1. Completar ACT como "failed" (o simplemente no completar)
kanban_block(task_id=act_id, reason="exploit_failed_need_alternative_vector")

# 2. Crear NUEVA tarea ORIENT que depende de la ACT fallida
orient_retry_id = kanban_create(
    goal="ORIENT (retry): exploit X falló, buscar alternativa (sudo, capabilities, cron)",
    board=board_name,
    assignee="default",
    depends_on=[act_id]  # depende de la ACT fallida para registrar la iteración
)["task_id"]

# 3. Empezar esa nueva ORIENT
kanban_work(task_id=orient_retry_id)
```

**Resultado:** el DAG del board muestra el flujo completo: OBSERVE → ORIENT → DECIDE → ACT (blocked) → ORIENT-retry → DECIDE-retry → ACT-success → REPORT.

### Al pausar (3 pasos sin progreso)

```python
# Bloquear la tarea actual con motivo
kanban_block(
    task_id=current_task_id,
    reason="exhausted_vectors_need_operator_guidance"
)
```

### Al retomar (después de crash/interrupción)

```python
# 1. Listar tareas del board
tasks = kanban_list(board=board_name)

# 2. Buscar la última tarea in_progress o blocked
current_task = next(t for t in tasks if t["status"] in ["in_progress", "blocked"])

# 3. Leer el contexto del store de findings
findings = mcp_kali_read_findings()

# 4. Continuar desde ahí (kanban_work si estaba blocked)
if current_task["status"] == "blocked":
    kanban_work(task_id=current_task["task_id"])
```

---

## Ejemplo completo: engagement de CTF

```python
# === INICIO (paso 1 del OODA) ===
OODA — OBSERVE (paso 1): configurando board Kanban para engagement-pinguinazo-ssti

board = "engagement-pinguinazo-ssti"

observe_id = kanban_create(
    goal="OBSERVE: fingerprint target, identificar tecnología web, buscar inputs",
    board=board,
    assignee="default"
)["task_id"]

orient_id = kanban_create(
    goal="ORIENT: correlacionar hallazgos, buscar CVEs de SSTI en framework detectado",
    board=board,
    assignee="default",
    depends_on=[observe_id]
)["task_id"]

# ... (DECIDE, ACT, REPORT igual)

kanban_work(task_id=observe_id)

# Ahora ejecuto nmap, curl, whatweb...
mcp_kali_kali_run(command="nmap -sV -p 80,443 pinguinazo.local")
# ... (resto de reconocimiento)

# === FIN DE OBSERVE (paso 5) ===
OODA — OBSERVE (paso 5): reconocimiento completado — Flask 2.0.1 detectado

kanban_complete(task_id=observe_id)
# → Desbloquea automáticamente ORIENT

# === INICIO DE ORIENT (paso 6) ===
OODA — ORIENT (paso 6): analizando vulnerabilidades de Flask 2.0.1

kanban_work(task_id=orient_id)
# ... (análisis de CVEs, searchsploit, etc.)
```

---

## Visualización del board

### Desde TUI/chat

```bash
# Listar todos los boards
/kanban list

# Ver tareas de un board
/kanban list --board engagement-10.196.1.1
```

### Desde Dashboard web (:9119)

1. Login en http://localhost:9119 (admin/vulnerator)
2. Navegar a la sección **Kanban** (menú lateral)
3. Seleccionar board `engagement-<target>`
4. Ver DAG completo con estados y dependencias

---

## Multi-profile (futuro, NO implementado aún)

**Nota:** la Opción A (actual) usa un solo perfil (`default`). Kanban ya funciona en mono-profile como tracking durable. Si en el futuro quieres auto-dispatch (múltiples perfiles ejecutando tareas en paralelo):

```bash
# Crear perfiles especializados
hermes profile create vulnerator-recon --clone --description "Recon specialist"
hermes profile create vulnerator-exploit --clone --description "Exploitation worker"

# Arrancar gateways
hermes -p vulnerator-recon gateway start &
hermes -p vulnerator-exploit gateway start &

# Crear tareas con assignee específico
kanban_create(
    goal="OBSERVE: escanear /24 completo",
    board="engagement-192.168.1.0-24",
    assignee="vulnerator-recon"  # auto-dispatch a ese perfil
)
```

En mono-profile (actual), `assignee="default"` siempre y las tareas las ejecutas tú manualmente con `kanban_work`.

---

## Comandos útiles

```python
# Ver todas las tareas pending (sin assignee claim)
kanban_list(board="engagement-X", status="pending")

# Ver todas las tareas blocked (esperando decisión/input)
kanban_list(board="engagement-X", status="blocked")

# Re-intentar tarea blocked
kanban_work(task_id="1a2b3c4d")  # cambia blocked → in_progress
```

---

## Troubleshooting

### "Task has unmet dependencies"
```python
# Intentaste kanban_work en una tarea cuyo parent no está completed
# Solución: completar el parent primero, o cambiar depends_on
```

### "Task already claimed by another assignee"
```python
# En multi-profile: otro perfil ya la reclamó
# En mono-profile: no debería pasar (assignee="default" siempre)
```

### "Database locked"
```python
# Raro: dos procesos Hermes accediendo a ~/.hermes/kanban.db simultáneamente
# Solución: espera unos segundos y reintenta
```

---

## Referencias

- **Spec oficial:** `docs/hermes-kanban-v1-spec.pdf` (GitHub NousResearch/hermes-agent feat/kanban-standing)
- **Profiles:** https://hermes-agent.nousresearch.com/docs/user-guide/profiles
- **Issue tracking:** https://github.com/NousResearch/hermes-agent/issues/344 (roadmap futuro, NO actual)

---

**Última actualización:** 2026-10-22 — Vulnerator-AI commit ???
