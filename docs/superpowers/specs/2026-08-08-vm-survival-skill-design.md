# working-in-disposable-vms skill — design

Date: 2026-08-08
Status: approved design; implementation first, testing deferred (see §6)

## 1. Purpose

Agents in acradle VMs work on machines that can be deleted at any moment.
The conventions that make that survivable — commit and push relentlessly,
script every machine change, keep notes in the repo — currently live in
`AGENT-ENVIRONMENT.md`, a portable file copied verbatim into each prison
repo. Copies drift, and a file an agent must decide to read cannot govern
the agent's first action.

This skill moves those conventions into the `acradle-vm` plugin and injects
them into **every session at start** via a SessionStart hook, the same
mechanism superpowers uses. The plugin becomes the sole distribution
channel; the per-repo file is retired.

## 2. Decisions

| Decision | Choice |
| --- | --- |
| Skill shape | Always-injected discipline skill + bundled `setup-vm.sh` template (approach C; pointer-only injection rejected — discipline content must precede the first action) |
| Distribution | Second skill in plugin `acradle-vm` (see 2026-08-08-chute-push-skill-design.md); adds the plugin's first hook |
| AGENT-ENVIRONMENT.md | Retired. Prison repos delete their copy once the plugin is in the VM image. No stub left behind. `docs/vm-setup.md` and `CLAUDE.md` keep their roles |
| Branch policy | Not this skill's business — invariant only (commit often, push always, verify), branch choice comes from the checkout / CLAUDE.md |
| Worktrees | New rule: non-trivial work happens in a worktree by default, via superpowers' `using-git-worktrees` (cross-referenced, not duplicated) |
| Testing | Deferred by decision, not skipped — §6 |

## 3. Layout

```
plugins/acradle-vm/
  .claude-plugin/plugin.json
  hooks/
    hooks.json                        # SessionStart, matcher "startup|clear|compact"
    session-start                     # bash; emits SKILL.md as additionalContext
  skills/
    pushing-artifacts-to-phone/       # previous spec
    working-in-disposable-vms/
      SKILL.md
      setup-vm-template.sh            # scaffolding; loads only when copied
```

## 4. Hook

Mirrors superpowers' wiring: `hooks.json` registers SessionStart (matcher
`startup|clear|compact`) running a bash script; the script cats
`skills/working-in-disposable-vms/SKILL.md`, JSON-escapes it (bash parameter
substitution, no jq — the VM image has none), and emits
`hookSpecificOutput.additionalContext` wrapped in a short "you are working
in a disposable VM" preamble.

Difference from superpowers, deliberate: their hook injects a skill about
finding other skills; ours injects the rules themselves. The session pays
the token cost once and the rules govern from the first action.

Target is the Ubuntu VM only, so the script is plain bash — no polyglot
`.cmd` wrapper. If host-side (Windows) testing of the hook is ever wanted,
superpowers' `run-hook.cmd` shows the pattern to copy.

## 5. SKILL.md content

Frontmatter description: "Use when working inside a disposable/ephemeral
VM…" — discovery is moot in acradle VMs (the hook injects it), but the
description serves anyone browsing the skill list. Keywords: disposable,
ephemeral, VM, prison, commit, push, worktree, setup-vm, provisioning,
memory.

Body, in order:

1. **The model, stated as stakes.** The VM can be deleted at any moment,
   without warning. Two consequences (kept nearly verbatim from
   AGENT-ENVIRONMENT.md — it is the best part of the file): VM disk is not
   storage — anything that exists only here is already lost, it just hasn't
   happened yet; machine state is not reproducible by memory — if a change
   is not written down as a script, the next VM won't have it.
2. **Git is the only durable storage.** Commit after each meaningful unit
   of work; push every commit; verify the push landed. A local-only commit
   is on the same countdown as an uncommitted file. Branch policy comes
   from the checkout / CLAUDE.md, not this skill.
3. **Work in worktrees by default.** Before non-trivial work, create one —
   REQUIRED SUB-SKILL: superpowers' `using-git-worktrees`. Direct edits on
   the main checkout only for trivial single-file changes.
4. **Every machine modification lands in `scripts/setup-vm.sh`** —
   idempotent (state-test, then act), `--check`-capable (report without
   changing, exit nonzero if anything is missing), ordered so a bare VM
   runs it top-to-bottom. No script in the repo? Copy
   `setup-vm-template.sh` from this skill's directory and append a step.
   If the change loads at startup (plugins, skills, hooks, shell config),
   restart the session after applying it.
5. **Notes live in the repo, never machine-local memory.**
   `~/.claude/projects/*/memory/` dies with the VM. Table: durable notes →
   `docs/vm-setup.md` (what is installed and why) and `CLAUDE.md`
   (project instructions).
6. **Sudo sparingly.** Prefer user-scope installs (`~/.local`, `~/.claude`,
   project-local); root only when genuinely required — and scripted in
   `setup-vm.sh` like everything else.
7. **Bulletproofing** (this is a discipline skill; agents rationalize under
   pressure): rationalization table — "I'll commit once it works" / "this
   VM has been up for days" / "pushing every commit is noisy" / "I'll
   remember what I installed" — each with the one-line reality. Red-flags
   list, e.g. uncommitted work + about to run something risky → stop, push
   first.

Session-start bootstrap (from AGENT-ENVIRONMENT.md): run
`./scripts/setup-vm.sh --check` at the start of a session, apply if
missing pieces are reported.

## 6. setup-vm-template.sh

Bundled scaffolding an agent copies to `scripts/setup-vm.sh` when the repo
lacks one. Ships with the harness right and no real steps: `set -euo
pipefail`, `--check` flag, a `step "name" <test> <apply>` helper
(state-test before act; `--check` reports MISSING and exits 1), a
commented example step, and an ordered-steps section. Exact helper
semantics (commands vs functions, apt/npm/pip idioms) are implementation
detail; the template's contract is: run twice = same machine, no errors
the second time; `--check` mutates nothing.

## 7. Testing (deferred by decision)

Implementation proceeds first at the user's direction. Before the plugin
lands in VM images:

1. **Hook test** — run the session-start script standalone; valid JSON out,
   content intact (escaping bugs are the likely failure).
2. **Skill RED/GREEN** — pressure scenarios per writing-skills: baseline
   agent in a scratch repo under time/sunk-cost pressure (does it commit?
   push? script the install?), then with the skill injected; document
   rationalizations verbatim and close them in the table. This skill type
   is exactly where pressure-testing is load-bearing.
3. **Template test** — copy template, add a step, run twice: second run
   changes nothing and reports all-ok; `--check` on a missing step exits
   nonzero without mutating.

## 8. Out of scope

* Removing AGENT-ENVIRONMENT.md from existing prison repos (a per-repo
  cleanup once the plugin is installed in the VM image).
* VM image / install automation for the plugin itself — still the separate
  deliverable it was in the chute-push spec.
* Chute usage, signing keys, or any project-specific instruction — other
  skills / per-repo CLAUDE.md.
