#!/usr/bin/env python3
"""chute-set.py: build (and check) a chute artifact set, offline.

A chute artifact set is an ordinary zip carrying a .chute/set.json manifest
(chute internal/set). This script turns a flat directory of files into such a
zip, deterministically, and self-checks the result before keeping it. It has
no network, reads no HOME, no token and no environment: chute-push --set calls
it, and a session that cannot run chute-push can call it directly and upload
the zip with one curl -T.

Usage:
  chute-set.py --out OUT.zip [--items TSV] [--cover NAME] [--type EXT=MIME]...
               [--manifest-out FILE] DIR
  chute-set.py check FILE.zip

  DIR        members are its top-level, non-hidden, regular files; symlinks
             and subdirectories are skipped with one stderr line each.
  --items    filename<TAB>title<TAB>description rows; row order is display
             order; an empty title means "use the file name"; a row naming
             a file that is not a member is an error. A TSV inside DIR is not
             a member, nor are --out and --manifest-out.
  --cover    hoist this member to the front (the set's cover).
  --type     teach one extension a content type (repeatable); only the
             built-in table below is consulted otherwise.
  check      run the server-side Parse mirror over any zip; prints nothing
             when it would be accepted as a set, else the server's wording
             (or "archive carries no set manifest" for a plain zip, or a
             "producer-only: ..." line for something the server accepts but
             this producer never emits, e.g. a mis-cased key the phone's
             case-sensitive decoder would not read).
             (A DIR literally named "check" must be given as ./check.)

Exit codes:
  0  built (or: check passed)
  1  I/O error, or the self-check failed (a bug in this script: report it)
  2  usage
  3  inputs rejected locally; nothing was written and nothing was sent

Declared mirror (acradle-setup spec 2026-10-06, D2): chute internal/set is the
authority for the format. This file mirrors its Decode/Parse rules with the
server's exact error strings, as of chute main @ CONFORMED_TO_CHUTE below. The
harness (tests/test-chute-push.sh) checks the mirror against a real chuted;
bump CONFORMED_TO_CHUTE when it passes against a newer one. Ids are derived
exactly as chute cmd/chute-set does, over the sorted member names.

Python >= 3.10, standard library only.
"""

import argparse
import json
import os
import re
import stat
import sys
import zipfile

CONFORMED_TO_CHUTE = "50322eaf44c39f2002dfdcb066a03c5b8200546f"

# chute internal/set constants
MANIFEST_PATH = ".chute/set.json"
FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 256 << 10
MAX_ITEMS = 100
MAX_ID_LEN = 64
MAX_PATH_LEN = 1024
MAX_TITLE_LEN = 120
MAX_DESCRIPTION_LEN = 2000
MAX_ENTRIES = 10_000
MAX_MEMBER_DECLARED_BYTES = 64 << 20
MAX_TOTAL_DECLARED_BYTES = 1 << 30

# The manifest's keys, literally and in Go struct order. The phone's decoder
# is case-sensitive; Go's is not, so nothing on the wire catches a mis-cased
# key except check_archive's producer-only rule.
TOP_KEYS = ("chute_set", "items")
MANIFEST_KEYS = ("id", "path", "title", "description", "content_type")

NOT_A_SET = "archive carries no set manifest"
INVALID_JSON = "manifest is not valid JSON for this format"

EXIT_IO, EXIT_USAGE, EXIT_REJECTED = 1, 2, 3

CHUNK = 1 << 20
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
REGULAR_0644 = 0o100644 << 16

# D6: the only content types this script knows. No mimetypes fallback: it
# differs per guest. Unknown -> exit 3; --type is the escape hatch.
MIME_TABLE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "log": "text/plain",
    "xml": "application/xml",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "zip": "application/zip",
    "tgz": "application/gzip",
    "tar.gz": "application/gzip",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}


# --------------------------------------------------------------------------
# Go equivalents


# unicode.IsSpace: Latin-1 space plus the Unicode White_Space property. Not
# Python's str.isspace, which also counts U+001C..U+001F.
GO_SPACE = (
    "\t\n\v\f\r \u0085  "
    + "".join(chr(c) for c in range(0x2000, 0x200B))
    + "    　"
)


def go_trim(s):
    """strings.TrimSpace."""
    return s.strip(GO_SPACE)


_GO_ESC = {"\a": "\\a", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\v": "\\v"}


def go_quote(s):
    """strconv.Quote, for the two %q messages in set.Parse."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch in '"\\':
            out.append("\\" + ch)
        elif 0xDC80 <= o <= 0xDCFF:  # a surrogate-escaped invalid byte
            out.append("\\x%02x" % (o - 0xDC00))
        elif 0xD800 <= o <= 0xDFFF:
            out.append("�")
        elif ch.isprintable():  # categories L, M, N, P, S and ASCII space
            out.append(ch)
        elif ch in _GO_ESC:
            out.append(_GO_ESC[ch])
        elif o < 0x20 or o == 0x7F:
            out.append("\\x%02x" % o)
        elif o < 0x10000:
            out.append("\\u%04x" % o)
        else:
            out.append("\\U%08x" % o)
    out.append('"')
    return "".join(out)


def go_json(obj):
    """json.Marshal output: compact, UTF-8, Go's HTML-safe escapes."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for a, b in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        s = s.replace(a, b)
    return s.encode("utf-8")


# --------------------------------------------------------------------------
# ids (chute cmd/chute-set sanitizeID / dedupeID)

_ID_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]")
_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,%d}" % MAX_ID_LEN)


def sanitize_id(name):
    s = _ID_DISALLOWED.sub("_", name)[:MAX_ID_LEN]
    return s or "item"


def dedupe_id(base, used):
    if base not in used:
        return base
    n = 2
    while True:
        suffix = "-%d" % n
        cand = base
        if len(cand) + len(suffix) > MAX_ID_LEN:
            cand = cand[: MAX_ID_LEN - len(suffix)]
        cand += suffix
        if cand not in used:
            return cand
        n += 1


def content_type_for(name, overrides):
    low = name.lower()
    exts = []
    if low.endswith(".tar.gz"):
        exts.append("tar.gz")
    ext = os.path.splitext(low)[1][1:]
    if ext:
        exts.append(ext)
    for e in exts:
        for table in (overrides, MIME_TABLE):
            if e in table:
                return table[e]
    return None


# --------------------------------------------------------------------------
# Decode mirror (chute internal/set/set.go)


class _Bad(Exception):
    """Anything Go's json.Decoder would report as an error."""


class _Obj(list):
    """A JSON object as its (key, value) pairs, in order, duplicates kept."""


class _Float:
    """A JSON number with a fraction or exponent: never a Go int."""


def _no_constant(name):
    raise _Bad(name)


_SURROGATE = re.compile("[\ud800-\udfff]")


def _parse_json(b):
    # Go decodes invalid UTF-8 inside strings to one U+FFFD per byte.
    text = b.decode("utf-8", "surrogateescape")
    text = _SURROGATE.sub("�", text)
    i = 0
    while i < len(text) and text[i] in " \t\n\r":
        i += 1
    dec = json.JSONDecoder(
        object_pairs_hook=_Obj,
        parse_float=lambda s: _Float(),
        parse_constant=_no_constant,
    )
    try:
        value, _ = dec.raw_decode(text, i)  # trailing data is ignored, as Go's Decoder does
    except (ValueError, RecursionError):
        raise _Bad("syntax")
    return _fix_surrogates(value)


def _fix_surrogates(v):
    # A "\ud800" escape is U+FFFD in Go.
    if isinstance(v, str):
        return _SURROGATE.sub("�", v)
    if isinstance(v, _Obj):
        return _Obj((_fix_surrogates(k), _fix_surrogates(x)) for k, x in v)
    if isinstance(v, list):
        return [_fix_surrogates(x) for x in v]
    return v


def _fold_eq(key, name):
    """Go's case-insensitive field match (bytes.EqualFold) against an ASCII name."""
    if len(key) != len(name):
        return False
    for k, n in zip(key, name):
        if k == n:
            continue
        if n.isalpha() and k.isascii() and k.lower() == n:
            continue
        if (n, k) in (("s", "ſ"), ("k", "K")):
            continue
        return False
    return True


def _field(key, names):
    for n in names:
        if key == n:
            return n
    for n in names:
        if _fold_eq(key, n):
            return n
    return None


def _go_int(v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise _Bad("not an int")
    if not -(1 << 63) <= v < (1 << 63):
        raise _Bad("overflow")
    return v


def _decode_item(v):
    it = dict.fromkeys(MANIFEST_KEYS, "")
    if v is None:
        return it
    if not isinstance(v, _Obj):
        raise _Bad("item is not an object")
    for k, x in v:
        f = _field(k, MANIFEST_KEYS)
        if f is None:
            raise _Bad("unknown field")
        if x is None:  # null leaves a Go string unchanged
            continue
        if not isinstance(x, str):
            raise _Bad("not a string")
        it[f] = x
    return it


def _decode_top(v):
    fv, items = 0, None
    if v is None:
        return fv, items
    if not isinstance(v, _Obj):
        raise _Bad("not an object")
    for k, x in v:
        f = _field(k, TOP_KEYS)
        if f is None:
            raise _Bad("unknown field")
        if f == "chute_set":
            if x is not None:
                fv = _go_int(x)
        elif x is None:
            items = None
        elif isinstance(x, list) and not isinstance(x, _Obj):
            items = [_decode_item(e) for e in x]
        else:
            raise _Bad("items is not an array")
    return fv, items


def _check_path(p):
    if p == "":
        return "is required"
    if len(p.encode("utf-8")) > MAX_PATH_LEN:
        return "is longer than %d bytes" % MAX_PATH_LEN
    if p.startswith("/") or "\\" in p:
        return "must be a relative, forward-slashed archive member name"
    if p == ".." or p.startswith("../") or "/../" in p or p.endswith("/.."):
        return "must not traverse"
    return None


def _decode(b):
    """Returns (error, items, parsed) like set.Decode; error is server wording."""
    if len(b) > MAX_MANIFEST_BYTES:
        return "manifest is larger than %d bytes" % MAX_MANIFEST_BYTES, None, None
    try:
        parsed = _parse_json(b)
        fv, items = _decode_top(parsed)
    except _Bad:
        return INVALID_JSON, None, None
    if fv != FORMAT_VERSION:
        return "chute_set must be %d" % FORMAT_VERSION, None, None
    items = items or []
    if not items:
        return "a set needs at least one item", None, None
    if len(items) > MAX_ITEMS:
        return "a set holds at most %d items" % MAX_ITEMS, None, None
    seen_id, seen_path = set(), set()
    for i, it in enumerate(items):
        if not _ID_RE.fullmatch(it["id"]):
            return "item %d: id must match [A-Za-z0-9._-]{1,%d}" % (i, MAX_ID_LEN), None, None
        if it["id"] in seen_id:
            return "item %d: duplicate id" % i, None, None
        seen_id.add(it["id"])
        perr = _check_path(it["path"])
        if perr:
            return "item %d: path %s" % (i, perr), None, None
        if it["path"] in seen_path:
            return "item %d: duplicate path" % i, None, None
        seen_path.add(it["path"])
        if go_trim(it["title"]) == "":
            return "item %d: title is required" % i, None, None
        if len(it["title"]) > MAX_TITLE_LEN:
            return "item %d: title is longer than %d characters" % (i, MAX_TITLE_LEN), None, None
        if len(it["description"]) > MAX_DESCRIPTION_LEN:
            return (
                "item %d: description is longer than %d characters" % (i, MAX_DESCRIPTION_LEN),
                None,
                None,
            )
        if go_trim(it["content_type"]) == "":
            return "item %d: content_type is required" % i, None, None
    return None, items, parsed


def validate_manifest(b):
    """set.Decode mirror: None when the server would accept these bytes."""
    return _decode(b)[0]


# --------------------------------------------------------------------------
# Parse mirror (chute internal/set/archive.go)


def _raw_name(info):
    enc = "utf-8" if info.flag_bits & 0x800 else "cp437"
    return info.orig_filename.encode(enc, "surrogateescape")


_UNIX_IRREGULAR = (0o010000, 0o020000, 0o040000, 0o060000, 0o120000, 0o140000)


def _is_regular(info, raw):
    """fs.FileMode.IsRegular of archive/zip FileHeader.Mode()."""
    regular = True
    if info.create_system in (3, 19):  # unix, macOS
        regular = ((info.external_attr >> 16) & 0o170000) not in _UNIX_IRREGULAR
    elif info.create_system in (0, 11, 14):  # FAT, NTFS, VFAT
        regular = not info.external_attr & 0x10
    if raw.endswith(b"/"):
        regular = False
    return regular


def _producer_only(parsed):
    """Rules the server does not enforce but every consumer relies on."""
    keys = [k for k, _ in parsed]
    for k in keys:
        if k not in TOP_KEYS:
            return "producer-only: key %s is not exactly one of %s" % (
                go_quote(k),
                ", ".join(go_quote(n) for n in TOP_KEYS),
            )
    for n in TOP_KEYS:
        if keys.count(n) != 1:
            return "producer-only: key %s must appear exactly once" % go_quote(n)
    items = dict(parsed)["items"]
    for i, it in enumerate(items):
        if not isinstance(it, _Obj):
            return "producer-only: item %d is not an object" % i
        ikeys = [k for k, _ in it]
        for k in ikeys:
            if k not in MANIFEST_KEYS:
                return "producer-only: item %d: key %s is not exactly one of %s" % (
                    i,
                    go_quote(k),
                    ", ".join(go_quote(n) for n in MANIFEST_KEYS),
                )
        for n in MANIFEST_KEYS:
            if ikeys.count(n) != 1:
                return "producer-only: item %d: key %s must appear exactly once" % (i, go_quote(n))
        for k, v in it:
            if not isinstance(v, str):
                return "producer-only: item %d: %s must be a string" % (i, go_quote(k))
    return None


def check_archive(path):
    """set.Parse mirror: None when the server (and the phone) would accept path as a set."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, UnicodeDecodeError):
        return NOT_A_SET + ": not a zip archive"
    with zf:
        infos = zf.infolist()
        raws = [_raw_name(i) for i in infos]
        manifest = None
        for info, raw in zip(infos, raws):
            if raw == MANIFEST_PATH.encode():
                manifest = info
                break
        if manifest is None:
            return NOT_A_SET
        if len(infos) > MAX_ENTRIES:
            return "archive holds more than %d entries" % MAX_ENTRIES
        by_name = {}
        for info, raw in zip(infos, raws):
            if raw in by_name:
                return "archive has duplicate entry names"
            by_name[raw] = info

        if manifest.flag_bits & 0x1:
            return "producer-only: %s is encrypted" % MANIFEST_PATH
        if manifest.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            return "manifest member could not be opened"
        try:
            f = zf.open(manifest)
        except zipfile.BadZipFile as e:
            if "differ" in str(e):
                # zipfile compares the local header's name with the central
                # directory's; Go never reads the local name, so the server
                # accepts this. The mirror cannot read the manifest to vouch
                # for it, and must not answer in server wording.
                return (
                    "producer-only: %s: the local header names it differently from "
                    "the central directory (the server reads only the latter)" % MANIFEST_PATH
                )
            return "manifest member could not be opened"
        except (NotImplementedError, RuntimeError, OSError, ValueError, EOFError):
            return "manifest member could not be opened"
        try:
            with f:
                data = f.read(MAX_MANIFEST_BYTES)
        except Exception:  # CRC mismatch, zlib error, truncation: Go's read error
            return "manifest member could not be read"
        if len(data) >= MAX_MANIFEST_BYTES:
            return "manifest is larger than %d bytes" % MAX_MANIFEST_BYTES

        err, items, parsed = _decode(data)
        if err:
            return err

        total = 0
        for i, it in enumerate(items):
            info = by_name.get(it["path"].encode("utf-8"))
            if info is None:
                return "item %d: no archive member named %s" % (i, go_quote(it["path"]))
            if not _is_regular(info, it["path"].encode("utf-8")):
                return "item %d: %s is not a regular file" % (i, go_quote(it["path"]))
            if info.file_size > MAX_MEMBER_DECLARED_BYTES:
                return "item %d: declares more than %d bytes" % (i, MAX_MEMBER_DECLARED_BYTES)
            total += info.file_size
            if total > MAX_TOTAL_DECLARED_BYTES:
                return "items declare more than %d bytes in total" % MAX_TOTAL_DECLARED_BYTES

        err = _producer_only(parsed)
        if err:
            return err
        for i, it in enumerate(items):
            info = by_name[it["path"].encode("utf-8")]
            if info.flag_bits & 0x1:
                return "producer-only: item %d: %s is encrypted" % (i, go_quote(it["path"]))
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                return "producer-only: item %d: %s uses compression method %d (only store and deflate are read)" % (
                    i,
                    go_quote(it["path"]),
                    info.compress_type,
                )
    return None


# --------------------------------------------------------------------------
# build


class Reject(Exception):
    """Inputs rejected locally (exit 3)."""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class SelfCheckFailed(Exception):
    pass


_HINTS = (
    ("a set needs at least one item", "put the files directly in the directory; subdirectories, symlinks and dotfiles are skipped"),
    ("a set holds at most", "split the files into several sets of at most %d" % MAX_ITEMS),
    ("manifest is larger than", "shorten the titles and descriptions, or split into several sets"),
    ("title is required", "give the --items row a non-blank title, or leave the title empty to use the file name"),
    ("title is longer than", "shorten the title in --items (at most %d characters)" % MAX_TITLE_LEN),
    ("description is longer than", "shorten the description in --items (at most %d characters)" % MAX_DESCRIPTION_LEN),
    ("declares more than", "a member may be at most 64 MiB; shrink it or push it on its own"),
    ("in total", "split the files into several sets"),
    ("path ", "rename the file"),
)

_ITEM_MSG = re.compile(r"item (\d+): (.*)", re.S)


def _reject_server(msg, order):
    """Turn a server-wording error into a Reject naming the file."""
    hint = next((h for key, h in _HINTS if key in msg), None)
    m = _ITEM_MSG.fullmatch(msg)
    if m and int(m.group(1)) < len(order):
        n = int(m.group(1))
        raise Reject("item %d (%s): %s" % (n, order[n], m.group(2)), hint)
    raise Reject(msg, hint)


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _shown(name):
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def list_members(directory, exclude):
    members = []
    for name in sorted(os.listdir(directory)):
        if name.startswith("."):
            continue
        full = os.path.join(directory, name)
        st = os.lstat(full)
        if stat.S_ISLNK(st.st_mode):
            print("chute-set: skipped (symlink): %s" % _shown(name), file=sys.stderr)
            continue
        if stat.S_ISDIR(st.st_mode):
            print("chute-set: skipped (directory): %s" % _shown(name), file=sys.stderr)
            continue
        if not stat.S_ISREG(st.st_mode):
            print("chute-set: skipped (not a regular file): %s" % _shown(name), file=sys.stderr)
            continue
        if any(_same(full, x) for x in exclude):
            continue
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            raise Reject("%s: the file name is not valid UTF-8; rename the file" % _shown(name))
        members.append((name, st.st_size))
    return members


def load_items_tsv(path, directory, members):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise Reject("--items %s: cannot read it (%s)" % (path, e.strerror or e))
    # A leading UTF-8 byte-order mark (PowerShell 5.1's Out-File writes one)
    # would otherwise become an invisible part of the first filename.
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    rows, seen = [], {}
    for n, raw in enumerate(data.split(b"\n"), 1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise Reject("%s line %d: not valid UTF-8 (byte %d)" % (path, n, e.start + 1))
        if go_trim(text) == "":
            continue
        fields = text.split("\t")
        if len(fields) > 3:
            raise Reject(
                "%s line %d: %d columns; a row is filename<TAB>title<TAB>description"
                % (path, n, len(fields))
            )
        name = fields[0]
        if name not in members:
            raise Reject("%s line %d: '%s' is not a file in %s" % (path, n, name, directory))
        if name in seen:
            raise Reject("%s line %d: '%s' is already listed on line %d" % (path, n, name, seen[name]))
        seen[name] = n
        title = fields[1] if len(fields) > 1 else ""
        description = fields[2] if len(fields) > 2 else ""
        rows.append((name, title or name, description))
    return rows


def _zinfo(name):
    zi = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = REGULAR_0644
    return zi


def build(args, overrides):
    directory = args.dir
    if not os.path.isdir(directory):
        raise Reject("%s: not a directory" % directory)
    exclude = [p for p in (args.items, args.out, args.manifest_out) if p]
    members = list_members(directory, exclude)
    sizes = dict(members)
    names = [n for n, _ in members]  # sorted

    rows = load_items_tsv(args.items, directory, sizes) if args.items else []
    captioned = {n: (t, d) for n, t, d in rows}

    # D5: ids follow sorted names; display order follows the TSV, then names.
    used, ids = set(), {}
    for n in names:
        ids[n] = dedupe_id(sanitize_id(n), used)
        used.add(ids[n])
    order = [n for n, _, _ in rows] + [n for n in names if n not in captioned]
    if args.cover is not None:
        if args.cover not in sizes:
            raise Reject("--cover %s: not a file in %s" % (args.cover, directory))
        order.remove(args.cover)
        order.insert(0, args.cover)

    items = []
    for n in order:
        ct = content_type_for(n, overrides)
        if ct is None:
            ext = os.path.splitext(n)[1][1:].lower()
            if ext:
                raise Reject(
                    '%s: no content type is known for ".%s"; rename the file or pass --type %s=TYPE/SUBTYPE'
                    % (n, ext, ext)
                )
            raise Reject("%s: no extension, so no content type; rename the file or pass --type" % n)
        title, description = captioned.get(n, (n, ""))
        items.append(
            dict(zip(MANIFEST_KEYS, (ids[n], n, title, description, ct)))
        )

    manifest = go_json({"chute_set": FORMAT_VERSION, "items": items})
    # Server order: readBounded (>=) runs before Decode (>).
    if len(manifest) >= MAX_MANIFEST_BYTES:
        _reject_server("manifest is larger than %d bytes" % MAX_MANIFEST_BYTES, order)
    err = validate_manifest(manifest)
    if err:
        _reject_server(err, order)
    total = 0
    for i, n in enumerate(order):
        if sizes[n] > MAX_MEMBER_DECLARED_BYTES:
            _reject_server("item %d: declares more than %d bytes" % (i, MAX_MEMBER_DECLARED_BYTES), order)
        total += sizes[n]
        if total > MAX_TOTAL_DECLARED_BYTES:
            _reject_server("items declare more than %d bytes in total" % MAX_TOTAL_DECLARED_BYTES, order)

    tmp = "%s.tmp-%d" % (args.out, os.getpid())
    try:
        with open(tmp, "wb") as fh, zipfile.ZipFile(fh, "w") as zf:
            zf.writestr(_zinfo(MANIFEST_PATH), manifest)
            for it in items:
                with open(os.path.join(directory, it["path"]), "rb") as src, zf.open(
                    _zinfo(it["path"]), "w"
                ) as dst:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
        err = check_archive(tmp)
        if err is None:
            with zipfile.ZipFile(tmp) as zf:
                if zf.read(MANIFEST_PATH) != manifest:
                    err = "the manifest read back differs from the one written"
        if err:
            raise SelfCheckFailed(err)
        os.replace(tmp, args.out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    if args.manifest_out:
        with open(args.manifest_out, "wb") as f:
            f.write(manifest)

    for it in items:
        line = '  %s  id=%s  "%s"' % (it["path"], it["id"], it["title"])
        if not it["content_type"].startswith("image/"):
            line += " (opaque on the phone: opens with another app)"
        print(line)
    print("chute-set: %d items, %d bytes" % (len(items), os.path.getsize(args.out)))


# --------------------------------------------------------------------------
# CLI


def _type_arg(value):
    ext, sep, mime = value.partition("=")
    ext = ext.strip().lstrip(".").lower()
    mime = mime.strip()
    if not sep or not ext or not mime:
        raise argparse.ArgumentTypeError("expected EXT=TYPE/SUBTYPE, got %r" % value)
    return ext, mime


def _parser():
    p = argparse.ArgumentParser(
        prog="chute-set.py",
        description="Build a chute artifact set from a flat directory (or: chute-set.py check FILE.zip).",
    )
    p.add_argument("--out", required=True, help="the zip to write")
    p.add_argument("--items", help="TSV of filename<TAB>title<TAB>description; row order is display order")
    p.add_argument("--cover", help="member to show first")
    p.add_argument("--type", action="append", type=_type_arg, default=[], metavar="EXT=MIME")
    p.add_argument("--manifest-out", help="also write the exact manifest bytes here")
    p.add_argument("dir")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            # LF even on a Windows host, where the harness reads it from bash
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")

    if argv[:1] == ["check"]:
        if len(argv) != 2:
            print("usage: chute-set.py check FILE.zip", file=sys.stderr)
            return EXIT_USAGE
        if not os.path.isfile(argv[1]):
            print("chute-set: check: cannot read %s" % argv[1], file=sys.stderr)
            return EXIT_IO
        err = check_archive(argv[1])
        if err:
            print(err)
            return EXIT_REJECTED
        return 0

    args = _parser().parse_args(argv)
    overrides = dict(args.type)
    try:
        build(args, overrides)
    except Reject as e:
        print("chute-set: %s" % e.message, file=sys.stderr)
        if e.hint:
            print("hint: %s" % e.hint, file=sys.stderr)
        return EXIT_REJECTED
    except SelfCheckFailed as e:
        print(
            "chute-set: self-check: the archive just written would be rejected: %s\n"
            "chute-set: this is a chute-set.py bug (CONFORMED_TO_CHUTE=%s); report it"
            % (e, CONFORMED_TO_CHUTE),
            file=sys.stderr,
        )
        return EXIT_IO
    except OSError as e:
        print("chute-set: %s" % e, file=sys.stderr)
        return EXIT_IO
    return 0


if __name__ == "__main__":
    sys.exit(main())
