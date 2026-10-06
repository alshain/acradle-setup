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

## First push: pairing (one phone tap)

With no token (`~/.config/chute/token`) the script requests pairing,
prints a 4-digit code, and waits for phone approval.

**Relay the pairing code to the user verbatim in your reply** — the user
must approve only the phone card showing that exact code. A timed-out wait
is not a failure: the code stays valid 24 h and a re-run resumes it.

## Relaying deep links

Every successful push prints a `DEEP LINKS` block. **Relay it verbatim, as
markdown** — the user taps *Open* or *Install* and lands in the app without
waiting for the notification.

Never show the raw `chute://` URL. The links must render as the words *Open*
and *Install*; a bare URL in your reply is a defect, not a style choice. Never
compose one yourself either — copy the line the script printed.

The links only work on a phone with chute installed (0.1.4 or newer), which is
the same phone the notification would reach, so there is no case where relaying
them is wrong.

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
