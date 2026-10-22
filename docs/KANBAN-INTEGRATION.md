# Integración de Kanban en Vulnerator-AI

**Rama:** `feature/kanban-integration`  
**Fecha:** 2026-10-22  
**Estado:** ✅ Funcional y testeado

---

## Resumen

Esta rama integra el sistema de **Kanban Board** de Hermes Agent como capa de tracking **opcional** para engagements de pentest, sin cambiar el comportamiento por defecto del bucle OODA.

### Lo que añade

1. **Toolset `kanban`** habilitado en `config.yaml`
2. **Documentación en `SOUL.md`** de cómo usar Kanban (Opción A vs Opción B)
3. **Concurrencia de delegación aumentada** (10 → 50 workers)
4. **Guía completa de uso** en `agent-ooda/KANBAN-USAGE.md`
5. **Suite de tests** en `TESTING-KANBAN.md`

### Lo que NO cambia

- ✅ Bucle OODA sigue siendo el mismo (OBSERVE → ORIENT → DECIDE → ACT → REPORT)
- ✅ Formato de respuesta obligatorio `OODA — <FASE> (paso N): descripción`
- ✅ Por defecto usa `todo_list` (comportamiento de `master`)
- ✅ `record_finding` sigue siendo la memoria durable de evidencias
- ✅ Disciplina OODA sin cambios

---

## Cambios técnicos

### 1. `agent-ooda/profile/config.yaml`

#### a) Toolset `kanban` añadido

```yaml
toolsets:
  - kali
  - playwright
  - todo
  - kanban  # ← NUEVO
```

**Expone funciones:**
- `kanban_create(goal, board, assignee, depends_on)`
- `kanban_work(task_id)`
- `kanban_complete(task_id)`
- `kanban_list(board)`
- `kanban_block(task_id, reason)`
- `kanban_show(task_id)`
- `kanban_comment(task_id, text)`
- `kanban_attach(task_id, path)`

#### b) Concurrencia de delegación aumentada

```yaml
delegation:
  max_concurrent_children: 50  # antes: 10 (default)
```

**Validado por:** Refactor de 1,393 agentes de Nous (218 workers concurrentes estables)  
**Uso:** Scans masivos de redes grandes (/24 = 254 hosts en paralelo)

---

### 2. `agent-ooda/profile/SOUL.md`

Nueva sección: **"Gestión del estado del bucle con todo_list y Kanban"** (74 líneas)

#### Antes (master):
- 2 capas: `todo_list` + `record_finding`
- Solo mencionaba `todo_list`

#### Después (feature/kanban-integration):
- 3 capas: `todo_list` + `kanban_*` + `record_finding`
- **Tabla comparativa** de cuándo usar cada sistema:

| Sistema | Uso recomendado |
|---------|-----------------|
| `todo_list` | Engagements cortos (<1h), sin interrupciones esperadas |
| `kanban_*` | Engagements largos (>1h), crash recovery, múltiples targets |
| `record_finding` | Evidencias forenses (siempre) |

#### Workflow OODA + Kanban documentado:

1. **Inicio:** Crear board `engagement-<target-id>` con 5 tareas en cadena (OBSERVE → ORIENT → DECIDE → ACT → REPORT) con `depends_on`
2. **Al entrar en fase:** `kanban_work(task_id)`
3. **Al terminar fase:** `kanban_complete(task_id)` → desbloquea automáticamente la siguiente
4. **Si ACT falla:** Crear nueva tarea ORIENT con `depends_on=[act_task_id]` → el DAG registra la iteración
5. **Al pausar:** `kanban_block(task_id, reason="exhausted_vectors")`
6. **Al retomar:** `kanban_list(board)` → leer estado completo y continuar

**Ventajas documentadas:**
- Crash recovery (SQLite permanente en `~/.hermes/kanban.db`)
- DAG explícito de dependencias (auditoría)
- Reanudación sin interrogatorio

---

## Archivos nuevos

### 1. `agent-ooda/KANBAN-USAGE.md` (335 líneas)

Guía completa de uso con:
- 3 vías de acceso: tool calls Python, comandos `/kanban` en TUI, CLI `hermes kanban`
- Workflow paso a paso con código real
- Ejemplo de engagement CTF con iteración (ACT falla → ORIENT retry)
- Troubleshooting (database locked, dependencies no respetadas, etc.)
- Comparativa mono-profile vs multi-profile

### 2. `TESTING-KANBAN.md` (306 líneas)

Suite de 10 tests para validar la integración:
1. Rebuild exitoso
2. Config de delegación (50 workers)
3. Dashboard accesible con tema Ember
4. Board Kanban creado desde TUI
5. Workflow claim→complete
6. Visualización en Dashboard
7. Crash recovery
8. Delegación concurrente (50 workers)
9. SOUL.md cargado correctamente
10. Engagement real (opcional)

### 3. `rebuild-containers.sh` (62 líneas)

Script automatizado de rebuild desde cero:
- Para y elimina contenedores + volúmenes
- Limpia imágenes de Vulnerator
- Crea `docker-compose.tmp.yml` sin `platforms:` (fix para sistemas sin buildx)
- Rebuild sin cache
- Levanta todo
- Muestra verificaciones

---

## Comportamiento: master vs feature/kanban-integration

### Sin mencionar Kanban en el prompt (comportamiento `master`)

```
OODA — OBSERVE (paso 1): iniciando reconocimiento
[usa todo_list para tracking]
[ejecuta herramientas]
[usa record_finding para evidencias]
OODA — ORIENT (paso 2): analizando hallazgos
...
```

**Resultado:** Idéntico a `master`.

### Con Kanban explícito en el prompt

```
Prompt:
TRACKING:
- Usa Kanban para registrar el progreso
- Board: engagement-<target>
- Crea las fases del OODA en el board con dependencias
```

```
OODA — OBSERVE (paso 1): iniciando reconocimiento
[llama kanban_create 5 veces: OBSERVE, ORIENT, DECIDE, ACT, REPORT con depends_on]
[llama kanban_work(observe_id)]
[ejecuta herramientas]
[usa record_finding para evidencias]
[llama kanban_complete(observe_id)]  ← desbloquea ORIENT
OODA — ORIENT (paso 2): analizando hallazgos
[llama kanban_work(orient_id)]
...
```

**Diferencia:** Cada transición de fase queda registrada en Kanban. Las iteraciones del bucle (ACT falla → ORIENT retry) son explícitas en el DAG.

---

## Ejemplo de uso

### Prompt para engagement con Kanban

```
ENGAGEMENT CTF — Compromiso completo autorizado

TARGET: http://target.example.com
MODO: ctf
OBJETIVO: Obtener RCE

TRACKING:
- Usa Kanban para registrar el progreso del engagement
- Board: engagement-example-target
- Crea las fases del OODA en el board con dependencias
- Completa cada fase conforme avances

Empieza con OBSERVE.
```

### Monitoreo durante el engagement

**Desde Dashboard:**
```
http://localhost:9119 → Kanban → engagement-example-target
```

**Desde TUI:**
```
/kanban list --board engagement-example-target
```

**Desde CLI:**
```bash
docker exec ooda-agent hermes kanban list --board engagement-example-target
```

---

## Multi-profile (futuro, NO implementado en esta rama)

Kanban permite coordinación multi-profile, pero esta rama usa **mono-profile** (perfil `default`).

### Qué se podría hacer en el futuro

```bash
# Crear perfiles especializados
hermes profile create vulnerator-observe --clone
hermes profile create vulnerator-orient --clone
hermes profile create vulnerator-act --clone

# Arrancar gateways
hermes -p vulnerator-observe gateway start &
hermes -p vulnerator-orient gateway start &
hermes -p vulnerator-act gateway start &

# Conductor crea tareas con assignee específico
kanban_create(
    goal="OBSERVE: escanear /24 completo",
    board="engagement-internal-network",
    assignee="vulnerator-observe"  # auto-dispatch
)
```

**Dispatcher** (background) haría polling del board y asignaría tareas automáticamente a los perfiles.

**Pero esto requiere:**
- Múltiples perfiles configurados con SOUL.md especializado
- Dispatcher activo en el gateway
- Refactor del flujo OODA (orquestador vs ejecutores)

**Decisión de diseño:** Se dejó para trabajo futuro. La rama actual usa mono-profile con Kanban como **tracking**, no como **auto-dispatch**.

---

## Testing

### Test smoke (rápido)

```bash
# 1. Rebuild
./rebuild-containers.sh

# 2. Verificar config
docker exec ooda-agent hermes config show | grep -A 2 delegation
# Debe mostrar: max_concurrent_children: 50

# 3. Verificar toolsets
docker exec ooda-agent cat /opt/data/config.yaml | grep -A 5 "^toolsets:"
# Debe incluir: - kanban

# 4. Entrar en TUI
docker exec -it ooda-agent hermes tui

# 5. Probar Kanban
> /kanban boards list
# No debe dar error "command not found"
```

### Test completo

Seguir `TESTING-KANBAN.md` (10 tests secuenciales).

---

## Problemas conocidos

### 1. Multi-platform build (docker-compose.yml)

El `docker-compose.yml` declara `platforms: [linux/arm64, linux/amd64]`, pero sistemas sin Docker Buildx configurado fallan con:

```
Multi-platform build is not supported for the docker driver.
```

**Solución temporal:** `rebuild-containers.sh` crea `docker-compose.tmp.yml` sin `platforms:` y construye solo para la plataforma local.

**Solución permanente:** Configurar Buildx:
```bash
docker buildx create --name multiarch --driver docker-container --use
```

### 2. Kanban board no visible si el agente no lo usó

Si el prompt NO menciona "Usa Kanban", el agente usa `todo_list` y **no crea board**. Esto es correcto (comportamiento de `master`).

Para forzar Kanban, el prompt debe ser explícito:
```
TRACKING: Usa Kanban para registrar el progreso
```

### 3. Evidencias se pierden al hacer `docker compose down`

Findings (`record_finding`) viven en `/artifacts/<engagement-id>/` dentro del contenedor `kali`, que **NO tiene volumen persistente**.

**Solución:** Copiar evidencias antes de parar:
```bash
docker cp kali:/artifacts/<engagement-id>/findings.jsonl ./.agents/tasks/<engagement-id>/
```

**Mejora futura:** Añadir volumen en `docker-compose.yml`:
```yaml
kali:
  volumes:
    - ./artifacts:/artifacts
```

---

## Comparación con master

| Aspecto | `master` | `feature/kanban-integration` |
|---------|----------|------------------------------|
| Toolsets | `kali`, `playwright`, `todo` | `kali`, `playwright`, `todo`, `kanban` |
| Tracking default | `todo_list` | `todo_list` (sin cambios) |
| Tracking opcional | ninguno | `kanban_*` |
| Delegación concurrente | 10 (default Hermes) | 50 (explícito) |
| Crash recovery | Desde `record_finding` | Desde `kanban_list` + `record_finding` |
| DAG de workflow | No | Sí (con Kanban) |
| Complejidad | Baja | Baja (Kanban es opt-in) |

---

## Roadmap futuro

### Corto plazo (próxima rama)
- [ ] Añadir volumen persistente para `/artifacts` en `kali`
- [ ] Skill `kanban-pentest` que automatiza el setup de 5 tareas (OBSERVE→REPORT)
- [ ] Integrar Kanban board en el Dashboard skin de Vulnerator

### Medio plazo
- [ ] Multi-profile: perfiles especializados (recon, exploit, post-exploit)
- [ ] Auto-dispatch: dispatcher activo para asignar tareas automáticamente
- [ ] Kanban swarms: paralelización masiva de /16 (256 /24 en paralelo)

### Largo plazo
- [ ] Issue #344 de Hermes: agent-to-agent messaging, shared blackboard, consensus
- [ ] Orquestador meta-OODA + workers especializados

---

## Referencias

- **Hermes Kanban docs:** https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- **Hermes Profiles:** https://hermes-agent.nousresearch.com/docs/user-guide/profiles
- **Nous 1,393-agent refactor:** https://cellcog.ai/blog/nous-hermes-1393-agents-refactor/
- **Issue #344 (roadmap futuro):** https://github.com/NousResearch/hermes-agent/issues/344

---

## Conclusión

La integración de Kanban es **no invasiva** y **backward-compatible**:
- ✅ No cambia el bucle OODA
- ✅ No cambia el comportamiento por defecto
- ✅ Añade tracking durable opcional
- ✅ Aumenta concurrencia de delegación para pentests grandes
- ✅ Crash recovery sin perder estado del engagement

**Para usuarios de `master`:** Si no mencionas Kanban en el prompt, el comportamiento es idéntico.

**Para usuarios que adoptan Kanban:** Tracking durable + DAG explícito + crash recovery sin perder la disciplina OODA.

---

**Última actualización:** 2026-10-22  
**Rama:** `feature/kanban-integration`  
**Commits:** 4 commits desde `master`
