# writing-shell-scripts skill — design

Date: 2026-08-08
Status: designed in an autonomous session — decisions derived from repo
conventions, pending user review; implementation first, testing deferred
(see §5)

## 1. Purpose

Agents in acradle VMs write shell scripts constantly — `setup-vm.sh`
steps, helpers, harnesses — headless, with no reviewer between a quoting
bug and a broken run. shellcheck catches the standard bash hazard classes
mechanically. This skill makes it a completion gate: a shell script the
agent writes or edits is not done until shellcheck passes on it.

## 2. Decisions

| Decision | Choice |
| --- | --- |
| Shape | Standalone discovered skill `writing-shell-scripts`, third skill in plugin `acradle-vm`. Rejected: a rule in always-injected `working-in-disposable-vms` (shellcheck is code quality, not survival discipline, and that skill's always-loaded 500-word budget is deliberately tight); per-repo guidance (the drift problem that retired AGENT-ENVIRONMENT.md — the plugin is the sole distribution channel); a PostToolUse hook auto-running shellcheck on Write/Edit (strongest enforcement, but heavier than asked — needs shebang sniffing plus a shellcheck-missing degradation story; future hardening, §6) |
| Gate semantics | `shellcheck <file>` exits 0 at default severity (style findings included). Scripts the agent creates pass clean. Edits to existing scripts add no new findings; pre-existing findings are fixed when the fix is safe and small, reported rather than churned otherwise |
| What counts as a script | Files the agent creates or edits that are shell: `*.sh` / `*.bash`, a sh/bash shebang, or sourced shell fragments. Not: inline `bash -c` strings, heredoc snippets embedded in other files, cloud-init YAML |
| shellcheck availability | Not in the base image (git, node, claude only). Installing it is a machine change that outlives the session → a `scripts/setup-vm.sh` step per working-in-disposable-vms rule 3, the template's jq idiom: `step "shellcheck installed" 'command -v shellcheck >/dev/null' 'sudo apt-get install -y shellcheck'`. "Not installed" is never a reason to skip the gate |
| Findings policy | Fix, don't silence. `# shellcheck disable=SCnnnn` only at the narrowest scope with a same-line-or-adjacent reason comment. No file-wide disables, no command-line `-e` excludes (they don't travel with the file), no severity lowering |
| Token budget | Body ≤ 500 words (repo gate, `wc -w` on body); this skill targets well under — it is one rule plus mechanics |
| Testing | Deferred by decision, same hard gate as the sibling skills: the plugin does not land in VM images until §5 completes |

## 3. Layout

```
plugins/acradle-vm/skills/writing-shell-scripts/SKILL.md
```

No bundled script — the skill is judgment plus one command. `plugin.json`
needs no change; skills are discovered from `skills/`.

## 4. SKILL.md content

Frontmatter description, third person, trigger + timing, no workflow
summary: use when writing, editing, or reviewing a shell script, before
considering it done or committing it. Search keywords (shell, bash, sh,
script, shellcheck, lint, quoting, shebang) live in body and description.

Body, in order:

1. **The gate.** A shell script you create or edit is not done until
   `shellcheck` on it exits 0. Run it after writing and after every
   edit — the commit points the survival skill mandates are natural
   checkpoints. New scripts pass clean; edited scripts add no new
   findings.
2. **Getting shellcheck.** `command -v shellcheck` fails on a fresh VM:
   install it via a `scripts/setup-vm.sh` step (idiom above), copying
   the template from working-in-disposable-vms first if the repo has no
   script yet — cross-reference, don't restate that skill's rule.
3. **Handling findings.** Fix by default. A finding that genuinely does
   not apply gets the narrowest `# shellcheck disable=SCnnnn` directive
   with a reason comment beside it; anything broader is silencing, not
   handling.
4. **Rationalization table** (provisional until §5.2 captures real ones
   verbatim, matching how the survival skill's table was produced):
   "it's a three-line script" / "shellcheck isn't installed" / "the
   warning is a false positive" / "I already ran it when I created the
   file" / "it worked when I tested it" — each with a one-line reality.

## 5. Testing (deferred by decision)

Hard gate: the plugin does not land in VM images until all of this runs.

1. **Budget + structure** — `wc -w` on the body ≤ 500; `claude plugin
   validate` passes; description matches the writing-skills conventions.
2. **Skill RED/GREEN** — baseline agent in a scratch repo asked for a
   small bash helper under time pressure: does it run shellcheck
   unprompted (RED expected: no)? Then with the skill installed: gate
   honored, install step lands in setup-vm.sh, directives used only with
   reasons. Capture rationalizations verbatim; replace §4.4's table.
   *2026-08-08: the RED half ran host-side — two no-skill baselines (new
   script under time pressure; --dry-run edit to a hazard-laden script).
   Both skipped shellcheck: one offered functional runs as sufficiency
   ("exercised end-to-end with observed output"), one offered `bash -n`
   and left known word-splitting hazards as "pre-existing / out of
   scope". §4.4's table is built from these, no longer provisional.
   GREEN and the pressure variants remain deferred.*
3. **In-VM runbook** — section added to `docs/vm-testing.md`: ask the
   agent for a script with a deliberate quoting hazard; expect a
   shellcheck run before completion and the install recorded as a
   setup-vm.sh step, not a bare apt-get.

## 6. Out of scope

* PostToolUse hook enforcement (auto-lint on Write/Edit) — revisit after
  the skill's wording is field-tested.
* Linting this repo's own scripts (`chute-push`, `inject-rules`,
  template) in a host-side check — separate deliverable.
* Linters for other languages the agent writes in VMs.
