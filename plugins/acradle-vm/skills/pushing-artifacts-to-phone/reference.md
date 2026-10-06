# chute manual API (when the script can't)

Use `chute-push` whenever it runs; this is the fallback. Base `$CHUTE_URL`
(default `https://chute.vqrs.ch`); the token file (`~/.config/chute/token`)
is sent as `Authorization: Bearer`.

```
POST /v1/pair                 {"proposed_name":"<project>"} → {code, poll_secret, expires_at}
GET  /v1/pair/{code}          header X-Chute-Poll-Secret: <poll_secret>
                              202 pending | 200 {token} | 403 denied | 410 expired
PUT  /v1/streams/{s}/versions?filename=<basename>&branch=&commit=&version=&notes=
                              curl -T <file>; 201 new | 200 same bytes already exist
GET  /v1/signing-key          needs 'sign' capability; PKCS#12 keystore for Android updates
```

**A set by hand.** Build the zip with the network-free builder in this
skill's directory, then upload it with the same `PUT` as any file:

```bash
python3 "<this skill's directory>/chute-set.py" --out renders.zip [--items items.tsv] ./renders
```

The response's `set_item_count` is the number of items the server read; `0`
means it stored a plain zip, not a set — report that, do not retry. Exit 3
from the builder means the inputs were rejected and nothing was written.
