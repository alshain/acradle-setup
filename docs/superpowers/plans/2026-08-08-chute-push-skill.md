# chute-push Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `pushing-artifacts-to-phone` skill: a SKILL.md plus a `chute-push` bash script that pairs with chute and uploads artifacts, per `docs/superpowers/specs/2026-08-08-chute-push-skill-design.md`.

**Architecture:** Script owns mechanics (token, pairing with resume, upload, errors); SKILL.md owns judgment (stream choice, code-relay rule) plus a manual-API fallback. Lives in the already-committed `plugins/acradle-vm` plugin.

**Tech Stack:** bash + curl + git only. No jq (VM base image lacks it).

## Global Constraints

- Server URL default: `https://chute.vqrs.ch`, env-overridable via `$CHUTE_URL`.
- Constants `alshains-prison` / `alshain` overridable via `$CHUTE_PRISON_ORG` / `$CHUTE_SELF_OWNER`.
- Wire contract (verified against chute's `internal/api/{pair,upload,router}.go`): pair body `{"proposed_name":...}` → 201 `{code, poll_secret, expires_at}`; poll auth header `X-Chute-Poll-Secret`; poll responses 202 pending / 200 `{state,token}` / 403 denied / 404+410 gone / 401 bad secret / 429 rate-limited; upload `PUT /v1/streams/{s}/versions?filename=&branch=&commit=&version=&notes=` with `Authorization: Bearer`, 201 new / 200 replay / 413 too large; `filename` must be a basename (server rejects paths).
- Poll ≥10 s; back off on 429. Codes valid 24 h. Pending pairing persisted and resumed.
- Secrets (`poll_secret`, token) are never echoed to stdout/stderr.
- Testing beyond the checks below is deferred by decision (spec §7) — do NOT build the smoke harness or run RED/GREEN skill tests in this plan.

---

### Task 1: `chute-push` script

**Files:**
- Create: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push`

**Interfaces:**
- Produces: `chute-push --stream <name> [--version NAME] [--notes TEXT] [--wait SECONDS] <file>` — exit 0 success, 2 usage, 1 everything else. Task 2's SKILL.md documents exactly these flags.

- [ ] **Step 1: Write the script** with exactly this content:

```bash
#!/usr/bin/env bash
# chute-push — send an artifact to the user's phone via chute.
#
#   chute-push --stream <name> [--version NAME] [--notes TEXT] [--wait SECONDS] <file>
#
# Pairs on first use (phone approval; code printed loudly and persisted so a
# re-run resumes the same code). Token: ~/.config/chute/token. Exit codes:
# 0 success, 2 usage, 1 anything else.
set -euo pipefail

CHUTE_URL="${CHUTE_URL:-https://chute.vqrs.ch}"
CHUTE_PRISON_ORG="${CHUTE_PRISON_ORG:-alshains-prison}"
CHUTE_SELF_OWNER="${CHUTE_SELF_OWNER:-alshain}"
CONFIG_DIR="${HOME}/.config/chute"
TOKEN_FILE="${CONFIG_DIR}/token"
PENDING_FILE="${CONFIG_DIR}/pending-pairing"
POLL_INTERVAL=10

STREAM="" VERSION="" NOTES="" FILE="" WAIT=300

die() { echo "chute-push: $*" >&2; exit 1; }

usage() {
  echo "usage: chute-push --stream <name> [--version NAME] [--notes TEXT] [--wait SECONDS] <file>" >&2
  exit 2
}

need_val() { [ "$#" -ge 2 ] || die "flag $1 needs a value"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --stream)  need_val "$@"; STREAM="$2";  shift 2 ;;
    --version) need_val "$@"; VERSION="$2"; shift 2 ;;
    --notes)   need_val "$@"; NOTES="$2";   shift 2 ;;
    --wait)    need_val "$@"; WAIT="$2";    shift 2 ;;
    -h|--help) usage ;;
    -*)        die "unknown flag: $1" ;;
    *)         [ -z "$FILE" ] || usage; FILE="$1"; shift ;;
  esac
done

[ -n "$STREAM" ] || usage
[ -n "$FILE" ] || usage
[ -r "$FILE" ] || die "cannot read file: $FILE"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Percent-encode a string byte-wise (LC_ALL=C makes ${s:i:1} a byte, so
# UTF-8 comes out as %XX%XX... — required for query parameters like notes).
urlencode() {
  local LC_ALL=C s="$1" out="" c i
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      *) out+="$(printf '%%%02X' "'$c")" ;;
    esac
  done
  printf '%s' "$out"
}

json_escape() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "$s"; }

# First string value for a key in compact Go-marshaled JSON: "key":"value".
json_field() {
  printf '%s' "$1" | grep -o "\"$2\":\"[^\"]*\"" | head -n1 | sed 's/^"[^"]*":"//; s/"$//'
}

# GitHub fork-parent lookup using the credential already in git's store.
# Prints "owner/name" of the parent, or nothing (rc 1) when unavailable.
fork_parent() {
  local cred pass resp
  cred="$(printf 'protocol=https\nhost=github.com\n\n' \
    | GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=true git credential fill 2>/dev/null)" || return 1
  pass="$(printf '%s\n' "$cred" | sed -n 's/^password=//p' | head -n1)"
  [ -n "$pass" ] || return 1
  resp="$(curl -fsS --max-time 10 -H "Authorization: Bearer $pass" \
    "https://api.github.com/repos/$1/$2")" || return 1
  # parent's full_name precedes any nested object inside "parent":{...}
  printf '%s' "$resp" \
    | grep -o '"parent":{[^{]*"full_name":"[^"]*"' \
    | grep -o '"full_name":"[^"]*"' | head -n1 \
    | sed 's/^"full_name":"//; s/"$//'
}

# Proposed project name (pairing only — spec: lazy, zero lookups when a
# token already exists). Prison repos normalize to their upstream identity.
derive_project() {
  local url path owner name parent
  url="$(git remote get-url origin 2>/dev/null || true)"
  if [ -z "$url" ]; then basename "$PWD"; return; fi
  path="${url%.git}"
  case "$path" in
    git@*:*)    path="${path#*:}" ;;
    ssh://*)    path="${path#ssh://}"; path="${path#*/}" ;;
    http://*|https://*) path="${path#*://}"; path="${path#*/}" ;;
  esac
  owner="${path%%/*}"; name="${path#*/}"
  if [ -z "$owner" ] || [ -z "$name" ] || [ "$owner" = "$path" ]; then
    basename "$PWD"; return
  fi
  if [ "$owner" = "$CHUTE_PRISON_ORG" ]; then
    parent="$(fork_parent "$owner" "$name" || true)"
    if [ -n "$parent" ]; then
      owner="${parent%%/*}"; name="${parent#*/}"
    else
      # Seeded prisons aren't forks; fall back to name parsing.
      case "$name" in
        "$CHUTE_SELF_OWNER"-*) printf '%s\n' "${name#"$CHUTE_SELF_OWNER"-}"; return ;;
        *-*)                   printf '%s/%s\n' "${name%%-*}" "${name#*-}"; return ;;
        *)                     printf '%s\n' "$name"; return ;;
      esac
    fi
  fi
  if [ "$owner" = "$CHUTE_SELF_OWNER" ]; then
    printf '%s\n' "$name"
  else
    printf '%s/%s\n' "$owner" "$name"
  fi
}

pair() {
  local code secret resp http project waited tok
  mkdir -p "$CONFIG_DIR"; chmod 700 "$CONFIG_DIR"
  if [ -s "$PENDING_FILE" ]; then
    code="$(sed -n '1p' "$PENDING_FILE")"
    secret="$(sed -n '2p' "$PENDING_FILE")"
    echo "chute-push: resuming pending pairing"
  else
    project="$(derive_project)"
    echo "chute-push: requesting pairing for project '$project'"
    http="$(curl -sS --max-time 15 -o "$TMP" -w '%{http_code}' \
      -X POST -H 'Content-Type: application/json' \
      -d "{\"proposed_name\":\"$(json_escape "$project")\"}" \
      "$CHUTE_URL/v1/pair")" || die "cannot reach chute at $CHUTE_URL"
    [ "$http" = "201" ] || die "pairing request failed (HTTP $http): $(cat "$TMP")"
    resp="$(cat "$TMP")"
    code="$(json_field "$resp" code)"
    secret="$(json_field "$resp" poll_secret)"
    { [ -n "$code" ] && [ -n "$secret" ]; } || die "unexpected pairing response"
    ( umask 077; printf '%s\n%s\n' "$code" "$secret" > "$PENDING_FILE" )
  fi

  echo ""
  echo "==================================================================="
  echo "  PAIRING CODE: $code   (valid 24h)"
  echo "  Approve on the phone — only tap the card showing THIS code."
  echo "  AGENT: relay this code to the user in your reply, verbatim."
  echo "==================================================================="
  echo ""

  waited=0
  while [ "$waited" -lt "$WAIT" ]; do
    http="$(curl -sS --max-time 15 -o "$TMP" -w '%{http_code}' \
      -H "X-Chute-Poll-Secret: $secret" "$CHUTE_URL/v1/pair/$code")" || http=000
    case "$http" in
      200)
        tok="$(json_field "$(cat "$TMP")" token)"
        [ -n "$tok" ] || die "approved but no token in response"
        ( umask 077; printf '%s\n' "$tok" > "$TOKEN_FILE" )
        rm -f "$PENDING_FILE"
        echo "chute-push: paired."
        return 0 ;;
      202) : ;;
      401) die "poll secret rejected — remove $PENDING_FILE and re-run" ;;
      403) rm -f "$PENDING_FILE"; die "pairing denied on the phone" ;;
      404|410) rm -f "$PENDING_FILE"; die "pairing expired or already used — re-run to request a new code" ;;
      429) sleep 20; waited=$((waited + 20)) ;;
      000) : ;;
      *) die "unexpected response while polling (HTTP $http): $(cat "$TMP")" ;;
    esac
    sleep "$POLL_INTERVAL"; waited=$((waited + POLL_INTERVAL))
  done
  die "pairing still pending after ${WAIT}s — approve on the phone, then re-run (same code resumes)"
}

# Uploads $FILE. Returns 0 on success, 41 on auth rejection (caller
# re-pairs once); dies on every other failure.
upload() {
  local token qs http branch commit
  token="$(cat "$TOKEN_FILE")"
  qs="filename=$(urlencode "$(basename "$FILE")")"
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  commit="$(git rev-parse --short HEAD 2>/dev/null || true)"
  if [ -n "$branch" ]; then qs="${qs}&branch=$(urlencode "$branch")"; fi
  if [ -n "$commit" ]; then qs="${qs}&commit=$(urlencode "$commit")"; fi
  if [ -n "$VERSION" ]; then qs="${qs}&version=$(urlencode "$VERSION")"; fi
  if [ -n "$NOTES" ]; then qs="${qs}&notes=$(urlencode "$NOTES")"; fi
  http="$(curl -sS -o "$TMP" -w '%{http_code}' \
    -T "$FILE" -H "Authorization: Bearer $token" \
    "$CHUTE_URL/v1/streams/$(urlencode "$STREAM")/versions?$qs")" \
    || die "upload failed: cannot reach $CHUTE_URL"
  case "$http" in
    201) echo "chute-push: uploaded '$(basename "$FILE")' to stream '$STREAM': $(cat "$TMP")" ;;
    200) echo "chute-push: already on the server (idempotent replay): $(cat "$TMP")" ;;
    401) return 41 ;;
    413) die "file exceeds the server's upload cap" ;;
    *)   die "upload failed (HTTP $http): $(cat "$TMP")" ;;
  esac
}

[ -s "$TOKEN_FILE" ] || pair

rc=0
upload || rc=$?
if [ "$rc" = "41" ]; then
  echo "chute-push: token rejected (revoked or stale) — re-pairing"
  rm -f "$TOKEN_FILE"
  pair
  upload
elif [ "$rc" != "0" ]; then
  exit "$rc"
fi
```

- [ ] **Step 2: Syntax check**

Run: `bash -n plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push`
Expected: no output, exit 0.

- [ ] **Step 3: Behavior spot-checks (no server needed)**

```bash
bash plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push; echo "rc=$?"
# Expected: usage line on stderr, rc=2
bash plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push --stream x /nonexistent; echo "rc=$?"
# Expected: "cannot read file", rc=1
bash plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push --stream x --notes; echo "rc=$?"
# Expected: "flag --notes needs a value", rc=1
```

- [ ] **Step 4: Mark executable and commit**

```bash
git add plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push
git update-index --chmod=+x plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push
git commit -m "Add chute-push script: pair-with-resume + upload"
```

---

### Task 2: SKILL.md

**Files:**
- Create: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/SKILL.md`

**Interfaces:**
- Consumes: `chute-push` flags exactly as Task 1 defines them.

- [ ] **Step 1: Write SKILL.md** with exactly this content:

```markdown
---
name: pushing-artifacts-to-phone
description: Use when a build artifact or file — an APK, image, screenshot, document, specification, report, or binary — needs to reach the user, or when the user asks to send, push, or deliver a file or artefact to them or their phone.
---

# Pushing Artifacts to the User's Phone (chute)

chute is the asset-to-phone pipeline: one authenticated upload, a push
notification on the user's phone, install/preview there. The `chute-push`
script in this skill's directory does everything, pairing included:

```bash
bash "<this skill's directory>/chute-push" --stream prod-apk app-release.apk
```

Flags: `--stream <name>` (required), `--version <label>`, `--notes <text>`,
`--wait <seconds>` (pairing wait, default 300). Git branch + commit
provenance attaches automatically.

## Choosing a stream

| Stream | Use for |
|---|---|
| `prod-apk` | Release/main builds of the project's app |
| `test-apk` | PR, experiment, and debug builds — never `prod-apk` |
| `screenshots` | Images the user should look at |
| `docs` | Documents, specifications, reports |

New kind of artifact → pick a new stable stream name and keep using it.
Streams are created on first upload.

## First push: pairing (one phone tap)

With no token (`~/.config/chute/token`) the script requests pairing,
prints a 4-digit code, and waits for phone approval.

**Relay the pairing code to the user verbatim in your reply** — the user
must approve only the phone card showing that exact code. A timed-out wait
is not a failure: the code stays valid 24 h and a re-run resumes it.

## Manual API (when the script can't)

Base `$CHUTE_URL` (default `https://chute.vqrs.ch`); token file above,
sent as `Authorization: Bearer`.

```
POST /v1/pair                 {"proposed_name":"<project>"} → {code, poll_secret, expires_at}
GET  /v1/pair/{code}          header X-Chute-Poll-Secret: <poll_secret>
                              202 pending | 200 {token} | 403 denied | 410 expired
PUT  /v1/streams/{s}/versions?filename=<basename>&branch=&commit=&version=&notes=
                              curl -T <file>; 201 new | 200 same bytes already exist
GET  /v1/signing-key          needs 'sign' capability; PKCS#12 keystore for Android updates
```
```

- [ ] **Step 2: Sanity checks**

```bash
head -5 plugins/acradle-vm/skills/pushing-artifacts-to-phone/SKILL.md
# Expected: frontmatter opens with name: pushing-artifacts-to-phone
grep -c "chute" plugins/acradle-vm/skills/pushing-artifacts-to-phone/SKILL.md
# Expected: >= 5 (keyword coverage)
```

- [ ] **Step 3: Commit**

```bash
git add plugins/acradle-vm/skills/pushing-artifacts-to-phone/SKILL.md
git commit -m "Add pushing-artifacts-to-phone SKILL.md"
```
