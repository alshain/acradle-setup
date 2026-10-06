#!/usr/bin/env bash
# End-to-end harness for chute-push against a real local chuted (spec §7.1).
#
#   CHUTED_BIN=/path/to/chuted ./tests/test-chute-push.sh
#
# Needs: bash, curl, git, node (JSON assertions), python3 (the set
# scenarios: chute-set.py and tests/make-raw-set.py), and a chuted binary
# (CGO-free; `go build -o chuted ./cmd/chuted` in a chute checkout).
# Starts its own server on 127.0.0.1:18080 with a throwaway data dir; no
# phone, no network beyond loopback. Every step prints ok:; ends with
# "chute-push harness passed".
set -euo pipefail

[ -n "${CHUTED_BIN:-}" ] || { echo "set CHUTED_BIN to a chuted binary" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required (chute-set.py, make-raw-set.py)" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUSH="$ROOT/plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push"
SET_PY="$ROOT/plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-set.py"
MAKE_RAW="$ROOT/tests/make-raw-set.py"
CORPUS="$ROOT/tests/set-corpus"
PORT=18080
BASE="http://127.0.0.1:$PORT"

WORK="$(mktemp -d)"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true   # let chuted release its SQLite files
  fi
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "ok: $*"; }

jget() { node -e "const j=JSON.parse(require('fs').readFileSync(0,'utf8'));const v=$2;if(v==null)process.exit(1);console.log(typeof v==='string'?v:JSON.stringify(v))" <<<"$1"; }

# Isolated agent environment: fresh HOME (token store) and no system/global
# git config, so `git credential fill` finds no helper and returns nothing —
# forcing derive_project's string fallback instead of touching github.com.
AGENT_HOME="$WORK/home"; mkdir -p "$AGENT_HOME"

# chuted's pair limiter (burst 8, then 1 per 10s) is keyed per client IP,
# taken from the last X-Forwarded-For hop or else the socket address. Every
# harness request comes from 127.0.0.1, so by s8 the shared bucket is empty
# and the pair POST gets 429. Real agents each have their own IP; to model
# that, chute-push runs with a curl wrapper first on PATH that stamps every
# request with its own X-Forwarded-For (derived from the wrapper's PID). The
# server and chute-push are unmodified; only the test's network identity is.
REAL_CURL="$(command -v curl)" || fail "curl not on PATH"
CURL_SHIM="$WORK/curlshim"; mkdir -p "$CURL_SHIM"
cat > "$CURL_SHIM/curl" <<EOF
#!/usr/bin/env bash
exec "$REAL_CURL" -H "X-Forwarded-For: 10.\$(( (\$\$ >> 16) & 255 )).\$(( (\$\$ >> 8) & 255 )).\$(( \$\$ & 255 ))" "\$@"
EOF
chmod +x "$CURL_SHIM/curl"

# PUSH_URL overrides the server (an unreachable one proves "nothing was
# sent"); PUSH_BIN runs another copy of chute-push (the tripwire stub skill).
run_push() {  # run_push <cwd> <outfile> [args...]
  local cwd="$1" out="$2"; shift 2
  ( cd "$cwd" && PATH="$CURL_SHIM:$PATH" HOME="$AGENT_HOME" CHUTE_URL="${PUSH_URL:-$BASE}" \
      GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
      bash "${PUSH_BIN:-$PUSH}" "$@" ) > "$out" 2>&1
}

wait_for_line() {  # wait_for_line <file> <grep-pattern> <seconds>
  local i=0
  while [ "$i" -lt "$(( $3 * 2 ))" ]; do
    grep -q "$2" "$1" 2>/dev/null && return 0
    sleep 0.5; i=$((i + 1))
  done
  return 1
}

code_from() { grep 'PAIRING CODE:' "$1" | grep -o '[0-9]\{4\}' | head -n1; }

approve() {  # approve <code> <name>
  curl -fsS -X POST "$BASE/v1/pairings/$1/approve" \
    -H "Authorization: Bearer $DEVICE_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"target\":{\"create\":true},\"name\":\"$2\",\"grant_sign\":false}" >/dev/null
}

deny() {
  curl -fsS -X POST "$BASE/v1/pairings/$1/deny" \
    -H "Authorization: Bearer $DEVICE_TOKEN" >/dev/null
}

# Pairs the agent once (s13; s10 normally pairs first), unless a token is held.
ensure_paired() {  # ensure_paired <dir> <project-name>
  [ -s "$AGENT_HOME/.config/chute/token" ] && return 0
  mkdir -p "$1"; printf 'seed %s\n' "$2" > "$1/seed.txt"
  run_push "$1" "$WORK/pair-$2.out" --stream docs seed.txt &
  local bg=$!
  wait_for_line "$WORK/pair-$2.out" 'PAIRING CODE:' 20 || { cat "$WORK/pair-$2.out"; fail "no banner pairing $2"; }
  approve "$(code_from "$WORK/pair-$2.out")" "$2"
  wait "$bg" || fail "pairing push for $2 failed: $(cat "$WORK/pair-$2.out")"
}

# ── 0. SKILL.md gates (static, before any server) ────────────────────────
# The body is loaded into every session that triggers the skill, so it is
# capped at 500 words (sets spec "SKILL.md changes"). Counting rule: the
# body is everything after the closing `---` of the frontmatter, counted by
# `wc -w`. The frontmatter description is what the model matches a request
# against, so it must carry every trigger word CLAUDE.md mandates.
SKILL_DIR="$ROOT/plugins/acradle-vm/skills/pushing-artifacts-to-phone"
SKILL_MD="$SKILL_DIR/SKILL.md"
skill_front() { awk '/^---$/{n++; next} n==1' "$SKILL_MD"; }
skill_body()  { awk 'n>=2 {print} /^---$/{n++}' "$SKILL_MD"; }
words="$(skill_body | wc -w | tr -d ' ')"
[ "$words" -le 500 ] || fail "SKILL.md body is $words words (gate: 500); move detail to reference.md"
for w in artifact artefact APK screenshot image document specification report \
         binary send user phone install preview chute gallery before/after comparison; do
  skill_front | grep -qi -- "$w" || fail "SKILL.md frontmatter lacks trigger word '$w'"
done
skill_body | grep -q -- '--set' || fail "SKILL.md body does not teach --set"
skill_body | grep -q 'Gallery' || fail "SKILL.md body does not name the Gallery link"
skill_body | grep -q '0\.1\.17' || fail "SKILL.md body does not state the Gallery floor (chute 0.1.17+)"
skill_body | grep -q '(reference\.md)' || fail "SKILL.md does not link reference.md"
grep -q 'PUT  */v1/streams/{s}/versions' "$SKILL_DIR/reference.md" 2>/dev/null \
  || fail "reference.md lacks the manual upload route"
ok "SKILL.md gates: body $words/500 words, trigger words, --set, Gallery floor, reference.md"

# ── server ────────────────────────────────────────────────────────────────
export CHUTE_DATA_DIR="$WORK/data"
CHUTE_ADDR="127.0.0.1:$PORT" CHUTE_PUBLIC_URL="$BASE" \
  "$CHUTED_BIN" > "$WORK/chuted.log" 2>&1 &
SERVER_PID=$!

wait_for_line "$WORK/chuted.log" 'bootstrap available' 15 || fail "chuted did not start: $(tail -3 "$WORK/chuted.log")"
curl -fsS "$BASE/healthz" >/dev/null || fail "healthz"
ok "chuted up"

BOOT_CODE="$(grep -o 'code=[a-z0-9]*' "$WORK/chuted.log" | head -n1 | cut -d= -f2)"
[ -n "$BOOT_CODE" ] || fail "no bootstrap code in log"
CLAIM="$(curl -fsS -X POST "$BASE/bootstrap/claim" -H 'Content-Type: application/json' -d "{\"code\":\"$BOOT_CODE\"}")"
DEVICE_TOKEN="$(jget "$CLAIM" j.device_token)"
ok "device token claimed"

# ── 1. errors before any network ─────────────────────────────────────────
d1="$WORK/s1"; mkdir -p "$d1"
rc=0; run_push "$d1" "$WORK/s1.out" --stream x /nonexistent || rc=$?
[ "$rc" = 1 ] && grep -q 'cannot read file' "$WORK/s1.out" || fail "missing-file handling: rc=$rc $(cat "$WORK/s1.out")"
ok "missing file rejected"

# ── 2. unreachable server ────────────────────────────────────────────────
printf 'hello\n' > "$d1/a.txt"
rc=0; ( cd "$d1" && HOME="$AGENT_HOME" CHUTE_URL="http://127.0.0.1:9" GIT_CONFIG_NOSYSTEM=1 \
  bash "$PUSH" --stream docs a.txt ) > "$WORK/s2.out" 2>&1 || rc=$?
[ "$rc" = 1 ] && grep -q 'cannot reach chute' "$WORK/s2.out" || fail "unreachable handling: rc=$rc $(cat "$WORK/s2.out")"
ok "unreachable server reported"

# ── 3. fresh pairing + upload (no git repo → project = cwd basename) ─────
d3="$WORK/proj-alpha"; mkdir -p "$d3"
head -c 65536 /dev/urandom > "$d3/build.bin"
run_push "$d3" "$WORK/s3.out" --stream test-apk --version 1.0 --notes "first build" build.bin &
BG=$!
wait_for_line "$WORK/s3.out" 'PAIRING CODE:' 20 || { cat "$WORK/s3.out"; fail "no pairing banner"; }
C3="$(code_from "$WORK/s3.out")"
approve "$C3" "proj-alpha"
wait "$BG" || fail "push failed after approval: $(cat "$WORK/s3.out")"
grep -q "chute-push: uploaded 'build.bin' to stream 'test-apk'" "$WORK/s3.out" || fail "no upload line: $(cat "$WORK/s3.out")"
[ -s "$AGENT_HOME/.config/chute/token" ] || fail "token not persisted"
grep -q '^chu_' "$AGENT_HOME/.config/chute/token" || fail "token has unexpected shape"
[ ! -f "$AGENT_HOME/.config/chute/pending-pairing" ] || fail "pending file not cleaned up"
ok "fresh pairing + upload (code $C3 relayed in output)"
grep -qF '[Approve on phone](chute://approvals)' "$WORK/s3.out" || fail "no approvals link in pairing banner: $(cat "$WORK/s3.out")"
grep -qF '[Open](chute://version/' "$WORK/s3.out" || fail "no Open deep link: $(cat "$WORK/s3.out")"
grep -qF '[Install](chute://version/' "$WORK/s3.out" || fail "no Install deep link: $(cat "$WORK/s3.out")"
ok "deep links relayed as markdown (Approve, Open, Install)"

# ── 4. idempotent replay ─────────────────────────────────────────────────
run_push "$d3" "$WORK/s4.out" --stream test-apk build.bin || fail "replay run failed: $(cat "$WORK/s4.out")"
grep -q 'already on the server (idempotent replay)' "$WORK/s4.out" || fail "replay not detected: $(cat "$WORK/s4.out")"
ok "idempotent replay"

# ── 5. second artifact, provenance from a git checkout ───────────────────
d5="$WORK/proj-beta"; mkdir -p "$d5"
( cd "$d5" && git init -q && git config user.email t@t && git config user.name t \
  && printf 'x\n' > f && git add f && git commit -qm x \
  && git remote add origin "https://github.com/alshains-prison/alshain-abc.git" )
printf 'report body\n' > "$d5/report.md"
rm -f "$AGENT_HOME/.config/chute/token"
run_push "$d5" "$WORK/s5.out" --stream docs report.md &
BG=$!
wait_for_line "$WORK/s5.out" 'PAIRING CODE:' 20 || { cat "$WORK/s5.out"; fail "no pairing banner (s5)"; }
grep -q "requesting pairing for project 'abc'" "$WORK/s5.out" || fail "prison-name normalization failed: $(grep 'requesting' "$WORK/s5.out")"
approve "$(code_from "$WORK/s5.out")" "abc"
wait "$BG" || fail "s5 push failed: $(cat "$WORK/s5.out")"
LIST="$(curl -fsS "$BASE/v1/projects" -H "Authorization: Bearer $DEVICE_TOKEN")"
jget "$LIST" 'j.find(p=>p.name==="abc").streams.find(s=>s.name==="docs").latest.branch' | grep -qE 'master|main' || fail "branch provenance missing"
ok "alshain-abc normalized to 'abc'; git provenance attached"

# ── 6. third-party prison name with slash ────────────────────────────────
d6="$WORK/proj-gamma"; mkdir -p "$d6"
( cd "$d6" && git init -q \
  && git remote add origin "https://github.com/alshains-prison/jetbrains-intellij-community.git" )
printf 'idea\n' > "$d6/notes.txt"
rm -f "$AGENT_HOME/.config/chute/token"
run_push "$d6" "$WORK/s6.out" --stream docs notes.txt &
BG=$!
wait_for_line "$WORK/s6.out" 'PAIRING CODE:' 20 || { cat "$WORK/s6.out"; fail "no pairing banner (s6)"; }
grep -q "requesting pairing for project 'jetbrains/intellij-community'" "$WORK/s6.out" \
  || fail "owner/name normalization failed: $(grep 'requesting' "$WORK/s6.out")"
approve "$(code_from "$WORK/s6.out")" "jetbrains/intellij-community"
wait "$BG" || fail "s6 push failed: $(cat "$WORK/s6.out")"
ok "slash project name works end to end"

# ── 7. timeout leaves resumable pairing; resume reuses the code ──────────
d7="$WORK/proj-delta"; mkdir -p "$d7"
printf 'd\n' > "$d7/d.txt"
rm -f "$AGENT_HOME/.config/chute/token"
rc=0; run_push "$d7" "$WORK/s7a.out" --stream docs --wait 11 d.txt || rc=$?
[ "$rc" = 1 ] && grep -q 'still pending' "$WORK/s7a.out" || fail "timeout handling: rc=$rc $(cat "$WORK/s7a.out")"
[ -s "$AGENT_HOME/.config/chute/pending-pairing" ] || fail "pending pairing not persisted"
C7="$(code_from "$WORK/s7a.out")"
run_push "$d7" "$WORK/s7b.out" --stream docs d.txt &
BG=$!
wait_for_line "$WORK/s7b.out" 'PAIRING CODE:' 20 || { cat "$WORK/s7b.out"; fail "no banner on resume"; }
grep -q 'resuming pending pairing' "$WORK/s7b.out" || fail "did not resume: $(cat "$WORK/s7b.out")"
[ "$(code_from "$WORK/s7b.out")" = "$C7" ] || fail "resume drew a different code"
approve "$C7" "proj-delta"
wait "$BG" || fail "resumed push failed: $(cat "$WORK/s7b.out")"
ok "timeout → resume reuses code $C7"

# ── 8. stale token → automatic re-pair ───────────────────────────────────
printf 'chu_bogusbogusbogus\n' > "$AGENT_HOME/.config/chute/token"
d8="$WORK/proj-epsilon"; mkdir -p "$d8"; printf 'e\n' > "$d8/e.txt"
run_push "$d8" "$WORK/s8.out" --stream docs e.txt &
BG=$!
wait_for_line "$WORK/s8.out" 'PAIRING CODE:' 20 || { cat "$WORK/s8.out"; fail "no re-pair banner"; }
grep -q 'token rejected (revoked or stale)' "$WORK/s8.out" || fail "401 path not taken: $(cat "$WORK/s8.out")"
approve "$(code_from "$WORK/s8.out")" "proj-epsilon"
wait "$BG" || fail "re-paired push failed: $(cat "$WORK/s8.out")"
grep -q "chute-push: uploaded" "$WORK/s8.out" || fail "no upload after re-pair"
ok "401 → re-pair → upload"

# ── 9. denial ────────────────────────────────────────────────────────────
d9="$WORK/proj-zeta"; mkdir -p "$d9"; printf 'z\n' > "$d9/z.txt"
rm -f "$AGENT_HOME/.config/chute/token"
run_push "$d9" "$WORK/s9.out" --stream docs z.txt &
BG=$!
wait_for_line "$WORK/s9.out" 'PAIRING CODE:' 20 || { cat "$WORK/s9.out"; fail "no banner (s9)"; }
deny "$(code_from "$WORK/s9.out")"
rc=0; wait "$BG" || rc=$?
[ "$rc" = 1 ] && grep -q 'pairing denied on the phone' "$WORK/s9.out" || fail "denial handling: rc=$rc $(cat "$WORK/s9.out")"
[ ! -f "$AGENT_HOME/.config/chute/pending-pairing" ] || fail "pending file survived denial"
ok "denial is a clean failure"

# ── set fixtures ─────────────────────────────────────────────────────────
PNG_B64='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
png() { local f; for f in "$@"; do printf '%s' "$PNG_B64" | base64 -d > "$f"; done; }
line_of() { grep -nF -- "$2" "$1" | head -n1 | cut -d: -f1; }  # line_of <file> <fixed-string>
resp_of() { grep -F -- "$2" "$1" | head -n1 | sed 's/^[^{]*//'; }  # JSON tail of a chute-push line

# ── 10. set push: built before pairing, Gallery link, manifest read-back ──
d10="$WORK/proj-sets"; R10="$d10/renders"; mkdir -p "$R10"
png "$R10/a.png" "$R10/b.png" "$R10/c.png"
printf '# what changed\n' > "$R10/notes.md"
T_C='Über <b> & "quotes" — café'
T_MD="$(printf '%.0sé' $(seq 120))"   # 120 runes, 240 bytes: the title cap is runes
printf 'c.png\t%s\tdescription with é\nnotes.md\t%s\t\n' "$T_C" "$T_MD" > "$R10/items.tsv"
SET10=(--stream screenshots --set renders --items renders/items.tsv --cover b.png)
run_push "$d10" "$WORK/s10.out" "${SET10[@]}" --version v1 --notes "before/after" &
BG=$!
wait_for_line "$WORK/s10.out" 'PAIRING CODE:' 20 || { cat "$WORK/s10.out"; fail "no pairing banner (s10)"; }
approve "$(code_from "$WORK/s10.out")" "proj-sets"
rc=0; wait "$BG" || rc=$?
[ "$rc" = 0 ] || fail "s10 set push: rc=$rc $(cat "$WORK/s10.out")"
grep -qF "uploaded set 'renders.zip' (4 items) to stream 'screenshots'" "$WORK/s10.out" \
  || fail "s10: no set upload line: $(cat "$WORK/s10.out")"
lb="$(line_of "$WORK/s10.out" 'chute-set: 4 items')"; lp="$(line_of "$WORK/s10.out" 'PAIRING CODE:')"
[ -n "$lb" ] && [ "$lb" -lt "$lp" ] || fail "s10: the set was not built before pairing (build line $lb, banner line $lp)"
R10JSON="$(resp_of "$WORK/s10.out" "uploaded set 'renders.zip'")"
[ "$(jget "$R10JSON" j.set_item_count)" = 4 ] || fail "s10: set_item_count: $R10JSON"
[ "$(jget "$R10JSON" j.created)" = true ] || fail "s10: created: $R10JSON"
V10="$(jget "$R10JSON" j.version_id)"
grep -qF "[Open](chute://version/$V10) · [Gallery](chute://set/$V10)" "$WORK/s10.out" \
  || fail "s10: no Open · Gallery deep links: $(cat "$WORK/s10.out")"
! grep -qF '[Install]' "$WORK/s10.out" || fail "s10: a set must not offer Install"
# The stored manifest is byte-identical to what chute-set.py builds offline
# from the same inputs, and so is the whole archive (determinism, D4).
curl -fsS -o "$WORK/s10.stored" -H "Authorization: Bearer $DEVICE_TOKEN" "$BASE/v1/versions/$V10/set" \
  || fail "s10: manifest read-back"
python3 "$SET_PY" --out "$WORK/s10-ref.zip" --items "$R10/items.tsv" --cover b.png \
  --manifest-out "$WORK/s10.manifest" "$R10" > /dev/null || fail "s10: offline reference build"
cmp -s "$WORK/s10.stored" "$WORK/s10.manifest" || fail "s10: stored manifest differs from chute-set.py's"
[ "$(sha256sum "$WORK/s10-ref.zip" | cut -d' ' -f1)" = "$(jget "$R10JSON" j.sha256)" ] \
  || fail "s10: uploaded archive is not the deterministic offline build"
M10="$(cat "$WORK/s10.stored")"
# The phone's decoder is case-sensitive: the on-the-wire keys are pinned here.
[ "$(jget "$M10" 'Object.keys(j).join()')" = "chute_set,items" ] || fail "s10: top-level keys: $M10"
[ "$(jget "$M10" '[...new Set(j.items.map(i=>Object.keys(i).join()))].join("|")')" = \
  "id,path,title,description,content_type" ] || fail "s10: item keys: $M10"
[ "$(jget "$M10" 'j.chute_set')" = 1 ] || fail "s10: chute_set: $M10"
[ "$(jget "$M10" 'j.items.map(i=>i.path).join()')" = "b.png,c.png,notes.md,a.png" ] \
  || fail "s10: order is not cover, TSV rows, then by name: $M10"
[ "$(jget "$M10" 'j.items[1].title')" = "$T_C" ] || fail "s10: UTF-8/<>&\" title mangled: $M10"
[ "$(jget "$M10" '[...j.items[2].title].length')" = 120 ] || fail "s10: 120-rune title: $M10"
ok "set push: 4 items, built before pairing, Gallery link, manifest read back byte-identical"

# ── 11. unchanged set re-pushed: replay, links again, --version ignored ──
run_push "$d10" "$WORK/s11.out" "${SET10[@]}" --version v2 || fail "s11 replay: $(cat "$WORK/s11.out")"
grep -q 'already on the server (idempotent replay)' "$WORK/s11.out" || fail "s11: no replay line: $(cat "$WORK/s11.out")"
grep -qF "set 'renders.zip' (4 items)" "$WORK/s11.out" || fail "s11: replay line lacks the item count"
[ "$(jget "$(resp_of "$WORK/s11.out" 'idempotent replay')" j.version_id)" = "$V10" ] || fail "s11: version id changed"
grep -qF -- '--version/--notes were ignored' "$WORK/s11.out" || fail "s11: no ignored note: $(cat "$WORK/s11.out")"
grep -qF "[Gallery](chute://set/$V10)" "$WORK/s11.out" || fail "s11: no Gallery link on replay"
LIST="$(curl -fsS "$BASE/v1/projects" -H "Authorization: Bearer $DEVICE_TOKEN")"
[ "$(jget "$LIST" 'j.find(p=>p.name==="proj-sets").streams.find(s=>s.name==="screenshots").latest.version_name')" = v1 ] \
  || fail "s11: server should keep the original version name"
ok "unchanged set: replay, same version, --version ignored and said so"

# ── 12. set inputs rejected locally, before any network ──────────────────
[ -s "$AGENT_HOME/.config/chute/token" ] || fail "s12 needs a held token (so pairing cannot be what fails)"
d12="$WORK/s12"; mkdir -p "$d12/many" "$d12/xyz" "$d12/empty" "$d12/blank" "$d12/miss"
for i in $(seq -w 1 101); do png "$d12/many/p$i.png"; done
png "$d12/xyz/a.png"; printf 'x\n' > "$d12/xyz/foo.xyz"
png "$d12/blank/a.png"; printf 'a.png\t   \t\n' > "$d12/blank.tsv"
png "$d12/miss/a.png"; printf 'a.png\tA\t\nnope.png\tN\t\n' > "$d12/miss.tsv"
s12case() {  # s12case <label> <expected-text> [chute-push set args...]
  local label="$1" want="$2" rc=0; shift 2
  PUSH_URL="http://127.0.0.1:1" run_push "$d12" "$WORK/s12-$label.out" --stream screenshots "$@" || rc=$?
  [ "$rc" = 3 ] || fail "s12 $label: rc=$rc (want 3): $(cat "$WORK/s12-$label.out")"
  grep -qF -- "$want" "$WORK/s12-$label.out" || fail "s12 $label: missing '$want': $(cat "$WORK/s12-$label.out")"
  ! grep -q 'cannot reach' "$WORK/s12-$label.out" || fail "s12 $label: reached for the network"
}
s12case many 'a set holds at most 100 items' --set many
s12case xyz 'foo.xyz: no content type is known for ".xyz"' --set xyz
s12case empty 'a set needs at least one item' --set empty
s12case blank 'item 0 (a.png): title is required' --set blank --items blank.tsv
s12case miss "line 2: 'nope.png' is not a file" --set miss --items miss.tsv
ok "set inputs rejected with exit 3 before any network (101 files, .xyz, empty, blank title, missing row)"
# No python3: a PATH holding only what chute-push runs before its python3
# check (exec wrappers, so this works wherever the real tools live).
NOPY="$WORK/nopy"; mkdir -p "$NOPY"
for t in dirname basename mktemp rm; do
  printf '#!%s\nexec %s "$@"\n' "$BASH" "$(command -v "$t")" > "$NOPY/$t"; chmod +x "$NOPY/$t"
done
rc=0; ( cd "$d12" && PATH="$NOPY" HOME="$AGENT_HOME" CHUTE_URL="http://127.0.0.1:1" \
  "$BASH" "$PUSH" --stream screenshots --set xyz ) > "$WORK/s12-nopy.out" 2>&1 || rc=$?
[ "$rc" = 1 ] && grep -qF -- '--set needs python3' "$WORK/s12-nopy.out" \
  && grep -qF 'single-file pushes still work' "$WORK/s12-nopy.out" || fail "s12 no python3: rc=$rc $(cat "$WORK/s12-nopy.out")"
ok "no python3: --set says so (single-file pushes unaffected)"

# ── 14. null-case controls: plain zip, .apk name, usage ──────────────────
d14="$WORK/s14"; mkdir -p "$d14"
python3 -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.writestr("a.txt","plain"); z.close()' "$d14/plain.zip"
run_push "$d14" "$WORK/s14a.out" --stream docs plain.zip || fail "s14 plain zip: $(cat "$WORK/s14a.out")"
[ "$(jget "$(resp_of "$WORK/s14a.out" "uploaded 'plain.zip'")" j.set_item_count)" = 0 ] || fail "s14: plain zip counted as a set"
grep -qF '[Install](chute://version/' "$WORK/s14a.out" || fail "s14: plain zip lacks Install"
! grep -qF '[Gallery]' "$WORK/s14a.out" || fail "s14: plain zip offered a Gallery"
python3 "$SET_PY" --out "$d14/x.apk" --items "$R10/items.tsv" --cover b.png "$R10" > /dev/null || fail "s14: build x.apk"
rc=0; run_push "$d14" "$WORK/s14b.out" --stream test-apk x.apk || rc=$?
[ "$rc" = 1 ] && grep -q 'APK could not be parsed' "$WORK/s14b.out" || fail "s14 set-as-apk: rc=$rc $(cat "$WORK/s14b.out")"
s14usage() {  # s14usage <label> <expected-text> [args...]
  local label="$1" want="$2" rc=0; shift 2
  PUSH_URL="http://127.0.0.1:1" run_push "$d10" "$WORK/s14-$label.out" --stream screenshots "$@" || rc=$?
  [ "$rc" = 2 ] && grep -qF -- "$want" "$WORK/s14-$label.out" || fail "s14 $label: rc=$rc $(cat "$WORK/s14-$label.out")"
}
s14usage apkname 'a set must not be named .apk' --set renders --name x.APK
s14usage positional 'usage:' --set renders renders/a.png
s14usage itemsalone 'usage:' --items renders/items.tsv renders/a.png
ok "plain zip gets Install not Gallery; set zip as .apk is refused by the server; .apk name and flag misuse are usage errors"

# Tripwires: a stub skill whose "chute-set.py" hands chute-push a prepared
# archive, so the drift alarms can be exercised against the real server.
STUB="$WORK/stubskill"; mkdir -p "$STUB"; cp "$PUSH" "$STUB/chute-push"
cat > "$STUB/chute-set.py" <<'EOF'
# harness stub: copies $STUB_ZIP to --out instead of building a set
import os, shutil, sys
CONFORMED_TO_CHUTE = "0123456789abcdef0123456789abcdef01234567"
a = sys.argv[1:]
shutil.copyfile(os.environ["STUB_ZIP"], a[a.index("--out") + 1])
EOF
python3 -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.writestr("b.txt","no manifest"); z.close()' "$d14/drift.zip"
rc=0; ( export STUB_ZIP="$d14/drift.zip"; PUSH_BIN="$STUB/chute-push" run_push "$d10" "$WORK/s14d.out" --stream screenshots --set renders ) || rc=$?
[ "$rc" = 1 ] && grep -qF "stored 'renders.zip' as a plain artifact, not a set" "$WORK/s14d.out" \
  || fail "s14 tripwire A: rc=$rc $(cat "$WORK/s14d.out")"
! grep -qF 'chute://' "$WORK/s14d.out" || fail "s14 tripwire A: relayed links for a drifted push"
python3 "$MAKE_RAW" --out "$d14/invalid.zip" --manifest "$CORPUS/blank-title.json" --member a.png || fail "s14: make invalid"
rc=0; ( export STUB_ZIP="$d14/invalid.zip"; PUSH_BIN="$STUB/chute-push" run_push "$d10" "$WORK/s14e.out" --stream screenshots --set renders ) || rc=$?
[ "$rc" = 1 ] && grep -qF 'set manifest is invalid: item 0: title is required' "$WORK/s14e.out" \
  && grep -qF 'producer bug' "$WORK/s14e.out" \
  && grep -qF 'CONFORMED_TO_CHUTE=0123456789abcdef0123456789abcdef01234567' "$WORK/s14e.out" \
  || fail "s14 tripwire B: rc=$rc $(cat "$WORK/s14e.out")"
ok "tripwires: plain-artifact drift (A) and a server-rejected manifest (B) fail loud with exit 1"

# ── 15. optional byte-identity against chute's own cmd/chute-set ─────────
if [ -n "${CHUTE_SET_BIN:-}" ]; then
  d15="$WORK/s15"; mkdir -p "$d15/imgs"
  png "$d15/imgs/a.png" "$d15/imgs/b.jpg" "$d15/imgs/c.webp" "$d15/imgs/d e.gif"
  # Image members only, TSV outside the dir, rows in sorted order, no --cover:
  # the one configuration in which both producers must agree byte for byte.
  printf 'a.png\tA <b> & "c" é\tdesc é\nb.jpg\t\t\nc.webp\tÜnïcödé — title\t\n' > "$d15/titles.tsv"
  "$CHUTE_SET_BIN" -o "$d15/go.zip" -title-from "$d15/titles.tsv" "$d15/imgs" > /dev/null 2>&1 || fail "s15: chute-set failed"
  python3 -c 'import sys,zipfile; sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read(".chute/set.json"))' "$d15/go.zip" > "$d15/go.json"
  python3 "$SET_PY" --out "$d15/py.zip" --items "$d15/titles.tsv" --manifest-out "$d15/py.json" "$d15/imgs" > /dev/null \
    || fail "s15: chute-set.py failed"
  cmp -s "$d15/go.json" "$d15/py.json" || fail "s15: manifests differ"$'\n'"  go: $(cat "$d15/go.json")"$'\n'"  py: $(cat "$d15/py.json")"
  ok "byte-identical manifest to cmd/chute-set ($CHUTE_SET_BIN)"
else
  echo "SKIP: s15 byte-identity (set CHUTE_SET_BIN to a cmd/chute-set build)"
fi

# ── 16. local size pre-check against CHUTE_MAX_UPLOAD_BYTES ──────────────
d16="$WORK/s16"; mkdir -p "$d16/big"; png "$d16/big/a.png"
head -c 4096 /dev/urandom > "$d16/big/noise.txt"   # incompressible: the zip stays > 1000 bytes
rc=0; ( export CHUTE_MAX_UPLOAD_BYTES=1000; PUSH_URL="http://127.0.0.1:1" run_push "$d16" "$WORK/s16.out" --stream docs --set big ) || rc=$?
[ "$rc" = 3 ] && grep -qF 'over the upload cap of 1000 bytes' "$WORK/s16.out" || fail "s16: rc=$rc $(cat "$WORK/s16.out")"
! grep -q 'cannot reach' "$WORK/s16.out" || fail "s16: reached for the network"
ok "oversized set refused locally (exit 3) before any request"

# ── 13. error-text parity: chuted vs chute-set.py check vs the corpus ────
# Each corpus manifest is zipped UNVALIDATED (make-raw-set.py) and PUT with
# the agent token. The server's 400 "error" (JSON body; X-Chute-Error-Detail
# never reaches a client) must equal "set manifest is invalid: " + what
# chute-set.py check prints, which must equal the hand-kept expected.tsv
# column. A changed limit or string on either side turns this red.
ensure_paired "$WORK/proj-sets" "proj-sets"
AGENT_TOKEN="$(cat "$AGENT_HOME/.config/chute/token")"
CONFORMED="$(sed -n 's/^CONFORMED_TO_CHUTE = "\([0-9a-f]*\)"$/\1/p' "$SET_PY")"
[ -n "$CONFORMED" ] || fail "no CONFORMED_TO_CHUTE in chute-set.py"
mkdir -p "$WORK/s13"
n13=0
while IFS=$'\t' read -r -u 3 f exp args; do  # fd 3: nothing in the body can eat the TSV
  case "$f" in ''|'#'*) continue ;; esac
  z="$WORK/s13/${f%.json}.zip"
  # shellcheck disable=SC2086  # args is a word list of make-raw-set.py flags
  python3 "$MAKE_RAW" --out "$z" --manifest "$CORPUS/$f" $args || fail "s13 $f: make-raw-set.py failed"
  crc=0; got="$(python3 "$SET_PY" check "$z")" || crc=$?
  http="$(curl -sS -o "$WORK/s13.body" -w '%{http_code}' -T "$z" \
    -H "Authorization: Bearer $AGENT_TOKEN" \
    "$BASE/v1/streams/corpus/versions?filename=${f%.json}.zip")"
  body="$(cat "$WORK/s13.body")"
  case "$exp" in
    VALID|ACCEPT)
      [ "$http" = 201 ] || fail "s13 $f: server should store it as a set, got $http $body"
      [ "$(jget "$body" j.set_item_count)" -ge 1 ] || fail "s13 $f: stored as a plain artifact: $body"
      if [ "$exp" = VALID ]; then
        [ "$crc" = 0 ] && [ -z "$got" ] || fail "s13 $f: check rejects what the server accepts: rc=$crc $got"
      else
        [ "$crc" = 3 ] && case "$got" in producer-only:*) true ;; *) false ;; esac \
          || fail "s13 $f: check should report producer-only: rc=$crc $got"
      fi
      ;;
    *)
      [ "$http" = 400 ] || fail "s13 $f: expected 400, got $http $body"
      srv="$(jget "$body" j.error)" || fail "s13 $f: no error field: $body"
      [ "$crc" = 3 ] || fail "s13 $f: check rc=$crc (want 3): $got"
      [ "$srv" = "set manifest is invalid: $got" ] \
        || fail "s13 $f: server and check disagree"$'\n'"  server: $srv"$'\n'"  check:  $got"
      [ "$got" = "$exp" ] \
        || fail "s13 $f: check disagrees with expected.tsv"$'\n'"  check:    $got"$'\n'"  expected: $exp"
      ;;
  esac
  n13=$((n13 + 1))
done 3< "$CORPUS/expected.tsv"
[ "$n13" -ge 20 ] || fail "s13 ran only $n13 corpus rows"
ok "error-text parity on $n13 corpus rows (CONFORMED_TO_CHUTE=$CONFORMED)"

echo
echo "chute-push harness passed"
