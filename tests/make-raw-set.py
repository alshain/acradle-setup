#!/usr/bin/env python3
"""make-raw-set.py: wrap a manifest file into a zip, deliberately unvalidated.

Test fixture maker for the harness's error-text parity scenario (s13). It does
none of chute-set.py's checking: whatever bytes the manifest file holds become
.chute/set.json verbatim, so a corpus entry that breaks exactly one chute rule
reaches the server with that rule broken. Never use it to produce a real set.

  make-raw-set.py --out OUT.zip --manifest FILE
                  [--member NAME]... [--dir-member NAME]... [--pad-to N]

  --member      add a regular member NAME (small dummy bytes); repeating a
                name writes a duplicate entry, on purpose
  --dir-member  add an entry NAME whose unix mode says "directory" (no
                trailing slash, so only the mode bits make it non-regular)
  --pad-to N    append spaces to the manifest until it is exactly N bytes
                (JSON allows trailing whitespace; the size rule fires first)

The manifest is entry 0; members follow in argument order (members, then
directory members). Python >= 3.10, standard library only.
"""

import argparse
import sys
import warnings
import zipfile

EPOCH = (1980, 1, 1, 0, 0, 0)


def _info(name, mode):
    zi = zipfile.ZipInfo(name, date_time=EPOCH)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = mode << 16
    return zi


def main(argv=None):
    p = argparse.ArgumentParser(prog="make-raw-set.py")
    p.add_argument("--out", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--member", action="append", default=[])
    p.add_argument("--dir-member", action="append", default=[])
    p.add_argument("--pad-to", type=int)
    a = p.parse_args(argv)

    with open(a.manifest, "rb") as f:
        manifest = f.read()
    if a.pad_to is not None:
        if len(manifest) > a.pad_to:
            p.error("manifest is already %d bytes, more than --pad-to %d" % (len(manifest), a.pad_to))
        manifest += b" " * (a.pad_to - len(manifest))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on a duplicate name; that is the point
        with zipfile.ZipFile(a.out, "w") as zf:
            zf.writestr(_info(".chute/set.json", 0o100644), manifest)
            for name in a.member:
                zf.writestr(_info(name, 0o100644), ("member %s\n" % name).encode("utf-8"))
            for name in a.dir_member:
                zf.writestr(_info(name, 0o040755), b"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
