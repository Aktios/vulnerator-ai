# Kanban Integration in Vulnerator-AI

**Branch:** `feature/kanban-integration`  
**Date:** 2026-10-22  
**Status:** ✅ Functional and tested

---

## Summary

This branch integrates Hermes Agent's **Kanban Board** system as an **optional** tracking layer for pentest engagements, without changing the default OODA loop behavior.

### What it adds

1. **`kanban` toolset** enabled in `config.yaml`
2. **Documentation in `SOUL.md`** on how to use Kanban (Option A vs Option B)
3. **Increased delegation concurrency** (10 → 50 workers)
4. **Complete usage guide** in `agent-ooda/KANBAN-USAGE.md`

### What does NOT change

- ✅ OODA loop remains the same (OBSERVE → ORIENT → DECIDE → ACT → REPORT)
- ✅ Mandatory response format `OODA — <PHASE> (step N): description`
- ✅ Uses `todo_list` by default (`master` behavior)
- ✅ `record_finding` remains the durable evidence memory
- ✅ OODA discipline unchanged

---

## Technical changes

### 1. `agent-ooda/profile/config.yaml`

#### a) `kanban` toolset added

```yaml
toolsets:
  - kali
  - playwright
  - todo
  - kanban  # ← NEW
```

**Exposes functions:**
- `kanban_create(goal, board, assignee, depends_on)`
- `kanban_work(task_id)`
- `kanban_complete(task_id)`
- `kanban_list(board)`
- `kanban_block(task_id, reason)`
- `kanban_show(task_id)`
- `kanban_comment(task_id, text)`
- `kanban_attach(task_id, path)`

#### b) Increased delegation concurrency

```yaml
delegation:
  max_concurrent_children: 50  # previously: 10 (default)
```

**Validated by:** Nous 1,393-agent refactor (218 concurrent workers stable)  
**Use case:** Massive scans of large networks (/24 = 254 hosts in parallel)

---

### 2. `agent-ooda/profile/SOUL.md`

New section: **"Loop state management with todo_list and Kanban"** (74 lines)

#### Before (master):
- 2 layers: `todo_list` + `record_finding`
- Only mentioned `todo_list`

#### After (feature/kanban-integration):
- 3 layers: `todo_list` + `kanban_*` + `record_finding`
- **Comparison table** of when to use each system:

| System | Recommended use |
|--------|-----------------|
| `todo_list` | Short engagements (<1h), no expected interruptions |
| `kanban_*` | Long engagements (>1h), crash recovery, multiple targets |
| `record_finding` | Forensic evidence (always) |

#### OODA + Kanban workflow documented:

1. **Start:** Create board `engagement-<target-id>` with 5 chained tasks (OBSERVE → ORIENT → DECIDE → ACT → REPORT) with `depends_on`
2. **On phase entry:** `kanban_work(task_id)`
3. **On phase completion:** `kanban_complete(task_id)` → automatically unlocks next phase
4. **If ACT fails:** Create new ORIENT task with `depends_on=[act_task_id]` → DAG records the iteration
5. **On pause:** `kanban_block(task_id, reason="exhausted_vectors")`
6. **On resume:** `kanban_list(board)` → read complete state and continue

**Documented advantages:**
- Crash recovery (permanent SQLite in `~/.hermes/kanban.db`)
- Explicit DAG of dependencies (audit trail)
- Resumption without interrogation

---

## New files

### 1. `agent-ooda/KANBAN-USAGE.md` (335 lines)

Complete usage guide with:
- 3 access methods: Python tool calls, `/kanban` commands in TUI, CLI `hermes kanban`
- Step-by-step workflow with real code
- CTF engagement example with iteration (ACT fails → ORIENT retry)
- Troubleshooting (database locked, dependencies not respected, etc.)
- Comparison mono-profile vs multi-profile

### 2. `TESTING-KANBAN.md` (306 lines)

Test suite with 10 tests to validate integration:
1. Successful rebuild
2. Delegation config (50 workers)
3. Dashboard accessible with Ember theme
4. Kanban board created from TUI
5. Claim→complete workflow
6. Dashboard visualization
7. Crash recovery
8. Concurrent delegation (50 workers)
9. SOUL.md loaded correctly
10. Real engagement (optional)

### 3. `rebuild-containers.sh` (62 lines)

Automated rebuild script from scratch:
- Stop and remove containers + volumes
- Clean Vulnerator images
- Create `docker-compose.tmp.yml` without `platforms:` (fix for systems without buildx)
- Rebuild without cache
- Start everything
- Show verifications

---

## Behavior: master vs feature/kanban-integration

### Without mentioning Kanban in prompt (`master` behavior)

```
OODA — OBSERVE (step 1): starting reconnaissance
[uses todo_list for tracking]
[executes tools]
[uses record_finding for evidence]
OODA — ORIENT (step 2): analyzing findings
...
```

**Result:** Identical to `master`.

### With explicit Kanban in prompt

```
Prompt:
TRACKING:
- Use Kanban to track progress
- Board: engagement-<target>
- Create OODA phases in board with dependencies
```

```
OODA — OBSERVE (step 1): starting reconnaissance
[calls kanban_create 5 times: OBSERVE, ORIENT, DECIDE, ACT, REPORT with depends_on]
[calls kanban_work(observe_id)]
[executes tools]
[uses record_finding for evidence]
[calls kanban_complete(observe_id)]  ← unlocks ORIENT
OODA — ORIENT (step 2): analyzing findings
[calls kanban_work(orient_id)]
...
```

**Difference:** Each phase transition is recorded in Kanban. Loop iterations (ACT fails → ORIENT retry) are explicit in the DAG.

---

## Usage example

### Prompt for engagement with Kanban

```
CTF ENGAGEMENT — Full authorized compromise

TARGET: http://target.example.com
MODE: ctf
GOAL: Achieve RCE

TRACKING:
- Use Kanban to track engagement progress
- Board: engagement-example-target
- Create OODA phases in board with dependencies
- Complete each phase as you progress

Start with OBSERVE.
```

### Monitoring during engagement

**From Dashboard:**
```
http://localhost:9119 → Kanban → engagement-example-target
```

**From TUI:**
```
/kanban list --board engagement-example-target
```

**From CLI:**
```bash
docker exec ooda-agent hermes kanban list --board engagement-example-target
```

---

## Multi-profile (future, NOT implemented in this branch)

Kanban allows multi-profile coordination, but this branch uses **mono-profile** (`default` profile).

### What could be done in the future

```bash
# Create specialized profiles
hermes profile create vulnerator-observe --clone
hermes profile create vulnerator-orient --clone
hermes profile create vulnerator-act --clone

# Start gateways
hermes -p vulnerator-observe gateway start &
hermes -p vulnerator-orient gateway start &
hermes -p vulnerator-act gateway start &

# Conductor creates tasks with specific assignee
kanban_create(
    goal="OBSERVE: scan complete /24",
    board="engagement-internal-network",
    assignee="vulnerator-observe"  # auto-dispatch
)
```

**Dispatcher** (background) would poll the board and automatically assign tasks to profiles.

**But this requires:**
- Multiple profiles configured with specialized SOUL.md
- Active dispatcher in gateway
- OODA flow refactor (orchestrator vs executors)

**Design decision:** Left for future work. Current branch uses mono-profile with Kanban as **tracking**, not **auto-dispatch**.

---

## Testing

### Smoke test (quick)

```bash
# 1. Rebuild
./rebuild-containers.sh

# 2. Verify config
docker exec ooda-agent hermes config show | grep -A 2 delegation
# Should show: max_concurrent_children: 50

# 3. Verify toolsets
docker exec ooda-agent cat /opt/data/config.yaml | grep -A 5 "^toolsets:"
# Should include: - kanban

# 4. Enter TUI
docker exec -it ooda-agent hermes tui

# 5. Test Kanban
> /kanban boards list
# Should not error "command not found"
```

### Complete test

Follow `TESTING-KANBAN.md` (10 sequential tests).

---

## Known issues

### 1. Multi-platform build (docker-compose.yml)

The `docker-compose.yml` declares `platforms: [linux/arm64, linux/amd64]`, but systems without Docker Buildx configured fail with:

```
Multi-platform build is not supported for the docker driver.
```

**Temporary solution:** `rebuild-containers.sh` creates `docker-compose.tmp.yml` without `platforms:` and builds only for local platform.

**Permanent solution:** Configure Buildx:
```bash
docker buildx create --name multiarch --driver docker-container --use
```

### 2. Kanban board not visible if agent didn't use it

If prompt does NOT mention "Use Kanban", agent uses `todo_list` and **doesn't create board**. This is correct (`master` behavior).

To force Kanban, prompt must be explicit:
```
TRACKING: Use Kanban to track progress
```

### 3. Evidence lost on `docker compose down`

Findings (`record_finding`) live in `/artifacts/<engagement-id>/` inside `kali` container, which **has NO persistent volume**.

**Solution:** Copy evidence before stopping:
```bash
docker cp kali:/artifacts/<engagement-id>/findings.jsonl ./.agents/tasks/<engagement-id>/
```

**Future improvement:** Add volume in `docker-compose.yml`:
```yaml
kali:
  volumes:
    - ./artifacts:/artifacts
```

---

## Comparison with master

| Aspect | `master` | `feature/kanban-integration` |
|--------|----------|------------------------------|
| Toolsets | `kali`, `playwright`, `todo` | `kali`, `playwright`, `todo`, `kanban` |
| Default tracking | `todo_list` | `todo_list` (unchanged) |
| Optional tracking | none | `kanban_*` |
| Concurrent delegation | 10 (Hermes default) | 50 (explicit) |
| Crash recovery | From `record_finding` | From `kanban_list` + `record_finding` |
| Workflow DAG | No | Yes (with Kanban) |
| Complexity | Low | Low (Kanban is opt-in) |

---

## Future roadmap

### Short term (next branch)
- [ ] Add persistent volume for `/artifacts` in `kali`
- [ ] `kanban-pentest` skill that automates setup of 5 tasks (OBSERVE→REPORT)
- [ ] Integrate Kanban board in Vulnerator Dashboard skin

### Medium term
- [ ] Multi-profile: specialized profiles (recon, exploit, post-exploit)
- [ ] Auto-dispatch: active dispatcher to assign tasks automatically
- [ ] Kanban swarms: massive parallelization of /16 (256 /24 in parallel)

### Long term
- [ ] Hermes Issue #344: agent-to-agent messaging, shared blackboard, consensus
- [ ] Meta-OODA orchestrator + specialized workers

---

## References

- **Hermes Kanban docs:** https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- **Hermes Profiles:** https://hermes-agent.nousresearch.com/docs/user-guide/profiles
- **Nous 1,393-agent refactor:** https://cellcog.ai/blog/nous-hermes-1393-agents-refactor/
- **Issue #344 (future roadmap):** https://github.com/NousResearch/hermes-agent/issues/344

---

## Conclusion

Kanban integration is **non-invasive** and **backward-compatible**:
- ✅ Doesn't change OODA loop
- ✅ Doesn't change default behavior
- ✅ Adds optional durable tracking
- ✅ Increases delegation concurrency for large pentests
- ✅ Crash recovery without losing engagement state

**For `master` users:** If you don't mention Kanban in prompt, behavior is identical.

**For users adopting Kanban:** Durable tracking + explicit DAG + crash recovery without losing OODA discipline.

---

**Last update:** 2026-10-22  
**Branch:** `feature/kanban-integration`  
**Commits:** 6 commits from `master`
