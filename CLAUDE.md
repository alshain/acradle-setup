# CLAUDE.md

## What this repo is

acradle-setup develops the deliverables — Claude skills, helper scripts, plugins — that
get installed into the disposable VMs that acradle creates. Nothing here runs on this
machine in production; everything targets an agent running inside one of those VMs.

## Related projects

**acradle** — sibling checkout at `../acradle`. A Go tool that runs Claude Code inside
disposable, network-isolated Multipass VMs working on throwaway "prison" copies of repos.
Cloud-init provisions each VM fresh (git, Node, Claude Code); the agent runs headless
`claude -p "<task>"`; commits auto-push to `acradle/<run-id>` in the prison repo; the VM
is destroyed at the end.

**chute** — GitHub `alshains-prison/alshain-chute`, **no local checkout**. Read its docs
via `gh api repos/alshains-prison/alshain-chute/contents/<path> --jq '.content' | base64 -d`
(README, `AGENT-ENVIRONMENT.md`, FSD at `docs/superpowers/specs/2026-08-06-chute-design.md`).
It is the asset-to-phone pipeline: an in-VM agent uploads an artifact with one
authenticated HTTP call, an FCM push lands on the user's phone, install/preview from
there. Production server: `https://chute.vqrs.ch`.

Chute facts that shape everything here:

- **Pairing, not pre-placed secrets** (VMs are disposable): `POST /v1/pair {project}` →
  4-digit code + poll secret; the user approves on the phone; the agent polls
  `GET /v1/pair/{code}` until it receives a `chu_…` token, written to
  `~/.config/chute/token`. Pairings are single-use and expire in 10 minutes; there is
  deliberately no auto-approval.
- **Upload is one curl**: `curl -T file "$CHUTE_URL/v1/projects/{p}/streams/{s}/versions?filename=…&branch=…&commit=…"`
  with the bearer token. Streams are created implicitly on first upload; uploads are
  idempotent by SHA-256 (201 new, 200 replay); 512 MB cap.
- **Per-project Android signing keys**: `GET /v1/projects/{p}/signing-key`, gated behind
  a default-off `sign` capability. Tokens are scoped to exactly one project.

## Target environment (design constraints for every deliverable)

- Ubuntu Multipass guest, provisioned fresh per run — no state survives between runs.
- Headless `claude -p`: no human available to answer prompts mid-run.
- Internet access, but no LAN/host access.
- Scripts must need only **bash + curl + git** — no `jq` (parse with grep/sed).
  **One stated exception** (sets spec D1, `docs/superpowers/specs/2026-10-06-chute-artifact-sets-skill-design.md`):
  `chute-push --set` additionally needs python3 ≥ 3.10, **stdlib only** (zipfile, json),
  via the skill's `chute-set.py`; acradle provisioning already requires python3.
  Single-file pushes never need it, and a VM without python3 loses sets with a clear
  message, never single-file pushes. No other script gets python3 without its own spec decision.

## Repository layout

```
.claude-plugin/marketplace.json     # marketplace "acradle-setup"; plugins by relative path
plugins/acradle-vm/
  .claude-plugin/plugin.json        # plugin "acradle-vm"
  skills/pushing-artifacts-to-phone/
    SKILL.md                        # judgment: when to push, which stream
    chute-push                      # executable bash: pairing + upload mechanics
docs/superpowers/specs/             # committed design specs (YYYY-MM-DD-<topic>-design.md)
```

Skills surface as `acradle-vm:pushing-artifacts-to-phone`. Validate manifests with
`claude plugin validate`. VMs install with `claude plugin marketplace add <repo>` then
`claude plugin install acradle-vm@acradle-setup`.

## Conventions

- Superpowers workflow: brainstorm → spec (committed to `docs/superpowers/specs/`) →
  plan → implement. Superpowers is installed at user scope.
- Skill names are verb-first and discoverable by intent words, never by project codename
  (`pushing-artifacts-to-phone`, not `chute-upload`). Trigger keywords must include:
  artifact, **artefact** (British spelling), APK, screenshot, image, document,
  specification, report, binary, send, user, phone, install, preview, chute.
- Server URLs: hardcode the production default, allow an env override
  (`CHUTE_URL="${CHUTE_URL:-https://chute.vqrs.ch}"`) so tests can point at a local
  `chuted`.

## Status (2026-08-08)

- Approved design spec: `docs/superpowers/specs/2026-08-08-chute-push-skill-design.md`.
  Next step: implementation plan, then build the plugin/skill/script.
- **Testing is deferred by decision, not skipped** — spec §7 (script harness against a
  local `chuted`, skill RED/GREEN with a subagent, one by-hand push to the real server)
  must run before the plugin is installed into real VMs.
- Out of scope for now: VM install automation (next deliverable), Android signing-key
  skill, VM survival-conventions skill.
