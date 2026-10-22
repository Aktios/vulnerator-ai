# Testing Checklist — Kanban Integration

## Pre-requisitos

- [ ] Estás en la rama `feature/kanban-integration`
- [ ] Tienes el `.env` configurado con tus API keys (OPENROUTER_API_KEY, etc.)
- [ ] Docker Desktop está corriendo

## Test 1: Rebuild desde cero

```bash
# En el directorio vulnerator-ai/
./rebuild-containers.sh
```

**Resultado esperado:**
- ✅ 4 contenedores arrancados: `ooda-agent`, `kali`, `playwright-mcp`, `vulnerator-mcp`
- ✅ Dashboard accesible en http://localhost:9119
- ✅ Sin errores en logs

**Verificación:**
```bash
docker-compose ps
# Todos deben estar "Up"

docker logs ooda-agent | tail -20
# Debe mostrar "Gateway started" sin errores
```

---

## Test 2: Verificar configuración de delegación

```bash
docker exec ooda-agent hermes config show delegation
```

**Resultado esperado:**
```yaml
max_concurrent_children: 50
```

**Si NO aparece:**
```bash
# Forzar el valor manualmente
docker exec ooda-agent hermes config set delegation.max_concurrent_children 50
```

---

## Test 3: Acceso al Dashboard + tema Ember

1. Abrir http://localhost:9119
2. Login: `admin` / `vulnerator`

**Resultado esperado:**
- ✅ Login exitoso
- ✅ Tema rojo/bronce (ember) aplicado
- ✅ Banner "Vulnerator" visible
- ✅ Sección "Kanban" en el menú lateral

---

## Test 4: Crear board Kanban desde TUI

```bash
docker exec -it ooda-agent hermes tui
```

Una vez dentro del TUI, enviar este prompt al agente:

```
Crea un board Kanban de prueba llamado "test-engagement" con 5 tareas en cadena:
1. OBSERVE: fingerprint target
2. ORIENT: identify CVEs (depends_on OBSERVE)
3. DECIDE: select exploit vector (depends_on ORIENT)
4. ACT: execute exploit (depends_on DECIDE)
5. REPORT: document findings (depends_on ACT)

Usa kanban_create con assignee="default" para cada una.
```

**Resultado esperado:**
- ✅ Agente llama `kanban_create` 5 veces
- ✅ Devuelve 5 task_ids
- ✅ Sin errores "toolset not found" o "function not available"

**Verificación desde TUI:**
```
/kanban list --board test-engagement
```

**Salida esperada:**
```
Board: test-engagement
1. [pending] OBSERVE: fingerprint target
2. [blocked] ORIENT: identify CVEs (waiting: task 1)
3. [blocked] DECIDE: select exploit vector (waiting: task 2)
4. [blocked] ACT: execute exploit (waiting: task 3)
5. [blocked] REPORT: document findings (waiting: task 4)
```

---

## Test 5: Workflow básico (claim → complete)

Desde el TUI, enviar:

```
Empieza a trabajar la tarea OBSERVE del board test-engagement
```

**Resultado esperado:**
- ✅ Agente llama `kanban_work(task_id="<observe-id>")`
- ✅ Tarea cambia a `in_progress`

Luego:
```
Completa la tarea OBSERVE
```

**Resultado esperado:**
- ✅ Agente llama `kanban_complete(task_id="<observe-id>")`
- ✅ Tarea OBSERVE → `completed`
- ✅ Tarea ORIENT → `pending` (se desbloquea automáticamente)

**Verificación:**
```
/kanban list --board test-engagement
```

---

## Test 6: Visualización en Dashboard

1. Ir a http://localhost:9119
2. Clic en "Kanban" (menú lateral)
3. Seleccionar board `test-engagement`

**Resultado esperado:**
- ✅ 5 tareas visibles
- ✅ Tarea 1 (OBSERVE) marcada como `completed`
- ✅ Tarea 2 (ORIENT) marcada como `pending`
- ✅ Tareas 3-5 aún `blocked`
- ✅ Flechas de dependencia visibles (DAG)

---

## Test 7: Crash recovery

```bash
# 1. Matar contenedor brutalmente
docker kill ooda-agent

# 2. Rearrancar
docker-compose up -d ooda-agent

# 3. Esperar 5 segundos
sleep 5

# 4. Entrar en TUI
docker exec -it ooda-agent hermes tui

# 5. Listar board
/kanban list --board test-engagement
```

**Resultado esperado:**
- ✅ El estado del board se mantiene (OBSERVE=completed, ORIENT=pending)
- ✅ El SQLite persistió correctamente
- ✅ Puedes continuar el workflow desde donde quedó

---

## Test 8: Delegación concurrente (smoke test)

Desde el TUI:

```
Delega 10 tareas concurrentes de prueba. Cada una debe ejecutar:
kali_run echo "test-worker-$i" && sleep 2

Usa delegate_task para cada una y espera a que las 10 terminen.
```

**Resultado esperado:**
- ✅ 10 subagentes arrancan en paralelo (ver logs: `docker logs ooda-agent`)
- ✅ Todos devuelven su echo sin timeouts
- ✅ Conductor recibe los 10 resúmenes
- ✅ Sin errores de límite de concurrencia

**Si falla con "max concurrent exceeded":**
```bash
docker exec ooda-agent hermes config set delegation.max_concurrent_children 50
# Reintentar el test
```

---

## Test 9: SOUL.md cargado correctamente

Desde el TUI, enviar:

```
¿Qué formato de respuesta debo usar según tu SOUL.md?
```

**Resultado esperado:**
- ✅ Agente responde con:
  ```
  OODA — <FASE> (paso <N>): <descripcion>
  ```
- ✅ Menciona que debe usar OBSERVE / ORIENT / DECIDE / ACT
- ✅ Menciona que puede usar `todo_list` o `kanban_*` para tracking

---

## Test 10: Engagement real corto (opcional)

Si tienes un target de prueba (CTF local, VM de lab):

```
Inicia un engagement de pentest contra <target>.
Modo: pentest (NO explotar, solo demostrar).
Usa Kanban para trackear el progreso.
Board: engagement-<target-name>
```

**Observar:**
- ✅ Agente crea board con 5 fases (OBSERVE→ORIENT→DECIDE→ACT→REPORT)
- ✅ Va completando tareas conforme avanza
- ✅ Si ACT falla, crea nueva tarea ORIENT-retry con `depends_on` la ACT
- ✅ El DAG en Dashboard muestra el flujo completo

---

## Troubleshooting

### "kanban_create not found" o similar

```bash
# Verificar que toolsets incluyen 'todo' (que trae kanban)
docker exec ooda-agent hermes config show toolsets

# Debe incluir: [kali, playwright, todo]
```

### Dashboard no muestra sección Kanban

- Verificar versión de Hermes Agent en el contenedor:
  ```bash
  docker exec ooda-agent hermes --version
  ```
- Kanban requiere Hermes >= 0.3.x (branch feat/kanban-standing)

### Database locked

```bash
# Verificar que no hay múltiples procesos accediendo a kanban.db
docker exec ooda-agent ps aux | grep hermes

# Si hay múltiples, reiniciar contenedor
docker-compose restart ooda-agent
```

---

## Rollback (si algo falla)

```bash
# Volver a master
git checkout master

# Reconstruir contenedores de la versión estable
./rebuild-containers.sh
```

---

## ✅ Checklist final

Una vez completados todos los tests:

- [ ] Test 1: Rebuild exitoso
- [ ] Test 2: `delegation.max_concurrent_children: 50` configurado
- [ ] Test 3: Dashboard accesible con tema Ember
- [ ] Test 4: Board Kanban creado desde TUI
- [ ] Test 5: Workflow claim→complete funciona
- [ ] Test 6: Visualización en Dashboard OK
- [ ] Test 7: Crash recovery funciona
- [ ] Test 8: Delegación concurrente (10 workers) sin errores
- [ ] Test 9: SOUL.md cargado correctamente
- [ ] Test 10: Engagement real (opcional)

**Si TODOS los tests pasan:**
```bash
# Documentar en el commit
git checkout feature/kanban-integration
echo "✅ All tests passed on $(date)" >> TESTING-KANBAN.md
git add TESTING-KANBAN.md
git commit -m "test: validate Kanban integration - all tests passed"
```

---

**Última actualización:** 2026-10-22
