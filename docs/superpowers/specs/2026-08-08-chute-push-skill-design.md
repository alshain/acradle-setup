# chute-push skill — design

Date: 2026-08-08
Status: approved design; implementation first, testing deferred (see §7)

## 1. Purpose

acradle runs Claude Code inside disposable, network-isolated Multipass VMs.
Agents there produce artifacts — APKs, screenshots, documents, reports — and
the person who needs them is on a phone. chute (`alshains-prison/alshain-chute`)
closes that gap: one authenticated HTTP upload, an FCM push, install/preview on
the phone.

This repo (acradle-setup) develops the deliverables installed into those VMs.
The first is a Claude skill that teaches an in-VM agent to push artifacts
through chute, packaged as a Claude Code plugin so VMs install it with two
`claude plugin` commands.

## 2. Decisions

| Decision | Choice |
| --- | --- |
| Skill shape | SKILL.md for judgment + bundled `chute-push` bash script for mechanics (approach A; doc-only and standalone-CLI rejected) |
| Server URL | Hardcoded default `https://chute.vqrs.ch`, overridable via `$CHUTE_URL` (override exists for testing against a local `chuted`) |
| Packaging | Claude Code plugin `acradle-vm` in marketplace `acradle-setup`, same repo, relative-path source |
| Skill name | `pushing-artifacts-to-phone` — verb-first, discoverable by intent words, not the chute codename |
| Testing | Deferred by decision, not skipped — §7 |
| VM install automation | Out of scope; next deliverable |

## 3. Repository layout

```
.claude-plugin/
  marketplace.json              # marketplace "acradle-setup"; plugins referenced by relative path
plugins/
  acradle-vm/
    .claude-plugin/plugin.json  # plugin "acradle-vm"
    skills/
      pushing-artifacts-to-phone/
        SKILL.md
        chute-push              # executable bash
docs/superpowers/specs/
```

Skills surface as `acradle-vm:pushing-artifacts-to-phone`. Validate manifests
with `claude plugin validate` during implementation. In-VM install (mechanism
automated later): `claude plugin marketplace add <repo>` then
`claude plugin install acradle-vm@acradle-setup`.

## 4. SKILL.md

Short (target < 300 words; the judgment lives here, the mechanics in the
script). Contents:

* **Frontmatter description** — triggers only, no workflow summary. Covers:
  built artifact the user needs, "send/push X to me/the user/my phone", task
  output is a file rather than a commit. Keyword coverage across frontmatter
  and body: artifact, **artefact**, APK, screenshot, image, **document**,
  **specification**, report, binary, **send**, **user**, phone, install,
  preview, chute.
* What chute is, in two sentences.
* The `chute-push` one-liner.
* Stream table: `prod-apk`, `test-apk`, `screenshots`, docs — plus the rule:
  new channel → new stable stream name; PR/experiment builds never go to
  `prod-apk`.
* First-push behavior: phone approval tap, code valid 24 h. **The agent MUST
  relay the pairing code to the user verbatim** (in its user-visible output,
  not buried in tool logs) — the code is how the user verifies on the phone
  that the approval card they're tapping belongs to this agent's request and
  not someone else's pairing attempt.
* Compact manual-API fallback: the four raw curl calls (pair, poll,
  upload, signing-key) for cases the script doesn't cover.

## 5. `chute-push`

```
chute-push --stream <name> <file> [--version NAME] [--notes TEXT]
```

* `--stream` required — the agent chooses the channel deliberately; no default.
* **No `--project` flag.** The project name is only needed to obtain a token,
  so the script derives it lazily — only when no token exists yet (or a 401
  forces re-pairing). A pushed artifact costs zero name lookups in the common
  already-paired case.

**Project name normalization (pairing path only).** Prison repos are named
`alshains-prison/<upstream-owner>-<name>` (e.g. `alshain/abc` →
`alshains-prison/alshain-abc`). The proposed project name is the upstream
identity, not the prison name:

1. **Fork-parent lookup (primary).** If `origin` is a GitHub repo, query
   `GET /repos/{owner}/{repo}` with the token already in git's credential
   store (`git credential fill`; the App token has metadata:read) and take
   `parent.full_name`. Own repos (`parent.owner == alshain`) → bare name
   (`abc`); third-party → `owner/name` (`jetbrains/intellij-community`).
   Slashes are safe: chute project names are free-form display strings —
   uploads are token-scoped, and phone routes address projects by numeric id.
2. **String fallback** (seeded prisons have no fork parent; lookup may fail).
   For `alshains-prison/<n>`: strip a leading `alshain-` → bare name;
   otherwise split at the first hyphen → `owner/name`. Ambiguity for
   hyphenated owners is accepted — the fork lookup is the primary path, and
   the phone can rebind at approval time anyway.
3. **Not a prison repo:** `owner == alshain` → bare name; other owner →
   `owner/name`; no usable remote → directory basename.

Constants `alshains-prison` (prison org) and `alshain` (self owner) are
hardcoded next to `CHUTE_URL`, overridable via `$CHUTE_PRISON_ORG` /
`$CHUTE_SELF_OWNER`.
* `branch`/`commit` auto-derived from git (`--abbrev-ref HEAD`, `--short
  HEAD`), omitted silently outside a repo. `filename` = file basename.
* `CHUTE_URL="${CHUTE_URL:-https://chute.vqrs.ch}"`.
* Dependencies: bash, curl, git only. No `jq`; parse with grep/sed.

**Token flow.** Token at `~/.config/chute/token` (chute spec convention);
token presence is the entire client-side state. Pairing is triggered by: no
token, or 401 on upload. Pairing = `POST /v1/pair {proposed_name}` → print
the 4-digit code loudly with the expiry (24 h) → poll `GET /v1/pair/{code}`
with the poll secret at ≥ 10 s intervals, backing off on 429 (shared pairing
IP bucket) → on 200, write token `chmod 600` and proceed with the upload.
The poll secret is never echoed. Denied (403) → clear exit; expired (410) →
"re-run to request a new code".

**Pending-pairing persistence.** Codes live 24 h, so a pairing outlasts any
single invocation. The script persists `{code, poll_secret}` to
`~/.config/chute/pending-pairing` (`chmod 600`) when it creates one, bounds
each invocation's wait (default 5 min, `--wait SECONDS` to override), and on
timeout exits nonzero: "pairing still pending — approve on the phone, then
re-run". A later invocation finds the file and resumes polling the same code
instead of minting a new one (no duplicate approval cards on the phone). The
file is deleted on collection (200), denial (403), or expiry (410).

**Upload.** `curl -T` to `PUT /v1/streams/{stream}/versions` — token-scoped,
no project in the path. The authoritative API contract is chute's
`docs/superpowers/specs/2026-08-07-chute-api-changes.md` (frozen; it
supersedes the routes in the 2026-08-06 design doc and names this agent
skill as one of its three downstream tracks). Provenance as query params.
201 → report version id, sha256, size. 200 → report "already exists
(idempotent replay)" as success. Distinct actionable messages for 413 (over
the 512 MB cap), connection failure, and non-JSON responses.

## 6. Error handling summary

| Condition | Behavior |
| --- | --- |
| Missing/unreadable file | Immediate error before any network call |
| No token / 401 | Derive project name, pair, then retry upload once |
| Pairing denied | Exit nonzero: "denied on phone" |
| Pairing expired | Exit nonzero: "re-run for a new code" |
| 413 | Exit nonzero: file exceeds server cap |
| Server unreachable | Exit nonzero, names the URL tried |
| Same sha256 replay | Success (200), labelled as replay |

## 7. Testing (deferred by decision)

Implementation proceeds first at the user's direction. Before the plugin is
installed into real VMs, the following must run:

1. **Script harness** — local `chuted` (temp `CHUTE_DATA_DIR`), bootstrap →
   device token, then drive `chute-push` through: fresh pairing, 201 upload,
   200 replay, denial, missing file, unreachable server. Modeled on chute's
   `scripts/smoke.sh`. Expiry path gets code review, not a wall-clock test.
2. **Skill RED/GREEN** — subagent + scratch project + local chuted, "get this
   APK onto the user's phone": baseline without the skill (document failure),
   then with it (verify discovery, stream choice, pairing patience). Close
   wording gaps, retest.
3. **By-hand** — one real push to `https://chute.vqrs.ch`, phone tap,
   notification lands, artifact opens.

## 8. Out of scope

* VM install automation (cloud-init / setup-vm.sh wiring) — next deliverable.
* Android signing-key skill (`GET /v1/projects/{p}/signing-key`) — later skill;
  the manual-API section mentions the endpoint, nothing more.
* VM survival conventions skill (AGENT-ENVIRONMENT.md content) — separate
  brainstorm.
