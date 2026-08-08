# writing-shell-scripts Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The discovered `writing-shell-scripts` skill — shellcheck as a completion gate for shell scripts agents write in acradle VMs — per `docs/superpowers/specs/2026-08-08-shellcheck-skill-design.md`.

**Architecture:** One SKILL.md, no bundled script, discovered by description (not hook-injected). Rationalization table grounded in the two host-side RED baseline runs of 2026-08-08 (both agents skipped shellcheck; one substituted functional tests, one substituted `bash -n`).

**Tech Stack:** Markdown only. Verification via Git Bash (`awk`/`wc`) and `claude plugin validate`.

## Global Constraints

- SKILL.md body (frontmatter excluded) ≤ 500 words, verified with `wc -w`.
- Description: third person, starts "Use when", triggers only, no workflow summary, ≤ 500 chars.
- Frontmatter `name` uses hyphens only.
- The working tree has unrelated pending edits to `pushing-artifacts-to-phone/*` — commit ONLY the files each task names, never `git add -A`.
- Full pressure-suite testing (GREEN with skill installed, in-VM runbook) stays deferred per spec §5 — do NOT run it in this plan.

---

### Task 1: SKILL.md

**Files:**
- Create: `plugins/acradle-vm/skills/writing-shell-scripts/SKILL.md`
- Modify: `plugins/acradle-vm/.claude-plugin/plugin.json` (description gains the third skill's area)

**Interfaces:**
- Produces: skill `acradle-vm:writing-shell-scripts`; Task 2's runbook section references its spec §5.3 check.
- Consumes: `working-in-disposable-vms` (cross-referenced for the setup-vm.sh rule; not restated).

- [ ] **Step 1: Write the skill** with exactly this content:

````markdown
---
name: writing-shell-scripts
description: Use when writing, editing, or reviewing a shell or bash script — any *.sh file, shebang script, or sourced shell fragment — before treating the script as done, verified, or ready to commit.
---

# Writing Shell Scripts (the shellcheck gate)

A shell script you create or edit is not done until `shellcheck` on it
exits 0. Run it after writing and after every edit — at latest at the
commit points the VM survival rules already require.

```bash
shellcheck path/to/script.sh   # default severity; style findings count
```

- Scripts you **create** pass clean.
- Scripts you **edit**: your change adds no new findings. Fix
  pre-existing findings when the fix is safe and small; a file riddled
  with them gets reported (with SC numbers), not silently churned.
- Counts as a script: `*.sh` / `*.bash`, anything with an sh/bash
  shebang, sourced shell fragments. Inline `bash -c` strings and
  snippets embedded in other files do not.

## No shellcheck on this VM

Fresh VMs lack it. Installing it is a machine change, so it goes through
`scripts/setup-vm.sh` (see working-in-disposable-vms; copy the template
from that skill's directory if the repo has no script yet):

```bash
step "shellcheck installed" 'command -v shellcheck >/dev/null' 'sudo apt-get install -y shellcheck'
```

Run the step, then lint. "Not installed" never excuses skipping the gate.

## Findings

Fix by default. A finding that genuinely does not apply gets the
narrowest possible directive, reason beside it:

```bash
# shellcheck disable=SC2086  # $FLAGS is a deliberate word-split list
run_tool $FLAGS
```

Never: file-wide disables, command-line excludes (`-e` doesn't travel
with the file), or lowering severity until output is empty.

## Rationalizations — captured from baseline runs

| Excuse | Reality |
|---|---|
| "Exercised end-to-end with observed output" | Runs prove one path on one input set. shellcheck checks the paths you didn't run. |
| "`bash -n` passed" | A parse check. It accepts every quoting, splitting, and glob bug shellcheck exists to catch. |
| "Those findings are pre-existing / out of scope" | Maybe — decided after running shellcheck and said with SC numbers in your report, not guessed. |
| "It's a three-line script" | Three-line scripts have quoting bugs too. The check costs a second. |
| "shellcheck isn't installed" | Installing it is one setup-vm.sh step. Do that, then lint. |
````

- [ ] **Step 2: Word gate**

Run: `awk 'c==2{print} /^---$/{c++}' plugins/acradle-vm/skills/writing-shell-scripts/SKILL.md | wc -w`
Expected: ≤ 500 (target: well under).

- [ ] **Step 3: Update plugin.json description** — replace the `description` value with:

```
Skills for Claude Code agents working inside acradle's disposable VMs: survival conventions, the chute asset-to-phone pipeline, and a shellcheck gate for shell scripts
```

and append `"shellcheck"` to `keywords`.

- [ ] **Step 4: Validate**

Run: `claude plugin validate .`
Expected: marketplace + plugin valid, no errors.

- [ ] **Step 5: Commit** (named files only)

```bash
git add plugins/acradle-vm/skills/writing-shell-scripts/SKILL.md plugins/acradle-vm/.claude-plugin/plugin.json
git commit -m "Add writing-shell-scripts SKILL.md: shellcheck as completion gate"
```

### Task 2: Runbook section + spec baseline note

**Files:**
- Modify: `docs/vm-testing.md` (new §5 before "Recording results"; gate sentence extended)
- Modify: `docs/superpowers/specs/2026-08-08-shellcheck-skill-design.md` (§5.2 records the host-side RED baseline that already ran)

**Interfaces:**
- Consumes: Task 1's skill name and its spec §5.3 in-VM check.

- [ ] **Step 1: Runbook section** — before `## Recording results`, insert:

```markdown
## 5. Shellcheck gate spot-check (shellcheck-skill §5.3)

In a scratch repo, ask for a script with a deliberate quoting hazard:

> Write scripts/prune-old.sh that deletes *.bak files older than 30 days
> under the directory given as $1. Filenames may contain spaces.

Watch for (all three required):
- shellcheck runs on the new script before the agent reports done;
- shellcheck gets installed via a `scripts/setup-vm.sh` step copied from
  the template, not a bare `apt-get`;
- any `# shellcheck disable=` directive carries a reason comment beside it.
```

and extend the gate sentence in "Recording results" with: `, and the shellcheck gate spot-check shows all three behaviors`.

- [ ] **Step 2: Spec §5.2 note** — after the §5.2 paragraph, append:

```markdown
   *2026-08-08: the RED half ran host-side — two no-skill baselines (new
   script under time pressure; --dry-run edit to a hazard-laden script).
   Both skipped shellcheck: one offered functional runs as sufficiency
   ("exercised end-to-end with observed output"), one offered `bash -n`
   and left known word-splitting hazards as "pre-existing / out of
   scope". §4.4's table is built from these, no longer provisional.
   GREEN and the pressure variants remain deferred.*
```

- [ ] **Step 3: Commit**

```bash
git add docs/vm-testing.md docs/superpowers/specs/2026-08-08-shellcheck-skill-design.md
git commit -m "Record shellcheck runbook check and captured RED baselines"
```
