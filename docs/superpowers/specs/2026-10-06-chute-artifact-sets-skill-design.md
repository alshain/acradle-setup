# chute artifact sets from the acradle-vm plugin

**Status:** approved design, 2026-10-06 (judge panel + two adversarial reviews; review findings folded in). Target file: `docs/superpowers/specs/2026-10-06-chute-artifact-sets-skill-design.md` (acradle-setup).
**Supersedes nothing; extends** `2026-08-08-chute-push-skill-design.md` (adds a `--set` mode) and the deep-links change to `chute-push` (committed as `5edcd2f`, Rollout step 1).
**Checked against chute `main` @ `50322ea`** (2026-09-28) before commit; two corrections from that check are folded in below (s13 reads the JSON body, not `X-Chute-Error-Detail`; blank-title/content_type checks mirror Go's `strings.TrimSpace`, not Python's `str.strip`).

## Goal

An agent inside an acradle VM pushes several related files (before/after screenshots, every screen of a flow, a report plus its figures) as **one** chute artifact set: one upload, one notification, one swipeable gallery on the phone, one relayed *Gallery* link. The agent needs only what the guest already has.

A set is, on the wire, an ordinary `PUT /v1/streams/{s}/versions` of a zip carrying `.chute/set.json` (chute `internal/set`, 2026-09-14). Nothing changes in auth, pairing, streams, provenance or the upload route.

## Non-goals

- Changing chute's wire format, routes or notification target. The FCM notification for a set still deep-links to `chute://version/<id>`; that is a chute decision (open question).
- Per-item `chute://set/<id>?item=<id>` links. The agent cannot know which member the user wants; the *Gallery* link opens at the cover. Deferred with chute's own followups.
- Nested directories, symlinks, archives-of-archives, "Vs" ratings mode.
- Updating plugins on existing VMs (install is ensure-present; `docs/gaps.md` ~453 in acradle).
- Linting the repo's own scripts host-side (shellcheck spec §6) — but see Testing for the one in-guest run.

## Decisions

### D1. The producer is a python3-stdlib script shipped in the skill, invoked by `chute-push --set`

The two judges split: one chose this design, the other chose server-distributed `cmd/chute-set`. Resolution: **python3 builder**, for these reasons, each verified rather than taken from the research.

1. **No new trust edge.** The server-distribution design has every VM download and *execute* a binary from chute.vqrs.ch with TLS as the only authenticity check. Its claimed precedent is wrong: `/bootstrap/client.apk` is gated by `checkBootstrap(code)` (`handleBootstrapClientAPK` → `checkBootstrap`, `internal/api/bootstrap.go`), so the tools route would be chute's first unauthenticated executable download. The VM is a containment boundary; turning the artifact *sink* into a code *source* for a convenience feature is the wrong direction, and it conflicts with acradle's deferred SNI-allowlist hardening.
2. **Shippable from one repo.** The python design lands with a push of `alshain/acradle-setup`. The alternative needs chute code, a Dockerfile change, an image publish and a production refresh before any `--set` works, and three repos in lockstep forever (a frozen `chute-set` CLI contract).
3. **python3 is already load-bearing in every guest.** `C:/Projects/acradle/internal/worker/claudedefaults.go:77` and `rcsession.go:233` shell to it; cloud-init needs it. Live probe: 3.10.12 with `zipfile`/`json`. `zip`, `go`, `jq`, `gh` are absent; `node` is not guaranteed under the aiproxy cloud-init.
4. **Pure bash cannot do it honestly**: CRC-32 (`cksum` uses a different polynomial), little-endian headers, and correct JSON escaping of arbitrary UTF-8 would be ~150 lines of unreviewable binary bash.

**Stated exception to the dependency rule.** acradle-setup `CLAUDE.md` "Target environment" and chute-push spec §5 say "bash + curl + git only, no jq". Amend to: "…; `chute-push --set` additionally needs python3 ≥ 3.10 **stdlib only** (zipfile, json), which acradle provisioning already requires. Single-file pushes never need it." A VM without python3 loses sets with a clear message, never single-file pushes.

**Rejected alternatives**
- *Server-distributed `cmd/chute-set`* (above). Recorded as the **escalation path**: if the conformance harness catches drift twice (AGENTS.md: "same mistake twice → fix the rule"), revisit it, with the tools route bearer-gated and the build moved after pairing.
- *Binary committed into the public plugin repo*: chute is private with no releases; a 3 MB linux/amd64 blob in a skills repo, lagging the server, exec bit via git. Worse than both.
- *`go run`* (no Go in guests), *node* (not guaranteed).
- *Pure bash* (above).

### D2. Format ownership: chute `internal/set` is the authority; `chute-set.py` is a declared mirror, kept honest mechanically

chute spec §9's "one owner for the format, both directions" is read as **one authority plus a mechanical check**, not "one binary". The mirror:
- carries a header `CONFORMED_TO_CHUTE = "<sha>"` naming the chute `main` commit whose `set.go`/`archive.go` it mirrors, bumped on every harness run against a newer chuted;
- reproduces every Decode/Parse rule with the **server's exact error strings** (so parity can be asserted);
- is tested four ways (see Testing): real-chuted acceptance + stored-manifest read-back, error-text parity per rule, optional byte-identity against `cmd/chute-set`, and two runtime tripwires in `chute-push`.
- Drift therefore fails loud (exit 1 "report this"), never as a silent plain-zip upload.

We ask chute to publish a golden corpus under `internal/set/testdata/` (Changes needed in chute) so the oracle itself becomes chute-owned.

### D3. Keep `chute-push` as the single entry point; bash orchestrates, python only builds

`--set` is a mode of `chute-push`, not a sibling command: pairing, token, 401 re-pair-once, provenance, `--wait`, `CHUTE_URL`, `CONFIG_DIR`, and the DEEP LINKS block stay in one owner; the host-side `HOME="$PWD"` lever keeps working. `chute-set.py` has no network, no HOME, no token, so worktree sessions (which cannot run `chute-push`) can still `python3 <skill>/chute-set.py --out set.zip dir` and do the one sanctioned `curl -T`. No python in a bash heredoc (the bash 5.3 hang `inject-rules` works around).

### D4. Deterministic archives

Fixed `(1980,1,1,0,0,0)` timestamps, fixed entry order, Deflate everywhere, no extra fields → an unchanged directory re-pushed is sha-identical → chute's idempotent 200 replay, no duplicate version, no second notification. Rejected (agent-ux): Store-vs-Deflate by extension — breaks replay across plugin versions for no need. Note: determinism holds within one zlib version, i.e. within one guest image, which is the case that matters.

### D5. Display order vs ids

- **Display order** (grafted from agent-ux/minimal-deps): rows of `--items` in file order first, then uncaptioned files sorted by name; `--cover NAME` hoists one member to `items[0]`. Agents write captions to tell a story; forcing `01-` filename prefixes is worse UX.
- **Ids are derived in sorted-name order regardless** (exact `cmd/chute-set` algorithm), so ids never move when captions are reordered — deep links and a future Vs mode key off them.
- Deviation from `cmd/chute-set` (always sorted) is documented; the byte-identity tier runs with a TSV in sorted order and no `--cover`, where both producers must agree.

### D6. Content types from a fixed table; `--type` is the escape hatch

`mimetypes`/`/etc/mime.types` differ per guest (`.webp` is absent from 3.10's built-ins), so the script consults **only** its own table (png jpg jpeg gif webp svg bmp heic pdf txt md html htm json csv tsv log xml yaml yml zip tgz tar.gz mp4 webm mp3 wav) plus `--type EXT=MIME` (repeatable). Unknown → exit 3 naming the file. Never `application/octet-stream` by guess (`cmd/chute-set` parity). Rejected: a 4th TSV column (keeps the TSV byte-compatible with `chute-set -title-from`); `mimetypes` fallback (re-adds environment drift).

### D7. Gallery vs Install is decided by the server's `set_item_count`, not by mode

`deep_link set <id>` prints `[Open](chute://version/ID) · [Gallery](chute://set/ID)` (Install degrades to a plain open for a non-APK). `deep_link version <id>` is unchanged. Chosen by `set_item_count > 0` from the response, so a set zip pushed as a plain file still gets *Gallery*, and a plain zip gets *Install* — a null case the harness controls. `deep_link()` remains the only `chute://` writer in the script (per chute `docs/deep-links.md`).

### D8. Exit code 3 = "your inputs were rejected locally; nothing was sent"

Grafted from agent-ux; adopted because it is a useful signal for a headless agent and it is documented in the script header and SKILL.md. Applies to set-input validation only; single-file behaviour (0/2/1, the pre-existing 41 edge) is untouched.

### D9. Plugin version 0.1.0 → 0.2.0

First bump ever. The plugin cache path is version-keyed; it costs nothing and `claude plugin list` in a VM then shows which contract it has. Both `plugin.json` and both entries in `marketplace.json`.

## Components & CLI

All under `plugins/acradle-vm/skills/pushing-artifacts-to-phone/` unless the path starts with `tests/`, `docs/`, `.claude-plugin/` or names another repo (those are repo-root relative).

| File | Change |
|---|---|
| `chute-push` | `--set`, `--items`, `--cover`, `--type`, `--name`; `json_num`; `deep_link` kinds; 400/tripwire handling; exit 3 |
| `chute-set.py` (new, 100755, `#!/usr/bin/env python3`, stdlib only, 3.10-compatible) | builder + validator, standalone |
| `SKILL.md` | sets section, *Gallery*, trigger words; Manual API moved out |
| `reference.md` (new) | the Manual API table |
| `tests/test-chute-push.sh` | new scenarios (incl. the untested DEEP LINKS block) |
| `tests/test-chute-set.py` (new) | `python3 -m unittest`, no network |
| `tests/set-corpus/` (new) | invalid manifests + expected server error text |
| `docs/vm-testing.md` | §3 (chute) gains a by-hand set push; §0 install path updated to the public-marketplace route, host mount kept only for testing an unpushed version |
| `CLAUDE.md` | python3 exception (D1) |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | 0.2.0 |
| acradle `AGENTS.md` (chute section), `docs/gaps.md` | host-side set usage; pre-0.2.0 VM behaviour |

### `chute-push` (bash)

```
chute-push --stream <name> [--version NAME] [--notes TEXT] [--wait SECONDS] <file>
chute-push --stream <name> --set <dir> [--items <tsv>] [--cover NAME] [--type EXT=MIME]...
           [--name <file.zip>] [--version NAME] [--notes TEXT] [--wait SECONDS]
```

- `--set <dir>`: members are the top-level, non-hidden, regular files of `dir` (`os.lstat`); symlinks and subdirectories are skipped with one stderr line each (`chute-push: skipped (symlink|directory): <name>`). `--set` plus a positional, or `--items`/`--cover`/`--type`/`--name` without `--set` → `usage`, exit 2. Missing flag value → existing `die`, exit 1 (unchanged quirk).
- `--items <tsv>`: `filename<TAB>title<TAB>description`, same format as `cmd/chute-set -title-from`, with stricter semantics: a row naming a non-member is an error (below), the filename field is not trimmed, and a 4th+ column is an error; a row with an empty title falls back to the basename exactly as chute-set does; `\r` stripped, blank lines skipped; a row naming a non-member → exit 3 with the line number (chute-set ignores it silently; silent is wrong for an agent that typo'd). Files without a row: title = basename, description empty. If the TSV lives inside `dir` it is excluded from the members.
- `--name`: upload filename, default `$(basename "$(cd "$dir" && pwd)").zip` (so `--set .` never yields `..zip`). Ends in `.apk` (any case) → exit 2 "a set must not be named .apk: the server would run its APK parser first" (verified: the `application/vnd.android.package-archive` case in `internal/api/upload.go` is exclusive and first).
- `--notes` is the set's overall description; the manifest deliberately has none (chute spec §3).
- `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`; `command -v python3 || die "--set needs python3 (present on acradle guests)"`; `SET_TMP="$(mktemp -d)"` added to the EXIT trap; `python3 "$SCRIPT_DIR/chute-set.py" --out "$SET_TMP/$NAME" … "$DIR"`; python's exit 3 propagates as exit 3. The zip is built **before** any network or pairing, so a bad set never costs a phone tap, and the 401 re-pair retry re-sends the same file.
- Then `FILE="$SET_TMP/$NAME"` and the existing pair → upload → re-pair-once flow runs untouched.
- `upload()` 200/201: `count="$(json_num "$resp" set_item_count)"` (`grep -o '"KEY":[0-9]*'`, sibling of `json_field`, because the value is a JSON number). Success line: `chute-push: uploaded set '<name>' (<N> items) to stream '<s>': <json>`; replay line gains `(N items)`. DEEP LINKS uses `deep_link set` when `count > 0`, else `deep_link version`.
- Tripwire A: `--set` and `count` empty or 0 → exit 1 `server stored '<name>' as a plain artifact, not a set (set_item_count=0) — chute-set.py and chute have drifted; report this`.
- Tripwire B: HTTP 400 whose body contains `set manifest is invalid` → exit 1, body verbatim, `this is a producer bug (chute-set.py disagrees with the server, CONFORMED_TO_CHUTE=<sha>); do not retry the same inputs, report it`. The blob was reclaimed server-side; no version exists.
- Local size pre-check: zip larger than `${CHUTE_MAX_UPLOAD_BYTES:-536870912}` → exit 3 (same env name as the server's, so an operator with a raised cap sets one var; the server's 413 remains the authority).
- `deep_link KIND ID`: kinds `version`, `set`, and `approvals`. `version` and `approvals` landed in step 1 (`5edcd2f`: the banner literal `[Approve on phone](chute://approvals)` is now `deep_link approvals`, and an unknown kind dies), so the "only writer" invariant already holds; step 6 adds `set`.

### `chute-set.py` (python3)

```
chute-set.py --out OUT.zip [--items TSV] [--cover NAME] [--type EXT=MIME]... [--manifest-out FILE] DIR
chute-set.py check FILE.zip          # run the Parse mirror on any zip; prints server-wording errors (test hook)
```
Exit 0 / 3 (inputs rejected, message `chute-set: item N (<file>): <server wording>` or `chute-set: <file>: …`) / 1 (I/O, or a failed self-check, which is a producer bug) / 2 (usage, e.g. a malformed `--type`). For an item rule, `<server wording>` is the server's message after its `item N: ` prefix, so `item 3: title is required` reads `chute-set: item 3 (b.png): title is required`; `check` prints the server's message unsplit. A missing `DIR` or unreadable `--items` is an input rejection (3), not I/O. Members also exclude `--out` and `--manifest-out` when they live inside `DIR` (so a re-run never packs its own previous zip), and any other non-regular file is skipped with `skipped (not a regular file): <name>`. A TSV row naming a file already listed is exit 3 (which row's order would win is ambiguous). Output is UTF-8 with LF line ends on every host. Stdout on success: one line per item `  <file>  id=<id>  "<title>"` with `(opaque on the phone: opens with another app)` appended for non-`image/*` members, then `chute-set: N items, <bytes> bytes`.

## Manifest / zip construction

**Manifest**: dict in insertion order `chute_set` (int 1), `items[]` of `id, path, title, description, content_type` — `description` always present (empty string, as Go's non-omitempty marshal emits), keys literal lowercase in one constant `MANIFEST_KEYS` (the phone's kotlinx decoder is case-sensitive; Go's is not, so nothing on the wire catches a mis-cased key). `json.dumps(m, ensure_ascii=False, separators=(",", ":"))`, then Go's `json.Marshal` escapes applied for byte-identity (`<`→`\u003c`, `>`→`\u003e`, `&`→`\u0026`, U+2028/9), UTF-8 encoded. Titles/descriptions decoded `utf-8`/`strict` from the TSV (bad byte names the line); a filename that is not valid UTF-8 (surrogate-escaped) → exit 3 "rename the file". `--manifest-out` writes the exact bytes for the harness.

**Ids**: `re.sub(r'[^A-Za-z0-9._-]', '_', name)[:64]`, deduped `-2, -3…` with chute-set's truncation rule, empty → `item`; computed over the sorted-name list (D5). **Path** = bare basename.

**Zip** (`zipfile`): write to `OUT.zip.tmp-<pid>`, `os.replace` only after the self-check (fixes chute-set's truncate-before-validate flaw; nothing is left on failure). Entry 0 is `.chute/set.json` via `ZipInfo(date_time=(1980,1,1,0,0,0))`, `compress_type=ZIP_DEFLATED`, `create_system=3`, `external_attr=0o100644 << 16` (Go's `unixModeToFileMode` → regular; `ZipFile.getEntry` happy). Members in display order with the same header settings, streamed in 1 MiB chunks through `zf.open(zi, "w")`. No directory entries, no symlinks, no encryption, no comments, default `allowZip64` (never triggered under the caps).

## Validation & errors

**Layer 1 — inputs, before writing (exit 3, names file/row, server wording)**: 1–100 files (`a set needs at least one item` / `a set holds at most 100 items`); title non-blank after Go's `strings.TrimSpace` — mirrored with Go's `unicode.IsSpace` set, **not** Python's `str.strip()`, which also strips U+001C–U+001F and would reject a title the server accepts — and ≤120 code points (`len(str)` ≡ `RuneCountInString`); description ≤2000; basename ≤1024 bytes, no backslash, not `..`-shaped; content_type resolved (D6); per-file `st_size` ≤ 64 MiB and running total ≤ 1 GiB (what the archive will declare); serialized manifest `< 262144` bytes (`manifest is larger than 262144 bytes`). content_type non-blank by the same `TrimSpace` rule. **Every rule message is the server's exact string**, including its `item %d:` index (split around the file name on the build path, as above), where the index is the **0-based manifest position** (display order), as `set.Decode` counts; an agent hint ("split into several sets", "shorten captions") goes on a second line prefixed `hint:`, printed only by the build path, never by `check`.

**Layer 2 — self-check on the written archive (mirror of `set.Parse`, in `check` too)**: ≤10000 central-directory entries; no duplicate names; `.chute/set.json` present, `< 262144` bytes when read, round-trips through the same `validate_manifest()`; every item path has a byte-equal entry; an entry is non-regular exactly when Go's `FileHeader.Mode()` would say so: for a unix or macOS creator (`create_system` 3 or 19), `unixModeToFileMode` — `(external_attr >> 16) & 0o170000` is DIR/LNK/BLK/CHR/FIFO/SOCK (a zero type field is regular); for a FAT/NTFS/VFAT creator (0, 11, 14), `msdosModeToFileMode` — the MS-DOS directory bit `0x10`; any other creator is regular; and in every case a name ending in `/` is a directory. (Checked against Go's `archive/zip/struct.go`: the unix-only reading was incomplete.) The producer still always writes `0o100644`; `compress_type in (0, 8)`; `flag_bits & 0x1 == 0`; declared sizes within caps. Rules the server does not enforce but the producer and phone rely on (exact lowercase keys, each exactly once, string values; members neither encrypted nor compressed other than store/deflate) are reported by `check` as `producer-only: …`, never in server wording; an encrypted manifest is producer-only too, because Go ignores the flag and the server's outcome is undefined. `check` on a zip without a manifest prints chute's own `ErrNotASet` text, `archive carries no set manifest`. Parse's two path-quoting messages (`item %d: no archive member named %q`, `item %d: %q is not a regular file`) use Go's `%q` (`strconv.Quote`: `\"`, `\\`, `\n`-style escapes, `\x`/`\u` for non-printable runes, printable non-ASCII kept literal), not Python `repr()`; the mirror carries a small `go_quote()` and the unit tests pin it.

**Agent-visible table**

| Situation | Behaviour | Exit |
|---|---|---|
| python3 missing | "`--set` needs python3; single-file pushes still work" | 1 |
| empty dir / >100 / unknown ext / bad caption / oversize / bad TSV row | server-wording message naming the file; nothing sent | 3 |
| zip > `CHUTE_MAX_UPLOAD_BYTES` (default 512 MiB) | local, before upload | 3 |
| `--set` + positional, `--name *.apk`, `--items` without `--set` | usage | 2 |
| server 400 `set manifest is invalid` | body verbatim, "producer bug, report" | 1 |
| 201/200 with `set_item_count` 0 in set mode | tripwire, "drifted, report" | 1 |
| 401 / 413 / unreachable / pairing | unchanged shared paths | as today |
| unchanged re-push | 200 replay, same links, no new notification; `--version`/`--notes` are ignored (the server keeps the existing row); chute-push says so and that only a changed file or caption re-notifies | 0 |

## Deep links

`deep_link set ID` → `[Open](chute://version/ID) · [Gallery](chute://set/ID)`; relayed inside the existing `DEEP LINKS (relay to the user verbatim, as markdown — never the raw URL):` block. No Install for a set. No per-item links (Non-goals). `deep_link()` stays the only `chute://` writer in bash; chute's `docs/deep-links.md` lists it as one of two owners of the grammar and must be updated (Changes needed in chute). The push notification still lands on `chute://version/<id>` (version screen with its summary card); SKILL.md must not promise the notification opens the gallery.

## SKILL.md changes

Body is 428 words against the 500 gate. Move the Manual API table (~80 words) to `reference.md`, linked by one line; the harness gets `wc -w` ≤ 500 as a mechanical assertion. Add (~110 words):

> **Several related files: push one set, not N files.** Before/after screenshots, every screen of a flow, a render comparison, a report with its figures → one set: one notification, one swipeable gallery, one `--notes` for the whole. Never an APK in a set (it must be installed). Put the files flat in a directory; optionally write `items.tsv` (`filename<TAB>title<TAB>description`, row order = display order, first row or `--cover` = cover). Then `bash "<this skill's directory>/chute-push" --stream screenshots --set ./renders --items ./renders/items.tsv --notes "<what the set is>"`. Only images render inline; other files open in another app. Max 100 files, 64 MiB each. Unknown extensions are refused: rename or add `--type ext=mime/type`. **Exit 3 means your inputs were rejected before anything was sent — fix and re-run.**

"Relaying deep links" gains *Gallery* and states the floor per link: *Open*/*Install* need chute 0.1.4+, *Gallery* 0.1.17+ (`chute://set` routing landed 2026-09-14). Frontmatter description gains "a gallery or set of several screenshots or images, before/after, comparison". All mandated trigger words stay.

## Testing

**Unit** (`tests/test-chute-set.py`, no network): id derivation cases lifted from chute `cmd/chute-set/main_test.go` (sanitize, 64-char truncation, `-2` dedupe with truncation, emoji → `__`); TSV parsing and the missing-file row; ordering (TSV order then sorted; `--cover`; ids independent of caption order); MIME table precedence and the unknown-extension error; every Layer-1 rule → expected message; manifest bytes: exact key set, `description` always present, `chute_set` is int 1, `<>&"` and newline escaping; determinism (build twice → identical bytes); atomicity (failing build leaves no `OUT.zip`); `check` catches a hand-corrupted archive (symlink entry, dir entry, encrypted flag, mis-cased `Title`). Each red-checked by breaking the rule it pins.

**Harness** (`tests/test-chute-push.sh`, unchanged invocation `CHUTED_BIN=… ./tests/test-chute-push.sh`; chuted built from chute `main` at/after the sets commit; now also needs python3 on the host; fixtures are 1×1 PNGs via `base64 -d`; chuted's per-IP pair limiter is already handled inside the harness by a per-request `X-Forwarded-For` curl wrapper, `b6f0055`, so the new scenarios' pairings and polls need no external shim):
- **s3 (existing)**: assert `[Open](chute://version/` and `[Install](chute://version/` — pays the deep-links debt; red against origin's script, green with the deep-links change. Done in `5edcd2f`, which also asserts `[Approve on phone](chute://approvals)` in the pairing banner.
- **s10 set push**: 3 PNGs + a `.md`, TSV with UTF-8, `<>&"` and a 120-rune title → rc 0; `uploaded set 'renders.zip' (4 items)`; `jget`: `set_item_count === 4`, `created === true`; `[Gallery](chute://set/<vid>)` present, `Install` absent; `GET /v1/versions/<vid>/set` with the device token `cmp`-equal to `--manifest-out`; node asserts top-level keys exactly `{chute_set,items}` and item keys exactly `{id,path,title,description,content_type}` (the only on-the-wire check for the phone's case-sensitive decoder); order = TSV order then name; cover first. **This is the conformance claim against chute's real parser**: the 201 can only come from `set.Parse` accepting the archive.
- **s11 replay**: same command with a *new* `--version` → `already on the server (idempotent replay)`, same `version_id`, the original `version_name` via `jget`, and the "ignored" line.
- **s12 validation before network**: `CHUTE_URL=http://127.0.0.1:1` with 101 files / `foo.xyz` / empty dir / blank title / missing-file row → rc 3, the rule text, and NOT `cannot reach` (matches both the pairing and the upload wording; s12 runs with a token present). Red-check: comment out the builder call once and watch it fail with `cannot reach`.
- **s13 error-text parity** (`tests/set-corpus/<rule>.json` + `expected.tsv`): `chute_set: 2`, unknown field, mis-cased `Title` (server accepts → asserted as *producer-only*), 0 and 101 items, bad id charset, 65-char id, duplicate id, leading `/`, backslash, `a/../b`, blank title, 121-rune title, 2001-rune description, blank content_type, missing member, directory member, manifest of exactly 262144 bytes. A deliberately unvalidated `tests/make-raw-set.py` wraps each into a zip, curl PUTs it, and the 400 body's `error` field, decoded with `jget` (the body is JSON `{"error":…}` with Go's `<`-style escaping, which `JSON.parse` undoes), must equal `set manifest is invalid: ` + `chute-set.py check` output. (Not `X-Chute-Error-Detail`: chute's `statusRecorder` deletes that header before the response leaves the server — `internal/api/middleware.go`, "never sent to the client".) The corpus covers the 101-items and 262144-byte-manifest rules too. Change any limit or string on either side and this goes red — the drift alarm. Prints `CONFORMED_TO_CHUTE`.
- **s14 null-case control**: plain zip without manifest as a normal file → 201, `set_item_count === 0`, *Install* shown, no *Gallery*; the set zip uploaded under an `.apk` name → 400 `APK could not be parsed` (documents D-name rule).
- **s15 optional byte-identity** with `CHUTE_SET_BIN` (`go build ./cmd/chute-set`): image members only (Go's `mime.TypeByExtension` appends `; charset=utf-8` to text types and resolves `.md` only via the host's `/etc/mime.types`, so text members differ for non-drift reasons), TSV outside `dir`, sorted-order rows, no `--cover`, manifests `cmp`-equal; otherwise a loud `SKIP:` line.
- **s16 size pre-check**: `CHUTE_MAX_UPLOAD_BYTES=1000` → rc 3 before any request.
- `wc -w SKILL.md` ≤ 500.
- Red-check discipline recorded in the plan: break `chute-set.py` three ways (emit `"Title"`, omit the manifest, write a dir entry) and watch s10/s13 go red before restoring.

**In-guest shellcheck**: run once on the final `chute-push` inside a VM (host has no shellcheck) and keep it clean at default severity; host-side lint remains out of scope per shellcheck spec §6.

**By hand** (`docs/vm-testing.md` §3, human-only, marked as such): one real set push from a fresh VM to production; tap *Gallery*; confirm the notification says "N items"; confirm cover/order and that a `.md` member opens with another app.

## Rollout

Order in `C:/Projects/acradle-setup` (`main`), one self-contained commit each:
1. **The pending uncommitted deep-links change goes first, alone**: `chute-push` `deep_link()` + DEEP LINKS block + approvals banner, `SKILL.md` "Relaying deep links", **with** harness assertion s3 and the `approvals` kind folded into `deep_link`. Confirm which session owns it before committing; never stash. The 4 unpushed shellcheck commits ride along at push time (confirm ownership likewise). **Done: `5edcd2f`**, followed by the harness rate-limit fix `b6f0055` (per-request `X-Forwarded-For`), which the new scenarios rely on.
2. This spec + `CLAUDE.md` python3 exception.
3. Plan (`docs/superpowers/plans/`).
4. `chute-set.py` + `tests/test-chute-set.py`.
5. Corpus + `make-raw-set.py` + harness s12–s16 (red until 6).
6. `chute-push --set`, `json_num`, `deep_link` kinds, tripwires, exit 3.
7. `SKILL.md` + `reference.md` + word gate.
8. `docs/vm-testing.md` (§3 set push, §0 install path).
9. Version bump 0.1.0 → 0.2.0 (`plugin.json`, both `marketplace.json` entries).
10. `git push origin main` — VMs clone `alshain/acradle-setup@main`; nothing reaches a guest before this, and only **new** VMs get it.

`C:/Projects/acradle` (`dev`): `AGENTS.md` chute section gains the set command in host form (`HOME="$PWD"` from the main checkout), the note that worktree sessions build with `python3 <skill>/chute-set.py` then the single sanctioned `curl -T`, that links now render as *Open*, *Install* **or *Gallery***, and what an agent on a pre-0.2.0 VM does when `--set` is `unknown flag` (push single files; the VM predates the plugin). `docs/gaps.md`: one line that the ensure-latest gap now has a concrete consequence.

## Changes needed in chute (separate, cross-repo; none blocks this work)

1. `docs/superpowers/specs/2026-09-14-chute-artifact-sets-design.md` §9: the path is `cmd/chute-set`, not `scripts/chute-set`; add that the agent producer is acradle-setup's `chute-set.py`, a mirror conformance-tested against `cmd/chuted`.
2. `docs/deep-links.md` ~line 107: "Nothing in the product mints a chute://set link yet" → `chute-push --set` mints `chute://set/<versionId>`; the FCM notification still targets `chute://version/<id>`. Close the matching followups item.
3. **Requested**: publish `internal/set/testdata/` golden valid and invalid manifests with expected error strings, consumed by `set_test.go` and by the plugin harness, so chute owns the oracle and s13 stops being a hand-kept table.
4. Optional: `cmd/chute-set` fixes — `os.Create` truncates `-o` before validation; `-order tsv` opt-in so both producers can agree on display order; note that its output is deterministic only insofar as Go's zip writer omits timestamps for a zero `Modified` (judge 1's non-determinism claim was not verified and is not load-bearing here).
5. Decision: should a set's notification deep-link to `chute://set/<id>`?

## Open questions

- Does chute want the notification to open the gallery (`chute://set/<id>`) rather than the version screen? Affects what SKILL.md promises on tap.
- Will chute publish the golden corpus (item 3 above)? Until then s13 is maintained by hand and pins chute's wording.
- The deep-links edit was committed by this work (`5edcd2f`) on instruction, without a separate ownership check. Who owns the 4 unpushed shellcheck commits that will ride along at push time (step 10) is still open.
- Should acradle grow ensure-latest plugin installs so long-lived VMs receive 0.2.0, or is "new VMs only" acceptable for this feature?
