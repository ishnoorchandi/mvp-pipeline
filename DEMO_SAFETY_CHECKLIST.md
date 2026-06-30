# MVP Pipeline Demo Safety Checklist

## Purpose

This checklist is for safe, local demonstration of the MVP Pipeline guided workflow. Follow this order to avoid accidental builds, pushes, or changes to company repositories.

## Prerequisites

- Run the backend: `source venv/bin/activate && python backend/app.py`
- Run the frontend: `cd frontend && npm run dev`
- Use a **fixture or personal sandbox run** — never a OneHR / company repo in build mode.

---

## Safe demo order

1. Open the app and create a new **Existing App Upgrade** run (or open an existing fixture run).
2. Answer all requirement questions in the **Requirements Conversation** card.
3. Click **Approve Requirements** — confirm `requirements_signoff_state.json` is written.
4. Answer all architecture questions in the **Architecture Conversation** card.
5. Click **Approve Architecture** — confirm `architecture_signoff_state.json` is written.
6. Open the **Global Instructions** card.
7. Click **Generate GLOBAL_INSTRUCTIONS.md** — confirm `GLOBAL_INSTRUCTIONS.md` is written.
8. Open the **Sprint Orchestrator** card.
9. Enter a sprint number and click **Initialize Sprint Orchestrator**.
10. Click **Generate Build Prompt** — a `sprint_<n>_build_prompt.md` file is produced.
11. Open the artifact viewer and **copy the build prompt manually** into a Claude Code session.
12. After Claude Code completes the sprint, return to the app.
13. Open the **Record result manually** section in the Sprint Orchestrator card.
14. Record: Build attempt → **completed**.
15. Record: Smoke check → **passed** (or **waived** with a written reason).
16. Record: Review → **passed** (or **waived** with a written reason).
17. Record: Governance → **passed** (or **waived** with a written reason).
18. If checks failed, click **Generate Fix Prompt** and repeat.
19. When all checks pass, click **Approve Sprint Completion** and confirm.

---

## Never do during demo

- Do not use a company-protected repository in direct-build mode.
- Do not push to `main` or any protected branch.
- Do not edit `.env`, secrets, credentials, or `venv/`.
- Do not run destructive commands (`git reset --hard`, `git push --force`, etc.).
- Do not click "Approve Requirements" or "Approve Architecture" on a run you do not own.
- Do not use the `runs/` folder from another user's session without inspecting it first.

---

## Copy-only rule

The app generates prompts. **It does not run Claude Code automatically.**

> Every sprint build prompt, fix prompt, continuation prompt, and handoff must be copied manually into Claude Code by the operator.

The Sprint Orchestrator and Guided Workflow cards both display this reminder.

---

## Company-repo safety

The pipeline enforces a planning gate that requires:

1. Requirements approved
2. Architecture approved
3. `GLOBAL_INSTRUCTIONS.md` created

Company/protected repositories must also use **sandbox mode** or **plan-only mode** unless explicitly unlocked. The Build Workspace card shows the active mode and whether the original repo was modified.

---

## Key artifacts

| Artifact | Purpose |
|---|---|
| `requirements.md` | Official flattened requirements |
| `approved_architecture.md` | Signed-off tech stack |
| `GLOBAL_INSTRUCTIONS.md` | Build constitution for Claude Code |
| `sprint_<n>_build_prompt.md` | Copy this into Claude Code |
| `sprint_<n>_fix_prompt.md` | Copy this after a failed check |
| `sprint_<n>_continuation_prompt.md` | Copy this to continue a session |
| `sprint_<n>_handoff.md` | Read this at the start of a new session |
| `sprint_<n>_completion_approval.md` | Proof of sprint sign-off |
| `sprint_orchestrator_state.json` | Full orchestrator state |

---

## Running tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
cd frontend && npm run build
```

All tests should pass. The TypeScript build should succeed with no errors.
