# Skill-suggestion pipeline (MCP) — ideas

Date: 2026-08-08
Status: brainstorm / ideas only — not an approved design. Open questions in §9.

## 1. Purpose

Agents running inside acradle VMs use the skills developed in this repo
(acradle-setup). They are also the first to notice when a skill is wrong,
stale, or missing a case — a curl example that 404s, a stream name that
changed, a workflow gap. Today that observation dies with the VM.

Idea: give the in-VM agent a way to *propose* a skill improvement. The agent
tells a server which files it wants to replace (or add/delete) and why; the
server applies the changes to a branch of acradle-setup; the owner reviews
and merges. Merged improvements reach every future VM through the normal
skill-install path. Agents suggest, only the owner merges — the agent never
holds write access to this repo's main branch, and nothing an agent writes
takes effect without human review.

## 2. Constraints from the acradle architecture

Facts that bound the design (file refs are into the sibling `acradle` repo):

* **VMs cannot reach the orchestrator.** It binds loopback-only and its API
  is unauthenticated (`cmd/acradle/main.go:179`). Any agent-facing endpoint
  must live elsewhere.
* **The worker already exposes an agent surface VMs can reach**: the
  port-forward API on `:8444` — TLS with a private CA dropped into the VM
  (`~/.acradle/ca.pem`), a per-run bearer token (`~/.acradle/portforward-token`),
  a wrapper script, and an embedded SKILL.md
  (`internal/worker/agentsurface.go`). The worker does no authorization; it
  relays the bearer token over its outbound WebSocket and the orchestrator
  resolves token → (run, VM) (`internal/orchestrator/portforward_event.go`).
* **A `/mcp/<service>` path prefix on that surface is explicitly reserved**
  for future services (`docs/superpowers/specs/2026-08-07-browser-port-forward-design.md`),
  and `docs/phase-2.md` already sketches an "MCP escalation broker — narrow
  verbs only: `propose`, …". This feature *is* the `propose` verb.
* **GitHub App choke point.** `MintWrite` mints single-repo tokens and
  refuses anything outside the prison org
  (`internal/orchestrator/tokens.go:41-43`). The in-VM credential file and
  `credential.useHttpPath` already support multiple repos per VM
  (`internal/worker/plan.go:302-312`). The owner installation is used only
  for metadata reads today — it never mints a write token.
* **Skill installation is not built yet.** acradle embeds exactly one skill
  via `go:embed`; installing the `acradle-vm` plugin from acradle-setup is a
  planned next deliverable. The suggestion pipeline only pays off once
  installs actually pull from this repo — the two deliverables compound.
* **Per-run provenance exists** (`ACRADLE_RUN_ID`, task text, prison remote)
  but only in headless runs; the forward-token pattern gives server-attested
  run identity regardless, which is the stronger primitive.

## 3. Core flow (common to every variant)

1. Agent hits a snag with an installed skill and fixes its *local copy*
   (skills are plain files inside the VM), or drafts a new file.
2. Agent submits a proposal: title, rationale, and the full new content of
   each changed file (plus explicit deletes). Full-file replace, not diffs —
   the agent has the whole file locally, and full content is idempotent and
   trivially validated. The server computes the diff for review.
3. Server validates (path allowlist, size caps, text-only — §7), applies the
   files to a clean checkout of acradle-setup, commits with provenance
   (§8), and pushes branch `suggest/<run-id>-<slug>`.
4. Owner reviews the diff (GitHub PR and/or dashboard link) and merges,
   edits, or discards. No auto-merge, ever.
5. Next VM provisioning picks up the merged skill.

## 4. Placement — three approaches

### A. Orchestrator-backed service on the worker agent surface (recommended)

Extend the existing pattern: a new endpoint on the worker's `:8444` listener
(`/api/suggest` or the reserved `/mcp/skill-feedback`), authenticated with a
per-run bearer token exactly like port-forward. The worker relays to the
orchestrator over the existing WebSocket; the orchestrator validates,
maintains its own clone of acradle-setup (under its data dir — never the
owner's working copy), commits, and pushes the branch. "Do that locally" in
the best sense: the git work happens on the host, where the App key already
lives, and works even when GitHub is unreachable (branch waits in the local
clone; push retries later).

* Pros: reuses every existing mechanism (agent surface, token→run
  resolution, App-token choke point, SQLite, dashboard); provenance is
  server-attested, not agent-claimed; no new public infrastructure; the
  orchestrator can list suggestions in the dashboard next to runs.
* Cons: it's Go work in the acradle repo, not a deliverable of this one;
  needs a decision on how the orchestrator gets write access to
  acradle-setup (owner-install App token with `contents:write`, or a plain
  deploy key / PAT for just this repo).

### B. Standalone public suggestion server (chute-style)

A small public HTTPS service — or new routes on `chuted`, which already
runs, is public, and solved no-secrets-in-VM pairing and phone
notifications. Agent pairs (or reuses its chute project token), POSTs the
proposal bundle; the server pushes the branch/PR with its own GitHub
credential and can send an FCM push: "agent suggests a skill change".

* Pros: zero acradle changes; works from any VM or even outside acradle;
  chute's pairing/token/notification machinery is reusable; could expose a
  streamable-HTTP MCP endpoint directly, no shim in the VM.
* Cons: a public, internet-facing service holding a GitHub write credential
  is a real attack surface; provenance is weaker (chute knows the project,
  not the run); scope creep for chute, which is deliberately an artifact
  pipeline.

### C. Pure git, no server: prison mirror of acradle-setup (cheap v0)

Mirror acradle-setup into the prison org (`alshains-prison/alshain-acradle-setup`).
The orchestrator mints the VM's token for two repos instead of one — the
minting call already takes a repo list (`internal/orchestrator/auth.go:56`)
and the prison-org guard already admits it. A skill (shipped from this repo)
teaches the agent: clone the mirror, apply your change, push
`suggest/<run-id>-<slug>`, never anything else. Owner reviews branches on
the mirror and merges upstream by hand or via a small sync script.

* Pros: nearly zero new code; no server, no MCP, no new endpoint; review is
  ordinary git; validates the whole premise (do agents produce suggestions
  worth merging?) before any infrastructure is built.
* Cons: no validation choke point — a branch can contain anything (still
  branch-only, and prison repos are already treated as untrusted); every
  live VM shares write access to the mirror, so one run can push over
  another's suggestion branch; owner shuttles between mirror and real repo;
  no structured rationale/metadata beyond the commit message.

**Recommendation:** C first as a probe, A as the target. C is roughly a
plan-step change plus a skill, and everything it teaches (proposal quality,
review burden, path conventions) feeds A's design. B only becomes
interesting if suggestions should flow from machines acradle doesn't manage.

## 5. MCP server or plain script?

Within approach A, the agent-facing shape is a separate decision:

* **Bash script + SKILL.md** (`acradle-suggest <title> file...`), the
  `chute-push` / `acradle-forward` house style. No MCP wiring, works in
  `claude -p` unchanged, trivially testable with curl.
* **MCP server**: either a stdio shim in the VM wrapping the same HTTP
  calls, or Claude Code pointed at the worker's endpoint as a remote
  (streamable-HTTP) MCP server — acradle configures no MCP today, so this
  adds provisioning work (`claude mcp add` / `.mcp.json` during setup).
  What MCP buys: typed tool schema (harder to malform a proposal), tool-list
  discoverability without skill-trigger luck, and a natural home if the
  phase-2 broker grows more verbs (`request_repo`, `refresh_token`) — one
  server, several narrow tools.

A defensible line: the *transport and server* should be MCP-shaped from the
start (it's the reserved path, and phase-2 wants a broker there anyway), but
the v1 in-VM client can still be a script hitting it over plain HTTP — MCP
servers are just HTTP endpoints; the `claude mcp add` wiring can come later
without changing the server.

## 6. Tool surface sketch (deliberately minimal)

One tool. YAGNI on sessions, drafts, and multi-call protocols.

```
suggest_skill_change {
  title:      string          # one line, becomes branch slug + PR title
  rationale:  string          # what failed / what was missing, in the agent's words
  base:       string          # commit/version of the installed skills, for staleness detection
  files: [ { path: string, content: string | null } ]   # null = delete
}
→ { branch, pr_url?, status: accepted | rejected(reason) }
```

Replay-safe: same run + same payload hash → same branch, updated in place
(one suggestion branch per run, force-updated on resubmit — mirrors chute's
idempotent-upload behavior). Maybe later: `get_current_skill(path)` so an
agent can diff against upstream HEAD instead of its installed copy — only if
staleness turns out to matter in practice.

## 7. Trust model and validation

The sharp edge: **skill content is instructions executed by future agents.**
A malicious or prompt-injected proposal is a persistence vector — a way for
one throwaway VM to influence every later run. That's why "owner merges" is
a load-bearing requirement, not a nicety, and why the server validates
before anything reaches a branch:

* Path allowlist: `plugins/**/skills/**` (and maybe `docs/`) only. Never
  `.github/`, never `.claude-plugin/` manifests, never anything executable
  by CI. Reject traversal, absolute paths, symlink-shaped tricks; write via
  git plumbing into a clean index, not by touching a live working tree.
* Caps: max files per proposal, max bytes per file, UTF-8 text only.
* Rate limit: one open suggestion per run; total open-suggestion cap.
* Review is of *rendered diffs*; nothing from a proposal is ever executed
  host-side. Suggestion branches must not trigger CI with secrets.
* The reviewer treats proposals like an unknown contributor's PR — because
  that is exactly what they are, written by something that read untrusted
  repo content all day.

## 8. Provenance and review ergonomics

Each suggestion commit/PR carries: run id, task text (headless), prison
repo, plugin version installed in the VM (`base`), and the agent's
rationale verbatim. Commit author something like
`acradle agent <run-id> <noreply@…>` so history is honest about origin.

Owner-side, the review loop should be one click: dashboard (approach A)
or phone push (approach B) links straight to the PR diff. Merging stays on
GitHub — a dashboard "merge" button is YAGNI when the PR UI already exists.

## 9. Open questions

1. **Which credential pushes to acradle-setup** in approach A: extend the
   owner App installation to `contents:write`, or a single-purpose deploy
   key/PAT held by the orchestrator? (Smallest blast radius likely the
   deploy key.)
2. **Is acradle-setup public or private**, and does it become a prison-org
   mirror anyway under approach C? Affects token choice everywhere.
3. **Parked runs**: suggestions from interactive sessions too, or headless
   only at first? (Token-drop mechanism works for both; headless-only keeps
   v1 smaller.)
4. **Where does the "how to suggest" skill live?** Presumably here, in the
   `acradle-vm` plugin — a meta-skill (`improving-your-skills`) that
   triggers when an installed skill misleads the agent. Needs care so it
   doesn't fire on every minor friction.
5. **PR per suggestion or branch-only?** PRs give review UI and email for
   free; branch-only is quieter. Probably: branch always, PR opt-in per
   suggestion quality once volume is known.
6. **Does the phase-2 escalation broker subsume this?** If the broker lands,
   `propose` should be one of its tools rather than a parallel service —
   worth aligning names/paths now (`/mcp/broker` vs `/mcp/skill-feedback`).

## 10. Suggested staging

1. **v0 (this repo + ~20 lines in acradle):** prison mirror + two-repo
   token + a `suggesting-skill-improvements` skill documenting the branch
   convention. Measure: do useful suggestions appear?
2. **v1 (acradle):** worker-surface endpoint (MCP-shaped per §5, even if
   the client is a script) + orchestrator apply/push + validation from §7,
   branch + draft PR, dashboard list.
3. **v2 (if warranted):** wire the endpoint into Claude Code as a proper
   MCP tool, folded into the phase-2 broker; `get_current_skill`; maybe
   chute-powered phone notification on new suggestions.
