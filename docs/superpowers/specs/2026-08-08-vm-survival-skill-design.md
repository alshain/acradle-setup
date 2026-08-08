# working-in-disposable-vms skill — design

Date: 2026-08-08
Status: approved design, revised after three-lens subagent review;
implementation first, testing deferred (see §7)

## 1. Purpose

Agents in acradle VMs work on machines that can be deleted at any moment.
The conventions that make that survivable — commit and push relentlessly,
script every machine change, keep notes in the repo — currently live in
`AGENT-ENVIRONMENT.md`, a portable file copied verbatim into each prison
repo. Copies drift, and a file an agent must decide to read cannot govern
the agent's first action.

This skill moves those conventions into the `acradle-vm` plugin and injects
them at session start via a SessionStart hook, the same mechanism
superpowers uses. The plugin becomes the sole distribution channel; the
per-repo file is retired.

## 2. Decisions

| Decision | Choice |
| --- | --- |
| Skill shape | Always-injected discipline skill + bundled `setup-vm.sh` template (approach C; pointer-only injection rejected — discipline content must precede the first action) |
| Distribution | Second skill in plugin `acradle-vm` (see 2026-08-08-chute-push-skill-design.md); adds the plugin's first hook |
| Token budget | SKILL.md body ≤ 500 words, gated by `wc -w` in §7. Above the doctrine's <200 target for always-loaded skills — accepted deliberately: six rules plus bulletproofing don't fit 200, and superpowers' own always-injected skill is larger. (Originally 350; implementation showed the mandated content is 453 words — rules + table alone are 332 — so 350 was unreachable without cutting review-driven substance. Raised, with headroom for the RED-phase table replacement.) Compression lever: the setup-vm.sh contract lives in the template's header comments, not the skill body |
| AGENT-ENVIRONMENT.md | Retired. Prison repos delete their copy once the plugin is in the VM image. No stub left behind. `docs/vm-setup.md` and `CLAUDE.md` keep their roles. The file's fork-topology fact ("origin is the agent's own fork; pushing is always safe") is environment, not policy — it migrates to per-repo `CLAUDE.md` / acradle's provisioning docs, tracked in §8 |
| Runtime narrowing | Acknowledged: the hook reaches Claude Code only. Claude Code is the only supported in-VM runtime; if that changes, the conventions need an AGENTS.md-style re-export |
| Plugin dependency | The worktree rule hard-depends on superpowers being installed in the VM image (it is today — vm-setup.md lists v6.2.0, user scope). Stated prerequisite; without it the rule names a skill the agent can't invoke |
| Branch policy | Invariant only (commit often, push always, verify); branch choice comes from the checkout / CLAUDE.md. Worktree branches are temporary and merge back to the designated branch (§5.3) |
| Testing | Deferred by user direction — a knowing deviation from writing-skills' Iron Law, not an oversight. Hard gate: the plugin does not land in VM images until §7 completes |

## 3. Layout

```
plugins/acradle-vm/
  .claude-plugin/plugin.json
  hooks/
    hooks.json                        # SessionStart (startup|clear|compact) + SubagentStart
    inject-rules                      # bash; emits SKILL.md as additionalContext; takes event name as $1
  skills/
    pushing-artifacts-to-phone/       # previous spec
    working-in-disposable-vms/
      SKILL.md
      setup-vm-template.sh            # scaffolding; loads only when copied
```

## 4. Hook

`hooks.json` registers **two** events running the same script:

* **SessionStart**, matcher `startup|clear|compact` — the main session.
* **SubagentStart**, no matcher (all agent types) — SessionStart's
  `additionalContext` does not reach subagents (per the hooks reference),
  but SubagentStart supports `additionalContext` and it lands in the
  subagent's transcript, and plugin hooks.json may register it. Subagent
  coverage is therefore structural, not a prompt-forwarding convention.

Command pinned as (event name passed as the argument, echoed back as
`hookEventName` — the two events need different values there):

```json
"command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/inject-rules\" SessionStart"
```

(explicit `bash` prefix so the exec bit is irrelevant — this repo is
authored on Windows where `core.filemode` is off, and a bare-path command
would fail only at VM session start).

The script cats `skills/working-in-disposable-vms/SKILL.md`, JSON-escapes
it, and emits **exactly one** output shape:

```json
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}
```

`hookEventName` is required — omitting it makes the injection silently fail
to parse. Claude Code reads both `additional_context` and
`hookSpecificOutput` without deduplication, so never emit both. Build the
output with `printf`, not a heredoc (bash 5.3+ heredoc hang; superpowers
issue #571 — moot on the VM's bash 5.1, bites host-side testing).

**Escaping:** bash parameter substitution over five characters — `\`, `"`,
newline, carriage return, tab (no jq: the *base image* has none, and the
hook runs before any provisioning). Constraint that makes five enough:
SKILL.md must contain no control characters besides newline/tab; §7.1
verifies. No wrapper `.cmd`; the target is the Ubuntu VM only.

**Read failure:** if SKILL.md is missing or unreadable, emit a minimal
hardcoded context instead — "VM survival rules failed to load; treat this
VM as deletable: commit and push after every change" — so failure degrades
to the core invariant, never to an ungoverned session.

**Injection model:** injected at startup and re-injected after `clear` and
`compact` — re-injection on compact is the point, otherwise the rules
evaporate with the compacted context. `resume` is deliberately absent from
the matcher: the resumed transcript already contains the injection.

## 5. SKILL.md content

Frontmatter description, third person, triggers + violation symptoms, no
workflow summary: "Use when working inside a disposable/ephemeral VM,
before installing anything on the machine, or when uncommitted work exists
and you're about to run something risky." Search keywords (disposable,
ephemeral, VM, prison, commit, push, worktree, setup-vm, provisioning,
memory) live in the body, not the description.

Body (≤ 500 words), in order:

1. **The model, stated as stakes.** The VM can be deleted at any moment,
   without warning. Two consequences (kept nearly verbatim from
   AGENT-ENVIRONMENT.md): VM disk is not storage — anything that exists
   only here is already lost, it just hasn't happened yet; machine state
   is not reproducible by memory — if a change is not written down as a
   script, the next VM won't have it. Early, the spirit-vs-letter clause:
   violating the letter of these rules is violating their spirit.
   **Secrets carve-out:** credentials (`~/.config/chute/*`, git
   credentials) are the one thing never rescued into git — their recovery
   path is re-derivation (re-pairing), not the repo.
2. **Git is the only durable storage.** Commit when a test passes, a todo
   completes, or before running anything that could break the machine —
   and push every commit, worktree branches included. Verify the push
   landed (`git log origin/<branch>`). A local-only commit is on the same
   countdown as an uncommitted file.
3. **Work in worktrees.** If the change will touch more than one file,
   create a new file, or require running tests or builds → worktree first.
   **REQUIRED SUB-SKILL:** Use superpowers:using-git-worktrees. A
   single-file edit with no test run may go directly on the main checkout.
   Worktree branches are temporary: merge back to the checkout's
   designated branch and push; an unmerged, unpushed worktree is on the
   countdown too.
4. **Every machine modification lands in `scripts/setup-vm.sh`.** No
   script in the repo? Copy `setup-vm-template.sh` from this skill's
   directory and append a step — the template's header carries the
   idempotency contract (§6), the skill body only the rule. If the change
   loads at startup (plugins, skills, hooks, shell config), tell the user
   a session restart is needed and record it in the commit message.
   Session bootstrap: if `scripts/setup-vm.sh` exists, run `--check` at
   session start and apply what's missing; if it doesn't, nothing to do
   until the first machine change.
5. **Notes live in the repo, never machine-local memory.**
   `~/.claude/projects/*/memory/` (including `MEMORY.md`) dies with the
   VM. Durable notes → `docs/vm-setup.md` (what's installed and why),
   `CLAUDE.md` (project instructions).
6. **Persistence, not privilege, is what's regulated.** Root is available
   and the machine is disposable — ephemeral root operations (`sudo
   tcpdump`, `dmesg`, one-off diagnostics) need no ceremony; run them.
   The rule keys on an observable predicate: if the command changes
   machine state that outlives the session — installs, config edits,
   daemons, whether or not it needed sudo — it goes in `setup-vm.sh`.
   Within that, prefer user-scope installs (`$HOME`) where a tool offers
   one: they reproduce without root and can't break the base image.
7. **Bulletproofing.** Rationalization table — "I'll commit once it
   works" / "this VM has been up for days" / "pushing every commit is
   noisy" / "it's committed in my worktree" / "I'll remember what I
   installed" — each with a one-line reality. Red flags: uncommitted work
   + about to run something risky → stop, push first. Table entries are
   **provisional until §7.2's baseline runs** capture real
   rationalizations verbatim; they will be replaced, not merely appended
   to. No subagent carve-out and no prompt-forwarding rule: subagents get
   the same injection via the SubagentStart hook (§4) — these rules apply
   to every agent that can touch the machine.

## 6. setup-vm-template.sh

Bundled scaffolding an agent copies to `scripts/setup-vm.sh` when the repo
lacks one. Ships with the harness right and no real steps. Pinned
contract, stated in the template's header comments (the authoritative
home of these rules — the skill body just points here):

* Run twice = same machine, no errors the second time.
* `step "name" '<test-cmd>' '<apply-cmd>'` — args are shell strings run
  via `eval`; multi-line or fiddly logic goes in a function defined above
  and passed by name (a function name is a command). State-test before
  act, always.
* `--check` evaluates **every** step (no fail-fast), prints per-step
  `ok:`/`MISSING:`, mutates nothing, exits 0 iff all ok — nonzero exit
  with anything missing (an addition over AGENT-ENVIRONMENT.md, which
  specified only "report without changing"; the session-bootstrap flow
  depends on it).
* Steps ordered so a bare VM runs the file top-to-bottom.
* The shipped example step is real and runnable, not a placeholder:
  `step "jq installed" 'command -v jq >/dev/null' 'sudo apt-get install -y jq'`
  — an actual step from today's chute VM (which is also why "the VM has no
  jq" is only true of the base image), demonstrating the sudo-in-a-step
  idiom.

## 7. Testing (deferred by decision)

Implementation proceeds first at the user's direction. **Hard gate: the
plugin does not land in VM images until all of this has run.**

1. **Hook test** — run inject-rules standalone for both event arguments:
   valid JSON out with the matching `hookEventName`, content intact, the
   five-character escape set exercised, SKILL.md scanned for forbidden
   control characters, read-failure fallback produces the minimal
   context. Plus the budget gate: `wc -w` on SKILL.md body ≤ 500. In-VM:
   verify a spawned subagent's transcript actually contains the
   injection.
2. **Wording micro-tests** — before full scenarios, per writing-skills:
   the worktree/commit/sudo conditionals against a no-guidance control,
   5+ reps, flagged matches read manually.
3. **Skill RED/GREEN** — pressure scenarios: baseline agent in a scratch
   repo under time/sunk-cost pressure (does it commit? push? script the
   install? probe the worktree-rule boundary), then with the skill
   injected; capture rationalizations verbatim and replace §5.7's
   provisional table with them. Verify whether subagents receive the
   injection.
4. **Template test** — copy template, add a step, run twice: second run
   changes nothing; `--check` with a missing step reports every step and
   exits nonzero without mutating.

## 8. Out of scope

* Removing AGENT-ENVIRONMENT.md from existing prison repos, and migrating
  its fork-topology fact into per-repo `CLAUDE.md` — per-repo cleanup once
  the plugin is in the VM image.
* VM image / install automation for the plugin (and its superpowers
  prerequisite) — still the separate deliverable it was in the chute-push
  spec.
* Chute usage, signing keys, or any project-specific instruction — other
  skills / per-repo CLAUDE.md.
