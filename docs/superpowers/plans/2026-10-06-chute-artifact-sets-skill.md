# chute artifact sets (`chute-push --set`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent in an acradle VM pushes several related files as **one** chute artifact set (one upload, one notification, one gallery, one relayed *Gallery* link), per `docs/superpowers/specs/2026-10-06-chute-artifact-sets-skill-design.md` (the spec; section names below refer to it).

**Architecture:** `chute-set.py` (python3 stdlib, no network) builds and self-checks a deterministic zip carrying `.chute/set.json`, mirroring chute `internal/set` with the server's exact error strings. `chute-push --set` builds first (before any pairing or network), then runs the unchanged pair → upload → re-pair-once flow, and picks *Gallery* vs *Install* from the server's `set_item_count`.

**Tech stack:** bash + curl + git for `chute-push`; python3 ≥ 3.10 stdlib only (`zipfile`, `json`, `re`, `os`, `argparse`, `unicodedata`) for `chute-set.py` (D1 exception, recorded in `CLAUDE.md`). No jq. Harness: bash + curl + node (`jget`) + python3.

**Scope list (done means every box below ticked, all suites green, tree clean, then STOP):** spec Rollout steps 4–9 = Tasks 1–6. Step 10 (`git push origin main`) is a human go/no-go and is not in this plan. The acradle-side docs (Task 7) and the human-only checks (Task 8) are tracked here so they are not lost, but are gated as stated. Anything discovered mid-way that is not on this list goes to a one-line note in the final report, not into the work.

## Global constraints

- **Repo rules** (from the task brief and acradle `AGENTS.md`): never push; never `git add -A`/`git add .`; never `git stash`; add explicit paths. One self-contained commit per task, conventional subject, body says why, ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commits go straight to `main` (this repo's convention).
- **No fix/feature without a red test first.** Every assertion that passes the moment it is written is red-checked: break the code it pins, watch it fail, restore. Record the red output in the commit body.
- **Edits through the Edit/Write tools only**; no shell string replacement, no PowerShell `Get-Content`/`Set-Content` round-trips. Scripts keep LF; check committed blobs with `git show HEAD:<path> | od -c | grep -c '\\r'` (expect 0).
- **chute source of truth:** read chute from the fresh clone at `C:/Users/chris/AppData/Local/Temp/chute-main` (`50322ea`), never `C:/Projects/alshain-chute` (stale). Any disagreement between the spec and that code: follow the code, update the spec in the same commit, say so in the body.
- **Harness command** (Git Bash, ~1m47s today, longer after Task 2):
  `cd /c/Projects/acradle-setup && CHUTED_BIN=/c/Users/chris/AppData/Local/Temp/chute-bin/chuted.exe bash ./tests/test-chute-push.sh`
  Optional s15: add `CHUTE_SET_BIN=/c/Users/chris/AppData/Local/Temp/chute-bin/chute-set.exe`. No external curl shim is needed (`b6f0055` handles chuted's per-IP pair limiter inside the harness).
- **Unit command:** `cd /c/Projects/acradle-setup && python3 tests/test-chute-set.py -v` (the file ends in `unittest.main()`; its dashed name is not importable, so it is run as a path, not with `-m unittest <module>`). The host's python3 is 3.13; 3.10 compatibility is proven only by running the unit file once in a guest (Task 1, step 8).

### Facts from chute's code that the implementation must honour

Checked against `50322ea`; these are the traps, each pinned by a test below.

1. **Error strings** come from `internal/set/set.go` (`Decode`) and `archive.go` (`Parse`, `readBounded`) verbatim. The server prefixes them with `set manifest is invalid: ` (`internal/api/upload.go`). Item indices are **0-based** manifest positions.
2. **`X-Chute-Error-Detail` never reaches the client** — `statusRecorder` deletes it. Parity is asserted on the 400 body's JSON `error` field via `jget`.
3. **Blank checks are Go `strings.TrimSpace`** (`unicode.IsSpace`: `\t \n \v \f \r`, space, U+0085, U+00A0, and the Unicode `White_Space` set). Python `str.strip()` additionally strips U+001C–U+001F; use an explicit Go-equivalent whitespace set.
4. **`%q` messages** (`no archive member named %q`, `%q is not a regular file`) need a `go_quote()` matching `strconv.Quote` for the cases the corpus uses (ASCII, `"`, `\`, control chars, printable non-ASCII kept literal).
5. **Manifest size:** `Decode` rejects `len > 262144`; `Parse`'s `readBounded` rejects `len >= 262144`. Both say `manifest is larger than 262144 bytes`. The producer must keep the serialized manifest `< 262144`.
6. **Parse order:** no manifest member → plain artifact (not an error); only then `> 10000 entries`, then `archive has duplicate entry names`, then the manifest read, `Decode`, then per-item member/regular/size checks.
7. **APK first:** a `.apk` filename (any case) takes the APK branch exclusively → 400 `APK could not be parsed` for a set zip (`upload.go` `contentTypeFor` + switch).
8. **Go `json.Marshal`** escapes `<` `>` `&` as `\u003c` `\u003e` `\u0026`, U+2028/U+2029 as `\u2028`/`\u2029`, control chars as `\u00XX` (lowercase hex) except `\b \f \n \r \t`; field order is the struct order `chute_set, items[id, path, title, description, content_type]`; `description` is always emitted.
9. **`cmd/chute-set`** (`main.go`): members = `os.ReadDir`, skip dirs, dotfiles and non-regular (lstat) entries, `sort.Strings`; TSV filename is `TrimSpace`d there (not in ours, per spec); empty title → basename; `sanitizeID` = `[^A-Za-z0-9._-]` → `_` per **rune**, truncate to 64 **bytes**, empty → `item`; `dedupeID` appends `-2`, `-3`… truncating the base to fit 64.
10. **Readback:** `GET /v1/versions/{id}/set` (CapRead) returns the stored manifest bytes verbatim; 404 `version is not a set` otherwise.

---

### Task 1: `chute-set.py` + unit tests (Rollout step 4)

**Files:**
- Create: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-set.py` (mode 100755: `git add --chmod=+x`, `#!/usr/bin/env python3`, LF)
- Create: `tests/test-chute-set.py`

**Interfaces produced** (Task 3 and Task 2 consume exactly these):
- `chute-set.py --out OUT.zip [--items TSV] [--cover NAME] [--type EXT=MIME]... [--manifest-out FILE] DIR`
- `chute-set.py check FILE.zip` — prints nothing and exits 0 when valid; else prints the **server wording** (no `chute-set:` prefix, no `hint:`) on one line and exits 3.
- Exit 0 / 3 (inputs rejected; nothing written) / 1 (I/O).
- Stdout on build success: one line per item `  <file>  id=<id>  "<title>"` (+ ` (opaque on the phone: opens with another app)` for non-`image/*`), then `chute-set: N items, <bytes> bytes`.
- Stderr on a rejected input: `chute-set: item N (<file>): <server wording>` or `chute-set: <file>: …`, optional second line `hint: …`.
- `skipped (symlink|directory): <name>` lines on stderr (Task 3 re-prefixes nothing; chute-push lets them through).
- Module constant `CONFORMED_TO_CHUTE = "50322eaf44c39f2002dfdcb066a03c5b8200546f"` and `MANIFEST_KEYS`.

- [x] **Step 1: Write the failing unit tests** in `tests/test-chute-set.py` (load the script by path with `importlib.util.spec_from_file_location`, since its dashed name is not importable and it lives outside a package; drive the CLI with `subprocess` + `sys.executable` for exit-code tests). One `TestCase` per spec "Unit" bullet:
  - **ids:** cases lifted from chute `cmd/chute-set/main_test.go` (read it from the clone; copy its tables verbatim with a comment naming the source commit): sanitize, 64-byte truncation, `-2` dedupe with truncation, emoji → `__`, empty → `item`. Plus: ids computed over **sorted** names regardless of TSV order (D5).
  - **TSV:** tab split, 4th column → exit 3 with line number, row naming a non-member → exit 3 with line number, `\r` stripped, blank lines skipped, empty title → basename, filename **not** trimmed, invalid UTF-8 in title → exit 3 naming the line; TSV inside `DIR` excluded from members.
  - **members:** dotfiles, subdirectories and symlinks skipped with the `skipped (...)` line (symlink case `skipUnless` `os.symlink` works — Windows without dev mode cannot create them; it runs in the guest check, step 8); surrogate-escaped filename → exit 3 "rename the file".
  - **ordering:** TSV row order, then remaining names sorted; `--cover NAME` hoists to `items[0]`; `--cover` naming a non-member → exit 3.
  - **MIME:** table from D6 only (`.webp` resolves even though 3.10 `mimetypes` lacks it); `--type EXT=MIME` overrides and adds; unknown extension → exit 3 naming the file; `tar.gz` double extension resolves before `gz`.
  - **Layer 1** (every rule → expected server message, 0-based index): 0 files, 101 files, blank title (including a title of only U+00A0 — blank in Go — and a title of only U+001C — **not** blank in Go, so accepted: fact 3), 121-rune title, 2001-rune description, basename > 1024 bytes, backslash in name, per-file > 64 MiB and total > 1 GiB (use `os.truncate` sparse files, skip if the FS refuses), manifest ≥ 262144 bytes. `hint:` line present on build, absent on `check`.
  - **manifest bytes:** top-level keys exactly `["chute_set","items"]` in order, item keys exactly `MANIFEST_KEYS` in order, `chute_set` is `int` 1, `description` present when empty, `<>&` → `\u003c\u003e\u0026`, U+2028/9 escaped, `"` and `\n` escaped as Go does, non-ASCII literal UTF-8 (fact 8).
  - **determinism:** build twice into different paths → byte-identical zips; every entry has `date_time == (1980,1,1,0,0,0)`, `compress_type == ZIP_DEFLATED`, `create_system == 3`, `external_attr == 0o100644 << 16`, no extra field, entry 0 is `.chute/set.json`, no directory entries.
  - **atomicity:** a failing build leaves neither `OUT.zip` nor `OUT.zip.tmp-*`; a pre-existing `OUT.zip` is untouched by a failing build.
  - **`check`** on hand-corrupted archives (build with raw `zipfile`, not the producer): symlink entry (`0o120777 << 16`) → `item 0: "<name>" is not a regular file`; directory entry (name ending `/`); zero type field → regular (accepted); encrypted flag bit; mis-cased `"Title"` key → accepted by the server's case-insensitive decoder but rejected by `check` as producer-only (assert the distinct producer-only message); duplicate entry names; missing member → `item 0: no archive member named "x.png"`; a member name with `"` and `\` to pin `go_quote` (fact 4); a zip with no manifest → `check` reports "not a set" (exit 3, wording chosen here, documented in the script header).
- [x] **Step 2: Run, watch them fail** (`ModuleNotFoundError`/file missing). Record the output.
- [x] **Step 3: Implement `chute-set.py`** to the spec's "chute-set.py", "Manifest / zip construction" and "Validation & errors" sections plus facts 1–9. Structure: `GO_SPACE` set + `go_trim()`; `go_quote()`; `go_json()` (json.dumps + Go escapes); `sanitize_id`/`dedupe_id`; `MIME_TABLE`; `load_items_tsv()`; `list_members()`; `validate_manifest(m) -> str|None` (the `Decode` mirror, shared by build and `check`); `check_archive(path) -> str|None` (the `Parse` mirror); `build()` writes `OUT.zip.tmp-<pid>`, runs `check_archive` on it, then `os.replace`. Header comment documents exit codes, `CONFORMED_TO_CHUTE`, and the "declared mirror" rule from D2. No network, no `HOME`, no env reads.
- [x] **Step 4: Run the unit file until green.** Expected tail: `OK` (record the test count).
- [x] **Step 5: Red-check** each assertion that went green on first run by breaking the rule it pins (at minimum: swap `go_trim` for `str.strip`, drop the `\u003c` escape, emit `"Title"`, write entries with `time.localtime()`, make `sanitize_id` truncate by code points). Watch the matching test fail; restore. Record one line per break in the commit body.
- [x] **Step 6: Cross-check the manifest against the real server once, by hand** before Task 2 exists: build a 2-PNG set, `curl -T` it to a local chuted with a device token, expect 201 with `"set_item_count":2`. (Task 2 automates this; this step only catches a mirror that cannot pass at all before investing in the harness.)
- [x] **Step 7: Commit** `feat(chute-set): python3 builder and validator for chute artifact sets` with the two files.
- [x] **Step 8: 3.10 check in a guest** (done before the commit: 90 tests OK, none skipped, on 3.10.12) (read-only use of an already-running VM; do not start, stop or install anything): `multipass transfer` the two files into `/tmp/<unique>/` of a running guest, run `python3 /tmp/<unique>/test-chute-set.py -v` there (guests have 3.10.12), then delete the directory. If no guest is running, record "3.10 unverified" in the final report rather than starting one. A 3.10 failure is fixed red-first in a follow-up commit.

### Task 2: corpus, raw-set maker, harness s10–s16 (Rollout step 5; red until Task 3)

**Files:**
- Create: `tests/set-corpus/<rule>.json` (one manifest per rule) and `tests/set-corpus/expected.tsv` (`<rule-file>\t<expected server message without the "set manifest is invalid: " prefix>\t<make-raw-set.py args>`)
- Create: `tests/make-raw-set.py` (deliberately unvalidated: wraps a manifest file plus named dummy members — and, on request, a directory entry — into a zip, no checks)
- Modify: `tests/test-chute-push.sh`

**Split as executed** (orchestrator's preference, so no commit leaves the harness red): this task's commit landed the corpus, `make-raw-set.py`, the `python3` presence check and **s13 only**, green, since s13 exercises `chute-set.py check` against the server and needs no `--set`. s10–s12 and s14–s16 are written red-first as Task 3 Step 0 and land in Task 3's commit; the word gate moves to Task 4 Step 1. Corpus additions beyond the list below: `duplicate path`, `archive has duplicate entry names` (two members of one name), a missing member whose name holds a tab, `é` and `"` (pins `go_quote` against the server, fact 4), and a `VALID` row whose title is only U+001C (not blank in Go: fact 3 against the server). `VALID` = server 201 as a set and `check` silent; `ACCEPT` = server 201 as a set and `check` prints `producer-only:`.

- [x] **Step 1: Corpus.** One file per spec s13 rule: `chute_set: 2`, unknown field, mis-cased `Title` (expected column = `ACCEPT`; producer-only), 0 items, 101 items, bad id charset, 65-char id, duplicate id, leading `/`, backslash, `a/../b`, blank title, 121-rune title, 2001-rune description, blank content_type, missing member, directory member, manifest of exactly 262144 bytes (generate this one in the harness, not as a 256 KiB fixture: `make-raw-set.py --pad-to 262144`). Expected strings are copied from `set.go`/`archive.go` with the 0-based index; cite the chute commit in a header comment of `expected.tsv`.
- [ ] **Step 2: Harness scenarios** (append after s9, same `ok:`/`fail` style; fixtures are 1×1 PNGs via `base64 -d`; the harness gains a `python3` presence check next to the `CHUTED_BIN` one):
  - **s10 set push** — 3 PNGs + `notes.md`, `items.tsv` inside the dir with UTF-8, `<>&"` and a 120-rune title, `--manifest-out` captured via a `CHUTE_SET_MANIFEST_OUT` test hook *or* by re-running `chute-set.py --manifest-out` on the same inputs (prefer the latter: no test-only flag in `chute-push`). Assert: rc 0; `uploaded set 'renders.zip' (4 items)`; `jget` `set_item_count === 4`, `created === true`; `[Gallery](chute://set/<vid>)` present and `Install` absent; `curl` `GET /v1/versions/<vid>/set` with the device token is `cmp`-equal to the manifest bytes; node asserts top-level keys exactly `{chute_set,items}` and item keys exactly `{id,path,title,description,content_type}`; order = TSV order then name; cover first.
  - **s11 replay** — same command with a new `--version` → `already on the server (idempotent replay)`, same `version_id`, original `version_name` (read back via the versions list route `GET /v1/projects/{id}/streams/{s}/versions` with the device token), the "ignored" line.
  - **s12 validation before network** — `CHUTE_URL=http://127.0.0.1:1`, token present: 101 files, `foo.xyz`, empty dir, blank title, missing-file row → rc 3, the rule text, and output does **not** contain `cannot reach`.
  - [x] **s13 error-text parity** (landed in this task's commit) — for each corpus row: `make-raw-set.py` → zip; `curl -T` it with the **agent** token (the upload route is `CapUpload` + `requireTokenProject`; the device token gets 403 `insufficient capability`, observed; s13 pairs once via `ensure_paired` if no token is held) (through the harness's own curl, not the shim — one PUT per row stays under no limiter since uploads are token-authed; confirm against `router.go` that `/streams/{s}/versions` has no `rateLimit`); expect 400 and `jget '.error'` == `set manifest is invalid: ` + `chute-set.py check` output; `ACCEPT` rows expect 201 from the server and a non-empty producer-only message from `check`. Print `CONFORMED_TO_CHUTE`.
  - **s14 null-case control** — plain zip without manifest pushed as a normal file → 201, `set_item_count === 0`, *Install* shown, no *Gallery*; the s10 set zip pushed as `x.apk` via plain `chute-push <file>` → rc 1 with `APK could not be parsed`; `chute-push --set ... --name x.APK` → rc 2 (usage, before network).
  - **s15 byte-identity (optional)** — with `CHUTE_SET_BIN`: image members only, TSV outside `dir`, rows in sorted order, no `--cover`; `chute-set.exe -title-from` vs `chute-set.py --manifest-out` → manifests `cmp`-equal (extract `.chute/set.json` from chute-set's zip with python `zipfile`). Without it: a loud `SKIP: s15 (set CHUTE_SET_BIN to cmd/chute-set)` line.
  - **s16 size pre-check** — `CHUTE_MAX_UPLOAD_BYTES=1000` → rc 3 and no request reached chuted (assert via `CHUTE_URL=http://127.0.0.1:1` and absence of `cannot reach`).
  - **word gate** — `wc -w < SKILL.md` ≤ 500 (body as the spec counts it: everything after the closing frontmatter `---`; state the counting rule in a comment). This one is red until Task 4.
- [x] **Step 3: Run the harness** — with s13 only: all green (s1–s9 unchanged), s13 red-checked by mutation (see the commit body).
- [x] **Step 4: Commit** `test(chute-push): error-text parity corpus, raw-set maker and s13`. The s10–s12/s14–s16 bullets above are carried out as Task 3 Step 0; the word gate as Task 4 Step 1.

### Task 3: `chute-push --set` (Rollout step 6)

**Files:**
- Modify: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-push`

- [ ] **Step 0: Write s10–s12 and s14–s16** exactly as listed in Task 2 Step 2, placed before s13 (numeric order; s13's `ensure_paired` then reuses s10's token). Run the harness and record it **red at s10** (`--set` is an unknown flag → usage) with s1–s9 green. Each later scenario's red is shown by temporarily running it alone (trimmed scratch copy of the harness, as Task 2's s13 red-checks did), not by a commit.
- [ ] **Step 1: Implement** per the spec's "`chute-push` (bash)" section: flags `--set --items --cover --type --name`; usage text gains the second synopsis line; `--set` + positional or set-only flags without `--set` → usage (exit 2); `--name` ending in `.apk` (any case) → exit 2 with the spec's message; `SCRIPT_DIR`; `command -v python3 || die "--set needs python3 (present on acradle guests); single-file pushes still work"`; `SET_TMP` in the EXIT trap; build before pairing; propagate python's exit 3; size pre-check against `${CHUTE_MAX_UPLOAD_BYTES:-536870912}` → exit 3; `json_num` (`grep -o '"KEY":[0-9]*'`); success/replay lines with `(N items)` and the replay "ignored" note; `deep_link set ID` → `[Open](chute://version/ID) · [Gallery](chute://set/ID)`, chosen by `count > 0`; tripwire A (set mode, `count` empty/0 → exit 1 "drifted, report"); tripwire B (400 containing `set manifest is invalid` → exit 1, body verbatim, `CONFORMED_TO_CHUTE=<sha>` read from `chute-set.py` with `sed -n`, "do not retry"). Header comment: exit 3 meaning (D8). No python in a heredoc (D3).
- [ ] **Step 2: Harness green**, full run, every `ok:` including s10–s16 (s15 run once with and once without `CHUTE_SET_BIN`); only the word gate may still be red — if so, keep it red-by-design for Task 4 and say so, or order Task 4 before this commit's final run. Record real output and wall time.
- [ ] **Step 3: Red-checks** (spec "Red-check discipline"): break `chute-set.py` three ways — emit `"Title"`, omit the manifest entry, write a directory entry — and watch s10/s13 (and tripwire A for the omitted manifest) go red; plus comment out the builder call once and watch s12 fail with `cannot reach`; plus make `deep_link set` print `Install` and watch s10 fail. Restore after each. Record each in the commit body.
- [ ] **Step 4:** `bash -n chute-push`; `grep -n 'chute://'` shows hits only inside `deep_link()` and comments.
- [ ] **Step 5: Commit** `feat(chute-push): --set pushes a directory as one chute artifact set`.

### Task 4: `SKILL.md` + `reference.md` + word gate (Rollout step 7)

**Files:**
- Modify: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/SKILL.md`
- Create: `plugins/acradle-vm/skills/pushing-artifacts-to-phone/reference.md`

- [ ] **Step 1:** Word gate (deferred from Task 2: write it now, per Task 2 Step 2's last bullet) — confirm red after adding the sets paragraph and *before* moving the Manual API table.
- [ ] **Step 2:** Move the Manual API table to `reference.md` (linked by one line), add the spec's "Several related files" paragraph, *Gallery* in "Relaying deep links" with per-link floors (*Open*/*Install* chute 0.1.4+, *Gallery* 0.1.17+), frontmatter description gains "a gallery or set of several screenshots or images, before/after, comparison". Do **not** promise the notification opens the gallery (it lands on `chute://version/<id>`). All `CLAUDE.md`-mandated trigger words stay — add a harness assertion that greps each one in the frontmatter (red-check by deleting one).
- [ ] **Step 3:** Harness green including the word gate; `claude plugin validate .` clean (if the CLI is available on the host; otherwise record that it was not run).
- [ ] **Step 4: Commit** `docs(skill): teach artifact sets; move the manual API to reference.md`.

### Task 5: `docs/vm-testing.md` (Rollout step 8)

**Files:**
- Modify: `docs/vm-testing.md`

- [ ] **Step 1:** §0: install path via the public marketplace (`claude plugin marketplace add alshain/acradle-setup` + `claude plugin install acradle-vm@acradle-setup`); the host-mount route kept, labelled "only for testing an unpushed version".
- [ ] **Step 2:** §3 gains a by-hand set push, marked **human-only**: one real set from a fresh VM to production; tap *Gallery*; notification says "N items"; cover/order as captioned; a `.md` member opens with another app.
- [ ] **Step 3:** §5 (shellcheck spot-check) gains: run shellcheck on the final `chute-push` in a guest and keep it clean at default severity (spec "In-guest shellcheck").
- [ ] **Step 4: Commit** `docs(vm-testing): set push by hand and the public install path`. (Docs only: no red test; the harness is re-run anyway and its tail recorded.)

### Task 6: version bump 0.1.0 → 0.2.0 (Rollout step 9)

**Files:**
- Modify: `plugins/acradle-vm/.claude-plugin/plugin.json` (`version`), `.claude-plugin/marketplace.json` (`metadata.version` and `plugins[0].version`)

- [ ] **Step 1:** Add a harness (or a tiny `tests/` check) assertion that the three version strings are equal; red-check by bumping only one.
- [ ] **Step 2:** Bump all three to `0.2.0`; `claude plugin validate .` if available.
- [ ] **Step 3: Commit** `chore(plugin): acradle-vm 0.2.0 — chute artifact sets`.

**Stop here.** Report to the human: commit list, harness tail, unit tail, the guest 3.10 result, s15 run/skip, and that step 10 (`git push origin main`) awaits their go — with the open question of who owns the 4 unpushed shellcheck commits that would ride along.

### Task 7: acradle-side docs (after step 10 only; separate repo, `dev` branch)

Gated on the push: until then the docs would describe a flag no VM can have.

- [ ] acradle `AGENTS.md` chute section: the set command in host form (`HOME="$PWD"` from the main checkout); worktree sessions build with `python3 ../acradle-setup/plugins/acradle-vm/skills/pushing-artifacts-to-phone/chute-set.py --out set.zip <dir>` then the one sanctioned `curl -T`; links now render as *Open*, *Install* **or *Gallery***; a pre-0.2.0 VM answers `--set` with a usage error — push single files there.
- [ ] acradle `docs/gaps.md`: one line — the ensure-latest plugin gap now has a concrete consequence (long-lived VMs never get `--set`).
- [ ] One `docs:` commit on acradle `dev`.

### Task 8: human-only and cross-repo follow-ups (not agent work; listed so they are not lost)

- [ ] `docs/vm-testing.md` §3 set push against production (human; never from an agent session).
- [ ] chute changes 1–5 from the spec's "Changes needed in chute" (separate repo, separate owner).
- [ ] Spec open questions: notification target, golden corpus, ensure-latest installs.
