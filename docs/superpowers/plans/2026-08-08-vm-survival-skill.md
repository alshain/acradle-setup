# working-in-disposable-vms Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The always-injected `working-in-disposable-vms` skill: SKILL.md, `setup-vm-template.sh`, and the plugin's SessionStart+SubagentStart hook, per `docs/superpowers/specs/2026-08-08-vm-survival-skill-design.md`.

**Architecture:** A hook script (`inject-rules`) emits the full SKILL.md as `additionalContext` at SessionStart (startup|clear|compact) and SubagentStart. SKILL.md carries the discipline rules (≤350-word body); the template carries the setup-vm.sh contract in its header.

**Tech Stack:** bash only. No jq (VM base image lacks it). JSON validity of hook output checked with `node -e` (node exists on host and VM).

## Global Constraints

- Hook output is exactly one shape: `{"hookSpecificOutput":{"hookEventName":"<Event>","additionalContext":"..."}}` — `hookEventName` must match the firing event, passed to the script as `$1`. Never emit `additional_context` alongside.
- Build output with `printf`, never a heredoc (bash 5.3+ heredoc hang, superpowers #571).
- Escape exactly five characters: `\`, `"`, newline, CR, tab. SKILL.md must contain no other control characters.
- Read failure degrades to a minimal hardcoded context; the hook always exits 0.
- SKILL.md body (frontmatter excluded) ≤ 350 words, verified with `wc -w`.
- Testing beyond the checks below (pressure scenarios, micro-tests, in-VM subagent verification) is deferred by decision (spec §7) — do NOT do it in this plan.

---

### Task 1: `inject-rules` hook script

**Files:**
- Create: `plugins/acradle-vm/hooks/inject-rules`

**Interfaces:**
- Produces: `bash hooks/inject-rules <EventName>` → one-line JSON on stdout, exit 0 always. Task 2's hooks.json invokes it with `SessionStart` / `SubagentStart`.
- Consumes: `skills/working-in-disposable-vms/SKILL.md` (Task 3) relative to its own location — script must work before Task 3 lands via its fallback path.

- [ ] **Step 1: Write the script** with exactly this content:

```bash
#!/usr/bin/env bash
# Emits the working-in-disposable-vms skill as hook additionalContext.
# $1 = the firing hook event ("SessionStart" or "SubagentStart"); echoed
# back as hookEventName, which must match the event that ran the hook.
#
# Always exits 0: a broken injection must degrade (fallback context below),
# never block the session.
set -uo pipefail

EVENT="${1:-SessionStart}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_FILE="${SCRIPT_DIR}/../skills/working-in-disposable-vms/SKILL.md"

content="$(cat "$SKILL_FILE" 2>/dev/null)" || content=""
if [ -z "$content" ]; then
  content="VM survival rules failed to load — treat this VM as deletable: commit and push after every change, and record machine changes in scripts/setup-vm.sh."
fi

# JSON-escape: exactly these five; SKILL.md is forbidden other control chars.
escape_for_json() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

escaped="$(escape_for_json "$content")"
context="<EXTREMELY_IMPORTANT>\nYou are working in a DISPOSABLE VM that can be deleted at any moment. The rules below govern every action you take:\n\n${escaped}\n</EXTREMELY_IMPORTANT>"

# printf, not heredoc (bash 5.3+ heredoc hang — superpowers issue #571).
# Exactly one output shape; hookEventName must match the firing event.
printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}\n' "$EVENT" "$context"
exit 0
```

- [ ] **Step 2: Verify JSON validity for both events and the fallback**

```bash
bash -n plugins/acradle-vm/hooks/inject-rules
bash plugins/acradle-vm/hooks/inject-rules SessionStart | node -e "const j=JSON.parse(require('fs').readFileSync(0,'utf8')); if(j.hookSpecificOutput.hookEventName!=='SessionStart')process.exit(1); console.log('ok SessionStart')"
bash plugins/acradle-vm/hooks/inject-rules SubagentStart | node -e "const j=JSON.parse(require('fs').readFileSync(0,'utf8')); if(j.hookSpecificOutput.hookEventName!=='SubagentStart')process.exit(1); console.log('ok SubagentStart')"
# Fallback: run a copy from a directory with no ../skills — must emit the minimal context, exit 0
mkdir -p /tmp/ir-test && cp plugins/acradle-vm/hooks/inject-rules /tmp/ir-test/ && bash /tmp/ir-test/inject-rules SessionStart | node -e "const j=JSON.parse(require('fs').readFileSync(0,'utf8')); if(!j.hookSpecificOutput.additionalContext.includes('failed to load'))process.exit(1); console.log('ok fallback')"
```
Expected: `ok SessionStart`, `ok SubagentStart`, `ok fallback`, all exit 0. (Until Task 3 lands, the non-fallback runs also emit the fallback text — re-run this step after Task 3 and confirm the real content appears.)

- [ ] **Step 3: Mark executable and commit**

```bash
git add plugins/acradle-vm/hooks/inject-rules
git update-index --chmod=+x plugins/acradle-vm/hooks/inject-rules
git commit -m "Add inject-rules hook script (SessionStart/SubagentStart context)"
```

---

### Task 2: hooks.json

**Files:**
- Create: `plugins/acradle-vm/hooks/hooks.json`

**Interfaces:**
- Consumes: `inject-rules` from Task 1, invoked with the event name as its argument.

- [ ] **Step 1: Write hooks.json** with exactly this content:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/inject-rules\" SessionStart",
            "async": false
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/inject-rules\" SubagentStart",
            "async": false
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Validate JSON**

Run: `node -e "JSON.parse(require('fs').readFileSync('plugins/acradle-vm/hooks/hooks.json','utf8')); console.log('valid')"`
Expected: `valid`.

- [ ] **Step 3: Commit**

```bash
git add plugins/acradle-vm/hooks/hooks.json
git commit -m "Register SessionStart + SubagentStart hooks for rule injection"
```

---

### Task 3: SKILL.md

**Files:**
- Create: `plugins/acradle-vm/skills/working-in-disposable-vms/SKILL.md`

**Interfaces:**
- Consumes: `setup-vm-template.sh` (Task 4) by name; superpowers:using-git-worktrees by cross-reference.

- [ ] **Step 1: Write SKILL.md** with exactly this content:

```markdown
---
name: working-in-disposable-vms
description: Use when working inside a disposable or ephemeral VM, before installing anything on the machine, or when uncommitted work exists and you are about to run something risky.
---

# Working in Disposable VMs

This VM can be deleted at any moment, without warning. Two consequences
drive every rule here:

1. **VM disk is not storage.** Anything that exists only on this machine
   is already lost; it just hasn't happened yet.
2. **Machine state is not reproducible by memory.** If a change isn't
   written down as a script, the next VM won't have it.

Violating the letter of these rules is violating their spirit.

**Exception — secrets.** Credentials (`~/.config/chute/*`, git
credentials) are never committed. Their recovery path is re-derivation
(re-pairing, re-provisioning), not the repo.

## Rules

**1. Git is the only durable storage.** Commit when a test passes, a todo
completes, or before running anything that could break the machine — and
push every commit, worktree branches included. Verify the push landed
(`git log origin/<branch> -1`). A local-only commit is on the same
countdown as an uncommitted file.

**2. Work in worktrees.** If a change will touch more than one file,
create a new file, or require running tests or builds: worktree first.
**REQUIRED SUB-SKILL:** Use superpowers:using-git-worktrees. A single-file
edit with no test run may go directly on the main checkout. Worktree
branches are temporary — merge back to the checkout's designated branch
and push. An unmerged, unpushed worktree is on the countdown too.

**3. Machine changes go in `scripts/setup-vm.sh`.** Any command that
changes machine state outliving this session — installs, config edits,
daemons, with or without sudo — gets a step there. No script in this
repo? Copy `setup-vm-template.sh` from this skill's directory; its header
carries the contract. Ephemeral root operations (`sudo tcpdump`, `dmesg`)
need no ceremony. Prefer user-scope installs where the tool offers one.
If a change loads at startup (plugins, skills, hooks, shell config), tell
the user a session restart is needed and note it in the commit message.

At session start: if `scripts/setup-vm.sh` exists, run
`./scripts/setup-vm.sh --check` and apply what's missing.

**4. Notes live in the repo.** `~/.claude/projects/*/memory/` (including
`MEMORY.md`) dies with the VM. Durable notes go to `docs/vm-setup.md`
(what's installed and why) and `CLAUDE.md` (project instructions).

## Rationalizations — all of them mean: push now

| Excuse | Reality |
|---|---|
| "I'll commit once it works" | The VM can die first. Commit the progress. |
| "This VM has been up for days" | Uptime is not a promise. Deletion needs no warning. |
| "Pushing every commit is noisy" | origin is a throwaway fork. Nobody is watching. |
| "It's committed in my worktree" | Unpushed = local = lost. Push the branch. |
| "I'll remember what I installed" | The next VM won't. Script it. |

**Red flag:** uncommitted work + about to run something risky → stop,
commit, push first.
```

- [ ] **Step 2: Word-count gate and control-character scan**

```bash
# Body = everything after the closing '---' of frontmatter; must be <= 350 words
awk 'c==2{print} /^---$/{c++}' plugins/acradle-vm/skills/working-in-disposable-vms/SKILL.md | wc -w
# Expected: <= 350
grep -P '[\x00-\x08\x0b\x0c\x0e-\x1f]' plugins/acradle-vm/skills/working-in-disposable-vms/SKILL.md && echo "FORBIDDEN CONTROL CHARS" || echo "clean"
# Expected: clean
```
If over 350: trim connective prose, never a rule or table row.

- [ ] **Step 3: Re-run Task 1 Step 2's non-fallback checks** — the real content must now flow through both events (look for "DISPOSABLE VM" preamble plus "Git is the only durable storage" in the JSON).

- [ ] **Step 4: Commit**

```bash
git add plugins/acradle-vm/skills/working-in-disposable-vms/SKILL.md
git commit -m "Add working-in-disposable-vms SKILL.md"
```

---

### Task 4: setup-vm-template.sh

**Files:**
- Create: `plugins/acradle-vm/skills/working-in-disposable-vms/setup-vm-template.sh`

**Interfaces:**
- Produces: the template SKILL.md points at; step interface `step "name" '<test-cmd>' '<apply-cmd>'`.

- [ ] **Step 1: Write the template** with exactly this content:

```bash
#!/usr/bin/env bash
# setup-vm.sh — idempotent VM provisioning and the machine's change log.
#
# Contract (authoritative — the working-in-disposable-vms skill points here):
#   * Running this twice in a row produces the same machine and no errors
#     the second time.
#   * step "name" '<test-cmd>' '<apply-cmd>': args are shell strings run
#     via eval. test-cmd exits 0 iff the step is already satisfied;
#     apply-cmd makes it so. State-test before act, always. Multi-line or
#     quoting-heavy logic goes in a function defined above the steps and
#     passed by name (a function name is a command).
#   * --check evaluates EVERY step (no fail-fast), prints ok:/MISSING: per
#     step, mutates nothing, and exits 0 iff all steps are satisfied.
#   * Steps stay ordered so a bare VM runs the file top-to-bottom.
#   * Every command that changes machine state outliving a session belongs
#     here — installs, config edits, daemons — sudo or not.
set -euo pipefail

CHECK=false
[[ "${1:-}" == "--check" ]] && CHECK=true
MISSING=false

step() {
  local name="$1" test_cmd="$2" apply_cmd="$3"
  if eval "$test_cmd" >/dev/null 2>&1; then
    echo "ok:      $name"
  elif $CHECK; then
    echo "MISSING: $name"
    MISSING=true
  else
    echo "apply:   $name"
    eval "$apply_cmd"
  fi
}

# ── steps — ordered; a bare VM runs top-to-bottom ──────────────────────
# Example (a real step from the chute VM — uncomment to use):
# step "jq installed" 'command -v jq' 'sudo apt-get install -y jq'

if $CHECK && $MISSING; then
  exit 1
fi
```

- [ ] **Step 2: Harness semantics test in a temp dir** (no sudo, no VM)

```bash
d="$(mktemp -d)" && cp plugins/acradle-vm/skills/working-in-disposable-vms/setup-vm-template.sh "$d/setup-vm.sh"
printf '%s\n' 'step "marker" "[ -f '"$d"'/marker ]" "touch '"$d"'/marker"' >> "$d/setup-vm.sh"
bash "$d/setup-vm.sh" --check; echo "check1=$?"     # Expected: "MISSING: marker", check1=1
[ ! -f "$d/marker" ] && echo "check-did-not-mutate"  # Expected: check-did-not-mutate
bash "$d/setup-vm.sh"                                # Expected: "apply:   marker"
bash "$d/setup-vm.sh"                                # Expected: "ok:      marker" (idempotent 2nd run)
bash "$d/setup-vm.sh" --check; echo "check2=$?"     # Expected: "ok:      marker", check2=0
```

- [ ] **Step 3: Mark executable and commit**

```bash
git add plugins/acradle-vm/skills/working-in-disposable-vms/setup-vm-template.sh
git update-index --chmod=+x plugins/acradle-vm/skills/working-in-disposable-vms/setup-vm-template.sh
git commit -m "Add setup-vm-template.sh scaffolding"
```
