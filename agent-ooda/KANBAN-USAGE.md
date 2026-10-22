# Kanban Usage Guide — Vulnerator-AI

## Executive Summary

Kanban in Hermes Agent is a durable tracking system based on SQLite (~/.hermes/kanban.db) that enables:

- **Crash recovery:** state survives container restarts.
- **Dependency DAG:** tasks blocked until their parents are completed.
- **Resumption without context loss:** `kanban_list` reloads complete engagement state.

**Decision made:** Option A (OODA full control + Kanban as tracking). The OODA conductor continues executing its OBSERVE→ORIENT→DECIDE→ACT loop, and optionally records progress in Kanban for audit/resumption.

---

## When to use Kanban vs todo_list

| Criteria | Use `todo_list` | Use `kanban_*` |
|----------|-----------------|----------------|
| Engagement duration | <1h (quick CTF) | >1h (long pentest, multiple sessions) |
| Interruption risk | Low (continuous session) | High (crashes, operator pauses) |
| Target parallelization | No (1 target) | Yes (multiple targets /24, Cloud) |
| Post-engagement audit | Not critical | Critical (complete workflow DAG) |

**Both are NOT mutually exclusive:** you can use Kanban for engagement DAG (OBSERVE→ORIENT→...) and `todo_list` for ephemeral subtasks within each phase.

---

## Three ways to access Kanban

### 1. Direct tool calls (recommended for OODA)

The OODA conductor calls Python functions directly:

```python
# Create board at engagement start
kanban_create(
    goal="OBSERVE: initial recon — nmap, fingerprinting, basic enumeration",
    board="engagement-10.196.1.1",
    assignee="default"
)
# Output: {"task_id": "1a2b3c4d", "status": "pending"}

# List board tasks
kanban_list(board="engagement-10.196.1.1")
# Output: [{"task_id": "1a2b3c4d", "goal": "OBSERVE: ...", "status": "pending", ...}]

# Start working on task (marks as claimed+in_progress)
kanban_work(task_id="1a2b3c4d")

# Complete task
kanban_complete(task_id="1a2b3c4d")
```

### 2. `/kanban` commands in TUI/chat

From Hermes TUI or web chat (Dashboard :9119), operator can execute:

```bash
# Create task
/kanban create --goal "OBSERVE: initial recon" --board engagement-10.196.1.1

# List tasks
/kanban list --board engagement-10.196.1.1

# View task details
/kanban view --task-id 1a2b3c4d

# Complete task (if you're the assignee)
/kanban complete --task-id 1a2b3c4d
```

### 3. CLI `hermes kanban` (outside container)

From host, with access to Hermes volume:

```bash
# List all boards
docker exec ooda-agent hermes kanban list

# View board tasks
docker exec ooda-agent hermes kanban list --board engagement-10.196.1.1

# Create task (requires --profile if not in default)
docker exec ooda-agent hermes -p default kanban create \
  --goal "OBSERVE: initial recon" \
  --board engagement-10.196.1.1
```

---

## OODA + Kanban workflow (Option A)

**Goal:** OODA maintains its autonomous loop, Kanban records progress for audit/resumption.

### Engagement start

```python
# 1. Create board with descriptive name
board_name = f"engagement-{target_ip_or_id}"

# 2. Create phase chain with dependencies
observe_id = kanban_create(
    goal="OBSERVE: initial recon — nmap, fingerprinting, enumeration",
    board=board_name,
    assignee="default"
)["task_id"]

orient_id = kanban_create(
    goal="ORIENT: correlate findings, identify CVEs, prioritize vectors",
    board=board_name,
    assignee="default",
    depends_on=[observe_id]
)["task_id"]

decide_id = kanban_create(
    goal="DECIDE: select exploitation vector with justification",
    board=board_name,
    assignee="default",
    depends_on=[orient_id]
)["task_id"]

act_id = kanban_create(
    goal="ACT: execute exploit, verify escalation, capture flag",
    board=board_name,
    assignee="default",
    depends_on=[decide_id]
)["task_id"]

report_id = kanban_create(
    goal="REPORT: record final evidence, deliver report",
    board=board_name,
    assignee="default",
    depends_on=[act_id]
)["task_id"]
```

### During OODA loop

```python
# On phase entry (OBSERVE)
kanban_work(task_id=observe_id)
# → status changes to "in_progress"

# ... execute your OBSERVE steps (nmap, curl, whatweb, etc.)

# On phase completion with evidence
kanban_complete(task_id=observe_id)
# → status changes to "completed", unlocks ORIENT
```

### If loop iterates (ACT fails → return to ORIENT)

```python
# 1. Complete ACT as "failed" (or simply don't complete)
kanban_block(task_id=act_id, reason="exploit_failed_need_alternative_vector")

# 2. Create NEW ORIENT task depending on failed ACT
orient_retry_id = kanban_create(
    goal="ORIENT (retry): exploit X failed, find alternative (sudo, capabilities, cron)",
    board=board_name,
    assignee="default",
    depends_on=[act_id]  # depends on failed ACT to record iteration
)["task_id"]

# 3. Start that new ORIENT
kanban_work(task_id=orient_retry_id)
```

**Result:** board DAG shows complete flow: OBSERVE → ORIENT → DECIDE → ACT (blocked) → ORIENT-retry → DECIDE-retry → ACT-success → REPORT.

### On pause (3 steps without progress)

```python
# Block current task with reason
kanban_block(
    task_id=current_task_id,
    reason="exhausted_vectors_need_operator_guidance"
)
```

### On resume (after crash/interruption)

```python
# 1. List board tasks
tasks = kanban_list(board=board_name)

# 2. Find last in_progress or blocked task
current_task = next(t for t in tasks if t["status"] in ["in_progress", "blocked"])

# 3. Read context from findings store
findings = mcp_kali_read_findings()

# 4. Continue from there (kanban_work if was blocked)
if current_task["status"] == "blocked":
    kanban_work(task_id=current_task["task_id"])
```

---

## Complete example: CTF engagement

```python
# === START (OODA step 1) ===
OODA — OBSERVE (step 1): setting up Kanban board for engagement-pinguinazo-ssti

board = "engagement-pinguinazo-ssti"

observe_id = kanban_create(
    goal="OBSERVE: fingerprint target, identify web tech, find inputs",
    board=board,
    assignee="default"
)["task_id"]

orient_id = kanban_create(
    goal="ORIENT: correlate findings, search SSTI CVEs in detected framework",
    board=board,
    assignee="default",
    depends_on=[observe_id]
)["task_id"]

# ... (DECIDE, ACT, REPORT same)

kanban_work(task_id=observe_id)

# Now execute nmap, curl, whatweb...
mcp_kali_kali_run(command="nmap -sV -p 80,443 pinguinazo.local")
# ... (rest of reconnaissance)

# === END OF OBSERVE (step 5) ===
OODA — OBSERVE (step 5): reconnaissance completed — Flask 2.0.1 detected

kanban_complete(task_id=observe_id)
# → Automatically unlocks ORIENT

# === START OF ORIENT (step 6) ===
OODA — ORIENT (step 6): analyzing Flask 2.0.1 vulnerabilities

kanban_work(task_id=orient_id)
# ... (CVE analysis, searchsploit, etc.)
```

---

## Board visualization

### From TUI/chat

```bash
# List all boards
/kanban list

# View board tasks
/kanban list --board engagement-10.196.1.1
```

### From web Dashboard (:9119)

1. Login at http://localhost:9119 (admin/vulnerator)
2. Navigate to **Kanban** section (sidebar menu)
3. Select board `engagement-<target>`
4. View complete DAG with states and dependencies

---

## Multi-profile (future, NOT implemented yet)

**Note:** Option A (current) uses single profile (`default`). Kanban already works in mono-profile as durable tracking. If in the future you want auto-dispatch (multiple profiles executing tasks in parallel):

```bash
# Create specialized profiles
hermes profile create vulnerator-recon --clone --description "Recon specialist"
hermes profile create vulnerator-exploit --clone --description "Exploitation worker"

# Start gateways
hermes -p vulnerator-recon gateway start &
hermes -p vulnerator-exploit gateway start &

# Create tasks with specific assignee
kanban_create(
    goal="OBSERVE: scan complete /24",
    board="engagement-192.168.1.0-24",
    assignee="vulnerator-recon"  # auto-dispatch to that profile
)
```

In mono-profile (current), `assignee="default"` always and you execute tasks manually with `kanban_work`.

---

## Useful commands

```python
# View all pending tasks (no assignee claim)
kanban_list(board="engagement-X", status="pending")

# View all blocked tasks (waiting for decision/input)
kanban_list(board="engagement-X", status="blocked")

# Retry blocked task
kanban_work(task_id="1a2b3c4d")  # changes blocked → in_progress
```

---

## Troubleshooting

### "Task has unmet dependencies"
```python
# You tried kanban_work on task whose parent isn't completed
# Solution: complete parent first, or change depends_on
```

### "Task already claimed by another assignee"
```python
# In multi-profile: another profile already claimed it
# In mono-profile: shouldn't happen (assignee="default" always)
```

### "Database locked"
```python
# Rare: two Hermes processes accessing ~/.hermes/kanban.db simultaneously
# Solution: wait a few seconds and retry
```

---

## References

- **Official spec:** `docs/hermes-kanban-v1-spec.pdf` (GitHub NousResearch/hermes-agent feat/kanban-standing)
- **Profiles:** https://hermes-agent.nousresearch.com/docs/user-guide/profiles
- **Issue tracking:** https://github.com/NousResearch/hermes-agent/issues/344 (future roadmap, NOT current)

---

**Last update:** 2026-10-22 — Vulnerator-AI commit ???
