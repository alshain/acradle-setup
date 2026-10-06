---
name: pushing-artifacts-to-phone
description: Use when a build artifact or file — an APK, image, screenshot, document, specification, report, or binary — needs to reach the user, or when the user asks to send, push, or deliver a file or artefact to them or their phone to install or preview (via chute) — including a gallery or set of several screenshots or images, before/after, or a comparison.
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

**chute is the only sanctioned egress for artifacts.** Never upload user
files or build outputs to public file hosts, pastebins, or any ad-hoc
third-party service, and never repurpose repo credentials (e.g. `gh
release`) as a delivery channel. If `chute-push` fails, report the
failure to the user — do not improvise another route.

## Choosing a stream

| Stream | Use for |
|---|---|
| `prod-apk` | Release/main builds of the project's app |
| `test-apk` | PR, experiment, and debug builds — never `prod-apk` |
| `screenshots` | Images the user should look at |
| `docs` | Documents, specifications, reports |

New kind of artifact → pick a new stable stream name and keep using it.
Streams are created on first upload.

## Several related files: push one set, not N files

Before/after screenshots, every screen of a flow, a render comparison, a
report with its figures → one set: one notification, one swipeable gallery,
one `--notes` for the whole. Never an APK in a set (it must be installed).
Put the files flat in a directory; optionally write `items.tsv`
(`filename<TAB>title<TAB>description`, row order = display order, first row
or `--cover` = cover). Then:

```bash
bash "<this skill's directory>/chute-push" --stream screenshots --set ./renders --items ./renders/items.tsv --notes "<what the set is>"
```

Only images render inline; other files open in another app. Max 100 files,
64 MiB each. Unknown extensions are refused: rename or add
`--type ext=mime/type`. **Exit 3 means your inputs were rejected before
anything was sent — fix and re-run.**

## First push: pairing (one phone tap)

With no token (`~/.config/chute/token`) the script requests pairing,
prints a 4-digit code, and waits for phone approval.

**Relay the pairing code to the user verbatim in your reply** — the user
must approve only the phone card showing that exact code. A timed-out wait
is not a failure: the code stays valid 24 h and a re-run resumes it.

## Relaying deep links

Every successful push prints a `DEEP LINKS` block. **Relay it verbatim, as
markdown** — the user taps *Open*, *Install* or (for a set) *Gallery*
without waiting for the notification.

Never show or compose a raw `chute://` URL — a bare URL in your reply is a
defect. Copy the line the script printed, so the links render as words.

*Open*/*Install* need chute 0.1.4+, *Gallery* 0.1.17+, on the same phone
the notification reaches, so relaying them is never wrong. The notification
itself opens the version screen, not the gallery.

## When the script can't run

The raw HTTP API, and building a set by hand, are in
[reference.md](reference.md) in this skill's directory.
