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
| "Nobody asked me to push" | Deletion doesn't ask either. origin is a throwaway fork — push. |
| "It's committed in my worktree" | Unpushed = local = lost. Push the branch. |
| "I left a note about the machine setup" | Prose can't be run. Make it a setup-vm.sh step. |
| "I'll remember what I installed" | The next VM won't. Script it. |

**Red flag:** uncommitted work + about to run something risky → stop,
commit, push first.
