#!/usr/bin/env bash
# End-to-end harness for chute-push against a real local chuted (spec §7.1).
#
#   CHUTED_BIN=/path/to/chuted ./tests/test-chute-push.sh
#
# Needs: bash, curl, git, node (JSON assertions), and a chuted binary
# (CGO-free; `go build -o chuted ./cmd/chuted` in a chute checkout).
# Starts its own server on 127.0.0.1:18080 with a throwaway data dir; no
# phone, no network beyond loopback. Every step prints ok:; ends with
# "chute-push harness passed".
set -euo pipefail

[ -n "${CHUTED_BIN:-}" ] || { echo "set CHUTED_BIN to a chuted binary" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUSH="$ROOT/plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push"
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

run_push() {  # run_push <cwd> <outfile> [args...]
  local cwd="$1" out="$2"; shift 2
  ( cd "$cwd" && PATH="$CURL_SHIM:$PATH" HOME="$AGENT_HOME" CHUTE_URL="$BASE" \
      GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
      bash "$PUSH" "$@" ) > "$out" 2>&1
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

echo
echo "chute-push harness passed"
