#!/usr/bin/env python3
"""Unit tests for chute-set.py (no network).

Run:  python3 tests/test-chute-set.py -v
The script's dashed name is not importable, so it is loaded by path, and the
CLI is driven through subprocess for exit codes and output.

The id cases are lifted from chute cmd/chute-set/main_test.go and the error
strings from chute internal/set/{set.go,archive.go}, both at
50322eaf44c39f2002dfdcb066a03c5b8200546f. If chute changes them, the harness
(tests/test-chute-push.sh s13) goes red against the real server; these tests
pin what the mirror says today.
"""

import contextlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(
    HERE, "..", "plugins", "acradle-vm", "skills", "pushing-artifacts-to-phone", "chute-set.py"
)
# CHUTE_SET_PY points the tests at a deliberately broken copy (red-checks).
SCRIPT = os.path.normpath(os.environ.get("CHUTE_SET_PY", SCRIPT))


def load_module():
    # never leave a __pycache__ next to the script: that directory ships as the skill
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("chute_set", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = load_module()

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
MIB = 1 << 20


def run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(
        [sys.executable, SCRIPT, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def can_symlink(tmp):
    try:
        os.symlink(os.path.join(tmp, "nowhere"), os.path.join(tmp, ".probe"))
    except (OSError, NotImplementedError):
        return False
    os.unlink(os.path.join(tmp, ".probe"))
    return True


def manifest(items, chute_set=1):
    return json.dumps({"chute_set": chute_set, "items": items}).encode("utf-8")


def item(i="a", path="a.png", title="A", description="", content_type="image/png"):
    return {
        "id": i,
        "path": path,
        "title": title,
        "description": description,
        "content_type": content_type,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.dir = os.path.join(self.tmp, "renders")
        os.mkdir(self.dir)
        self.out = os.path.join(self.tmp, "out.zip")

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, name, data=PNG, where=None):
        path = os.path.join(where or self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def tsv(self, text, name="items.tsv", where=None, raw=None):
        path = os.path.join(where or self.tmp, name)
        with open(path, "wb") as f:
            f.write(raw if raw is not None else text.encode("utf-8"))
        return path

    def build(self, *extra):
        return run("--out", self.out, *extra, self.dir)

    def build_ok(self, *extra):
        rc, out, err = self.build(*extra)
        self.assertEqual(rc, 0, f"stdout={out!r} stderr={err!r}")
        return out, err

    def read_manifest_bytes(self, path=None):
        with zipfile.ZipFile(path or self.out) as zf:
            return zf.read(".chute/set.json")

    def read_manifest(self, path=None):
        return json.loads(self.read_manifest_bytes(path).decode("utf-8"))

    def raw_zip(self, entries, path=None):
        """Write a zip with raw zipfile, no producer checks.

        entries: list of (name, data, external_attr|None, create_system|None,
        compress_type|None).
        """
        path = path or os.path.join(self.tmp, "raw.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data, attr, system, ctype in entries:
                zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED if ctype is None else ctype
                zi.create_system = 3 if system is None else system
                zi.external_attr = (0o100644 << 16) if attr is None else attr
                zf.writestr(zi, data)
        return path


# --------------------------------------------------------------------------
# ids


class TestIds(unittest.TestCase):
    # chute cmd/chute-set/main_test.go @ 50322ea
    def test_sanitize_cases_from_chute(self):
        self.assertEqual(cs.sanitize_id("my photo!.png"), "my_photo_.png")
        self.assertEqual(cs.sanitize_id(""), "item")
        self.assertEqual(cs.sanitize_id("\U0001F600\U0001F600"), "__")

    def test_sanitize_truncates_to_64(self):
        self.assertEqual(cs.sanitize_id("a" * 70 + ".png"), "a" * 64)
        # a multi-byte rune is one '_' before truncation, so 64 runes in -> 64 out
        self.assertEqual(cs.sanitize_id("é" * 70), "_" * 64)

    def test_dedupe_cases_from_chute(self):
        used = {"photo_.png": True}
        got = cs.dedupe_id("photo_.png", used)
        self.assertEqual(got, "photo_.png-2")
        used[got] = True
        self.assertEqual(cs.dedupe_id("photo_.png", used), "photo_.png-3")

    def test_dedupe_truncates_the_base_to_fit(self):
        base = "a" * 64
        self.assertEqual(cs.dedupe_id(base, {base: True}), "a" * 62 + "-2")


class TestIdsInBuild(Base):
    def test_colliding_names_dedupe_in_sorted_order(self):
        # chute: TestBuildItemsDedupesIDsThatCollideAfterSanitization
        self.put("photo!.png")
        self.put("photo?.png") if os.name != "nt" else self.put("photo#.png")
        second = "photo?.png" if os.name != "nt" else "photo#.png"
        self.build_ok()
        ids = {it["path"]: it["id"] for it in self.read_manifest()["items"]}
        # sorted: "photo!.png" < "photo#.png" / "photo?.png"
        self.assertEqual(ids["photo!.png"], "photo_.png")
        self.assertEqual(ids[second], "photo_.png-2")

    def test_ids_do_not_follow_caption_order(self):
        self.put("photo!.png")
        self.put("photo#.png")
        t = self.tsv("photo#.png\tSecond first\nphoto!.png\tFirst second\n")
        self.build_ok("--items", t)
        m = self.read_manifest()
        self.assertEqual([it["path"] for it in m["items"]], ["photo#.png", "photo!.png"])
        ids = {it["path"]: it["id"] for it in m["items"]}
        self.assertEqual(ids["photo!.png"], "photo_.png")
        self.assertEqual(ids["photo#.png"], "photo_.png-2")


# --------------------------------------------------------------------------
# TSV


class TestTsv(Base):
    def test_fourth_column_is_an_error_with_line_number(self):
        self.put("a.png")
        t = self.tsv("a.png\tA\n\na.png\tA\td\textra\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertIn("line 3", err)
        self.assertIn("column", err)
        self.assertFalse(os.path.exists(self.out))

    def test_row_naming_a_non_member_is_an_error(self):
        self.put("a.png")
        t = self.tsv("b.png\tB\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertIn("line 1", err)
        self.assertIn("b.png", err)

    def test_filename_is_not_trimmed(self):
        self.put("a.png")
        t = self.tsv(" a.png\tA\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertIn("line 1", err)

    def test_duplicate_row_is_an_error(self):
        self.put("a.png")
        t = self.tsv("a.png\tA\na.png\tB\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertIn("line 2", err)

    def test_crlf_and_blank_lines(self):
        self.put("a.png")
        self.put("b.png")
        t = self.tsv("a.png\tTitle A\tDesc A\r\n\r\n   \r\nb.png\tTitle B\r\n")
        self.build_ok("--items", t)
        items = self.read_manifest()["items"]
        self.assertEqual(items[0]["title"], "Title A")
        self.assertEqual(items[0]["description"], "Desc A")
        self.assertEqual(items[1]["title"], "Title B")
        self.assertEqual(items[1]["description"], "")

    def test_empty_title_falls_back_to_basename(self):
        self.put("a.png")
        t = self.tsv("a.png\t\tonly a description\n")
        self.build_ok("--items", t)
        it = self.read_manifest()["items"][0]
        self.assertEqual(it["title"], "a.png")
        self.assertEqual(it["description"], "only a description")

    def test_file_without_row_gets_basename_and_empty_description(self):
        self.put("a.png")
        self.put("b.png")
        t = self.tsv("a.png\tA\n")
        self.build_ok("--items", t)
        it = self.read_manifest()["items"][1]
        self.assertEqual((it["path"], it["title"], it["description"]), ("b.png", "b.png", ""))

    def test_invalid_utf8_title_names_the_line(self):
        self.put("a.png")
        t = self.tsv(None, raw=b"a.png\tok\n\na.png\tbad \xff title\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertIn("line 3", err)
        self.assertIn("UTF-8", err)

    def test_tsv_inside_dir_is_not_a_member(self):
        self.put("a.png")
        t = self.tsv("a.png\tA\n", where=self.dir)
        self.build_ok("--items", t)
        self.assertEqual([it["path"] for it in self.read_manifest()["items"]], ["a.png"])

    def test_missing_tsv_is_rejected(self):
        self.put("a.png")
        rc, out, err = self.build("--items", os.path.join(self.tmp, "nope.tsv"))
        self.assertEqual(rc, 3)
        self.assertIn("nope.tsv", err)


# --------------------------------------------------------------------------
# members


class TestMembers(Base):
    def test_dotfiles_and_directories_are_skipped(self):
        self.put("a.png")
        self.put(".hidden.png")
        os.mkdir(os.path.join(self.dir, "sub"))
        out, err = self.build_ok()
        self.assertEqual([it["path"] for it in self.read_manifest()["items"]], ["a.png"])
        self.assertIn("skipped (directory): sub", err)
        self.assertNotIn(".hidden.png", err)

    def test_symlinks_are_skipped(self):
        if not can_symlink(self.tmp):
            self.skipTest("cannot create symlinks here (Windows without developer mode)")
        self.put("a.png")
        os.symlink(os.path.join(self.dir, "a.png"), os.path.join(self.dir, "link.png"))
        out, err = self.build_ok()
        self.assertEqual([it["path"] for it in self.read_manifest()["items"]], ["a.png"])
        self.assertIn("skipped (symlink): link.png", err)

    @unittest.skipIf(os.name == "nt", "Windows cannot hold a non-UTF-8 filename")
    def test_non_utf8_filename_is_rejected(self):
        self.put("a.png")
        with open(os.path.join(os.fsencode(self.dir), b"bad\xff.png"), "wb") as f:
            f.write(PNG)
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertIn("rename the file", err)

    def test_out_inside_dir_is_not_a_member(self):
        self.put("a.png")
        inside = os.path.join(self.dir, "set.zip")
        rc, out, err = run("--out", inside, self.dir)
        self.assertEqual(rc, 0, err)
        rc, out, err = run("--out", inside, "--type", "zip=application/zip", self.dir)
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            [it["path"] for it in self.read_manifest(inside)["items"]], ["a.png"]
        )

    def test_missing_dir_is_rejected(self):
        rc, out, err = run("--out", self.out, os.path.join(self.tmp, "nope"))
        self.assertEqual(rc, 3)
        self.assertIn("not a directory", err)


# --------------------------------------------------------------------------
# ordering


class TestOrdering(Base):
    def test_tsv_rows_first_then_sorted_rest(self):
        for n in ("d.png", "c.png", "b.png", "a.png"):
            self.put(n)
        t = self.tsv("c.png\tC\na.png\tA\n")
        self.build_ok("--items", t)
        paths = [it["path"] for it in self.read_manifest()["items"]]
        self.assertEqual(paths, ["c.png", "a.png", "b.png", "d.png"])

    def test_cover_hoists_to_first(self):
        for n in ("a.png", "b.png", "c.png"):
            self.put(n)
        t = self.tsv("b.png\tB\n")
        self.build_ok("--items", t, "--cover", "c.png")
        paths = [it["path"] for it in self.read_manifest()["items"]]
        self.assertEqual(paths, ["c.png", "b.png", "a.png"])

    def test_cover_must_be_a_member(self):
        self.put("a.png")
        rc, out, err = self.build("--cover", "zz.png")
        self.assertEqual(rc, 3)
        self.assertIn("--cover", err)
        self.assertIn("zz.png", err)


# --------------------------------------------------------------------------
# MIME


class TestMime(Base):
    def test_table_only(self):
        self.assertEqual(cs.content_type_for("x.webp", {}), "image/webp")
        self.assertEqual(cs.content_type_for("X.PNG", {}), "image/png")
        self.assertEqual(cs.content_type_for("a.jpeg", {}), "image/jpeg")
        self.assertEqual(cs.content_type_for("notes.md", {}), "text/markdown")
        self.assertEqual(cs.content_type_for("a.tar.gz", {}), "application/gzip")
        self.assertIsNone(cs.content_type_for("a.gz", {}))
        self.assertIsNone(cs.content_type_for("Makefile", {}))
        self.assertIsNone(cs.content_type_for("a.xyz", {}))

    def test_whole_table(self):
        for ext in (
            "png jpg jpeg gif webp svg bmp heic pdf txt md html htm json csv tsv log "
            "xml yaml yml zip tgz tar.gz mp4 webm mp3 wav"
        ).split():
            self.assertIsNotNone(cs.content_type_for("f." + ext, {}), ext)
        self.assertEqual(len(cs.MIME_TABLE), 27)

    def test_type_overrides_and_adds(self):
        self.put("a.png")
        self.put("b.xyz", b"x")
        self.build_ok("--type", "png=image/x-test", "--type", ".XYZ=application/x-xyz")
        cts = {it["path"]: it["content_type"] for it in self.read_manifest()["items"]}
        self.assertEqual(cts, {"a.png": "image/x-test", "b.xyz": "application/x-xyz"})

    def test_bad_type_flag_is_usage(self):
        self.put("a.png")
        rc, out, err = self.build("--type", "png")
        self.assertEqual(rc, 2)

    def test_unknown_extension_names_the_file(self):
        self.put("a.png")
        self.put("foo.xyz", b"x")
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertIn("foo.xyz", err)
        self.assertIn("--type", err)
        self.assertFalse(os.path.exists(self.out))

    def test_opaque_marker_on_stdout(self):
        self.put("a.png")
        self.put("notes.md", b"# hi\n")
        out, err = self.build_ok()
        lines = out.splitlines()
        self.assertEqual(lines[0], '  a.png  id=a.png  "a.png"')
        self.assertEqual(
            lines[1],
            '  notes.md  id=notes.md  "notes.md" (opaque on the phone: opens with another app)',
        )
        self.assertRegex(lines[2], r"^chute-set: 2 items, \d+ bytes$")
        self.assertEqual(lines[2], f"chute-set: 2 items, {os.path.getsize(self.out)} bytes")


# --------------------------------------------------------------------------
# Layer 1 through the CLI: server wording, 0-based display index


class TestLayer1(Base):
    def test_empty_dir(self):
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertEqual(err.splitlines()[0], "chute-set: a set needs at least one item")

    def test_101_files(self):
        for i in range(101):
            self.put(f"f{i:03d}.png")
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        lines = err.splitlines()
        self.assertEqual(lines[0], "chute-set: a set holds at most 100 items")
        self.assertTrue(lines[1].startswith("hint: "), lines)

    def test_100_files_ok(self):
        for i in range(100):
            self.put(f"f{i:03d}.png")
        self.build_ok()

    def _title(self, title, row=0):
        self.put("a.png")
        self.put("b.png")
        rows = ["a.png\tfirst", "b.png\tsecond"]
        rows[row] = rows[row].split("\t")[0] + "\t" + title
        t = self.tsv("\n".join(rows) + "\n")
        return self.build("--items", t)

    def test_blank_title_uses_display_index(self):
        rc, out, err = self._title("   ", row=1)
        self.assertEqual(rc, 3)
        self.assertEqual(err.splitlines()[0], "chute-set: item 1 (b.png): title is required")

    def test_index_is_display_position_not_sorted_position(self):
        self.put("a.png")
        self.put("b.png")
        t = self.tsv("b.png\tfirst\na.png\t \n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertEqual(err.splitlines()[0], "chute-set: item 1 (a.png): title is required")

    def test_nbsp_title_is_blank_like_go(self):
        rc, out, err = self._title(" 　")
        self.assertEqual(rc, 3)
        self.assertEqual(err.splitlines()[0], "chute-set: item 0 (a.png): title is required")

    def test_u001c_title_is_not_blank_in_go(self):
        rc, out, err = self._title("\u001c")
        self.assertEqual(rc, 0, err)

    def test_title_length(self):
        rc, out, err = self._title("é" * 121)
        self.assertEqual(rc, 3)
        self.assertEqual(
            err.splitlines()[0],
            "chute-set: item 0 (a.png): title is longer than 120 characters",
        )

    def test_title_120_ok(self):
        rc, out, err = self._title("\U0001F600" * 120)
        self.assertEqual(rc, 0, err)

    def test_description_length(self):
        self.put("a.png")
        t = self.tsv("a.png\tA\t" + "d" * 2001 + "\n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertEqual(
            err.splitlines()[0],
            "chute-set: item 0 (a.png): description is longer than 2000 characters",
        )

    @unittest.skipIf(os.name == "nt", "Windows cannot name a file with a backslash")
    def test_backslash_name(self):
        self.put("a\\b.png")
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertEqual(
            err.splitlines()[0],
            "chute-set: item 0 (a\\b.png): path must be a relative, forward-slashed archive member name",
        )

    def _sparse(self, name, size):
        path = os.path.join(self.dir, name)
        with open(path, "wb"):
            pass
        try:
            os.truncate(path, size)
        except OSError as e:
            self.skipTest(f"cannot make a {size}-byte file: {e}")

    def test_member_over_64_mib(self):
        self.put("a.png")
        self._sparse("big.png", 64 * MIB + 1)
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertEqual(
            err.splitlines()[0],
            "chute-set: item 1 (big.png): declares more than 67108864 bytes",
        )
        self.assertFalse(os.path.exists(self.out))

    def test_total_over_1_gib(self):
        for i in range(16):
            self._sparse(f"f{i:02d}.png", 64 * MIB)
        self.put("z.png", b"x")
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        self.assertEqual(
            err.splitlines()[0], "chute-set: items declare more than 1073741824 bytes in total"
        )
        self.assertFalse(os.path.exists(self.out))

    def _manifest_of_size(self, target):
        """100 members whose manifest serializes to exactly `target` bytes."""
        names = [f"f{i:03d}.png" for i in range(100)]
        for n in names:
            self.put(n)
        rows = {n: "€" * 800 for n in names}

        def size():
            items = [
                {"id": n, "path": n, "title": n, "description": rows[n], "content_type": "image/png"}
                for n in names
            ]
            return len(cs.go_json({"chute_set": 1, "items": items}))

        short = target - size()
        self.assertGreater(short, 0)
        # top up with ASCII, 2000 runes per description max
        for n in names:
            room = 2000 - len(rows[n])
            add = min(room, short)
            rows[n] += "x" * add
            short -= add
            if not short:
                break
        self.assertEqual(size(), target)
        return self.tsv("".join(f"{n}\t{n}\t{rows[n]}\n" for n in names))

    def test_manifest_of_262144_bytes_is_too_large(self):
        t = self._manifest_of_size(262144)
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        lines = err.splitlines()
        self.assertEqual(lines[0], "chute-set: manifest is larger than 262144 bytes")
        self.assertTrue(lines[1].startswith("hint: "))

    def test_manifest_of_262143_bytes_is_fine(self):
        t = self._manifest_of_size(262143)
        self.build_ok("--items", t)
        self.assertEqual(len(self.read_manifest_bytes()), 262143)


# --------------------------------------------------------------------------
# Decode mirror on raw bytes


class TestValidateManifest(unittest.TestCase):
    v = staticmethod(lambda b: cs.validate_manifest(b))

    def test_valid(self):
        self.assertIsNone(self.v(manifest([item()])))

    def test_format_version(self):
        self.assertEqual(self.v(manifest([item()], chute_set=2)), "chute_set must be 1")
        self.assertEqual(self.v(b'{"items":[]}'), "chute_set must be 1")
        self.assertEqual(self.v(b"null"), "chute_set must be 1")
        bad = "manifest is not valid JSON for this format"
        self.assertEqual(self.v(b'{"chute_set":1.0,"items":[]}'), bad)
        self.assertEqual(self.v(b'{"chute_set":"1","items":[]}'), bad)
        self.assertEqual(self.v(b'{"chute_set":true,"items":[]}'), bad)
        self.assertEqual(self.v(b'{"chute_set":99999999999999999999,"items":[]}'), bad)

    def test_not_json(self):
        bad = "manifest is not valid JSON for this format"
        for b in (b"", b"  ", b"{", b"[]", b'"x"', b"NaN", b'{"chute_set":NaN}', b"\xef\xbb\xbf{}"):
            self.assertEqual(self.v(b), bad, b)

    def test_trailing_data_after_the_value_is_ignored_like_go(self):
        self.assertIsNone(self.v(manifest([item()]) + b" garbage"))

    def test_unknown_fields(self):
        bad = "manifest is not valid JSON for this format"
        self.assertEqual(self.v(b'{"chute_set":1,"items":[],"x":1}'), bad)
        it = item()
        it["extra"] = "x"
        self.assertEqual(self.v(manifest([it])), bad)

    def test_case_insensitive_keys_are_accepted_like_go(self):
        it = item()
        it["Title"] = it.pop("title")
        self.assertIsNone(self.v(manifest([it])))
        self.assertIsNone(self.v(b'{"CHUTE_SET":1,"ITEMS":[' + json.dumps(item()).encode() + b"]}"))
        # Go's simple folding: U+017F LONG S matches 's'
        self.assertIsNone(self.v('{"chute_ſet":1,"items":[{"id":"a","path":"a","title":"t","content_type":"c"}]}'.encode()))

    def test_null_keeps_the_previous_value_like_go(self):
        b = b'{"chute_set":1,"items":[{"id":"a","path":"a","title":"t","title":null,"content_type":"c"}]}'
        self.assertIsNone(self.v(b))

    def test_type_mismatch(self):
        it = item()
        it["title"] = 5
        self.assertEqual(self.v(manifest([it])), "manifest is not valid JSON for this format")
        self.assertEqual(self.v(b'{"chute_set":1,"items":[5]}'), "manifest is not valid JSON for this format")

    def test_item_counts(self):
        self.assertEqual(self.v(manifest([])), "a set needs at least one item")
        self.assertEqual(self.v(b'{"chute_set":1,"items":null}'), "a set needs at least one item")
        many = [item(i=f"i{n}", path=f"p{n}") for n in range(101)]
        self.assertEqual(self.v(manifest(many)), "a set holds at most 100 items")

    def test_ids(self):
        msg = "item 0: id must match [A-Za-z0-9._-]{1,64}"
        for bad in ("", "a b", "a/b", "x" * 65, "a\n", "é"):
            self.assertEqual(self.v(manifest([item(i=bad)])), msg, repr(bad))
        self.assertIsNone(self.v(manifest([item(i="x" * 64)])))
        self.assertEqual(self.v(b'{"chute_set":1,"items":[null]}'), msg)
        two = [item(), item(path="b.png")]
        self.assertEqual(self.v(manifest(two)), "item 1: duplicate id")

    def test_paths(self):
        rel = "must be a relative, forward-slashed archive member name"
        cases = {
            "": "item 0: path is required",
            "x" * 1025: "item 0: path is longer than 1024 bytes",
            "é" * 513: "item 0: path is longer than 1024 bytes",
            "/a": "item 0: path " + rel,
            "a\\b": "item 0: path " + rel,
            "..": "item 0: path must not traverse",
            "../a": "item 0: path must not traverse",
            "a/../b": "item 0: path must not traverse",
            "a/..": "item 0: path must not traverse",
        }
        for p, want in cases.items():
            self.assertEqual(self.v(manifest([item(path=p)])), want, repr(p))
        self.assertIsNone(self.v(manifest([item(path="x" * 1024)])))
        self.assertIsNone(self.v(manifest([item(path="a/..b")])))
        two = [item(), item(i="b")]
        self.assertEqual(self.v(manifest(two)), "item 1: duplicate path")

    def test_title_description_content_type(self):
        self.assertEqual(self.v(manifest([item(title=" \t")])), "item 0: title is required")
        self.assertEqual(self.v(manifest([item(title="\u0085 ")])), "item 0: title is required")
        self.assertIsNone(self.v(manifest([item(title="\u001f")])))
        self.assertEqual(
            self.v(manifest([item(title="t" * 121)])), "item 0: title is longer than 120 characters"
        )
        self.assertEqual(
            self.v(manifest([item(description="d" * 2001)])),
            "item 0: description is longer than 2000 characters",
        )
        self.assertIsNone(self.v(manifest([item(description="d" * 2000)])))
        self.assertEqual(self.v(manifest([item(content_type=" ")])), "item 0: content_type is required")

    def test_rule_order_within_an_item(self):
        # id is checked before path, path before title
        self.assertEqual(
            self.v(manifest([item(i="!", path="", title="")])),
            "item 0: id must match [A-Za-z0-9._-]{1,64}",
        )
        self.assertEqual(self.v(manifest([item(path="", title="")])), "item 0: path is required")

    def test_size_limit_is_strictly_greater_in_decode(self):
        base = manifest([item()])
        self.assertIsNone(self.v(base + b" " * (262144 - len(base))))
        self.assertEqual(
            self.v(base + b" " * (262145 - len(base))), "manifest is larger than 262144 bytes"
        )


# --------------------------------------------------------------------------
# manifest bytes


class TestManifestBytes(Base):
    def test_shape_and_escaping(self):
        self.put("a.png")
        self.put("b.png")
        t = self.tsv('a.png\ta<b>&"c é\n')
        self.build_ok("--items", t)
        raw = self.read_manifest_bytes()
        pairs = json.loads(raw, object_pairs_hook=lambda p: p)
        self.assertEqual([k for k, _ in pairs], ["chute_set", "items"])
        for it in pairs[1][1]:
            self.assertEqual(tuple(k for k, _ in it), cs.MANIFEST_KEYS)
        self.assertEqual(cs.MANIFEST_KEYS, ("id", "path", "title", "description", "content_type"))
        self.assertTrue(raw.startswith(b'{"chute_set":1,"items":[{'), raw[:40])
        self.assertIn(b'"description":""', raw)
        self.assertIn('"title":"a\\u003cb\\u003e\\u0026\\"c é"'.encode("utf-8"), raw)
        self.assertNotIn(b" ", raw.replace(b"c \xc3\xa9", b""))

    def test_go_json_escapes(self):
        got = cs.go_json({"t": "<>&  \n\t\"\\\x01\b\f\x7fé"})
        self.assertEqual(
            got,
            b'{"t":"\\u003c\\u003e\\u0026\\u2028\\u2029\\n\\t\\"\\\\\\u0001\\b\\f\x7f\xc3\xa9"}',
        )

    def test_manifest_out_writes_the_exact_bytes(self):
        self.put("a.png")
        mo = os.path.join(self.tmp, "m.json")
        self.build_ok("--manifest-out", mo)
        with open(mo, "rb") as f:
            self.assertEqual(f.read(), self.read_manifest_bytes())


# --------------------------------------------------------------------------
# determinism and archive layout


class TestDeterminism(Base):
    def test_two_builds_are_byte_identical(self):
        self.put("a.png")
        self.put("b.png", PNG * 1000)
        self.put("notes.md", b"# notes\n")
        self.build_ok()
        other = os.path.join(self.tmp, "other.zip")
        rc, out, err = run("--out", other, self.dir)
        self.assertEqual(rc, 0, err)
        with open(self.out, "rb") as a, open(other, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_entry_layout(self):
        self.put("b.png")
        self.put("a.png")
        self.build_ok()
        with zipfile.ZipFile(self.out) as zf:
            infos = zf.infolist()
            self.assertEqual([i.filename for i in infos], [".chute/set.json", "a.png", "b.png"])
            for i in infos:
                self.assertEqual(i.date_time, (1980, 1, 1, 0, 0, 0), i.filename)
                self.assertEqual(i.compress_type, zipfile.ZIP_DEFLATED, i.filename)
                self.assertEqual(i.create_system, 3, i.filename)
                self.assertEqual(i.external_attr, 0o100644 << 16, i.filename)
                self.assertEqual(i.extra, b"", i.filename)
                self.assertEqual(i.flag_bits & 0x1, 0, i.filename)
                self.assertFalse(i.is_dir(), i.filename)
            self.assertEqual(zf.comment, b"")
            self.assertEqual(zf.read("b.png"), PNG)

    def test_members_follow_display_order(self):
        self.put("a.png")
        self.put("b.png")
        self.build_ok("--cover", "b.png")
        with zipfile.ZipFile(self.out) as zf:
            self.assertEqual(zf.namelist(), [".chute/set.json", "b.png", "a.png"])

    def test_built_archive_passes_check(self):
        self.put("a.png")
        self.build_ok()
        self.assertIsNone(cs.check_archive(self.out))
        rc, out, err = run("check", self.out)
        self.assertEqual((rc, out, err), (0, "", ""))


class TestAtomicity(Base):
    def test_failing_build_leaves_nothing(self):
        self.put("a.png")
        t = self.tsv("a.png\t \n")
        rc, out, err = self.build("--items", t)
        self.assertEqual(rc, 3)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["items.tsv", "renders"])

    def test_failing_build_keeps_a_previous_archive(self):
        self.put("a.png")
        with open(self.out, "wb") as f:
            f.write(b"previous")
        self.put("foo.xyz", b"x")
        rc, out, err = self.build()
        self.assertEqual(rc, 3)
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), b"previous")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["out.zip", "renders"])

    def test_failed_self_check_leaves_nothing(self):
        self.put("a.png")
        with open(self.out, "wb") as f:
            f.write(b"previous")
        err = io.StringIO()
        with mock.patch.object(cs, "check_archive", return_value="boom"), contextlib.redirect_stderr(err):
            rc = cs.main(["--out", self.out, self.dir])
        self.assertEqual(rc, 1)
        self.assertIn("self-check", err.getvalue())
        self.assertIn("boom", err.getvalue())
        self.assertIn(cs.CONFORMED_TO_CHUTE, err.getvalue())
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), b"previous")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["out.zip", "renders"])


# --------------------------------------------------------------------------
# Parse mirror on hand-made archives


def set_flag(path, bit):
    """Set a general-purpose flag bit on every local and central header."""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        while i != -1:
            (flags,) = struct.unpack_from("<H", data, i + off)
            struct.pack_into("<H", data, i + off, flags | bit)
            i = data.find(sig, i + 4)
    with open(path, "wb") as f:
        f.write(bytes(data))


class TestCheck(Base):
    M = ".chute/set.json"

    def one(self, path="a.png", **kw):
        return manifest([item(path=path, **kw)])

    def chk(self, entries):
        return cs.check_archive(self.raw_zip(entries))

    def test_accepts_a_minimal_set(self):
        self.assertIsNone(self.chk([(self.M, self.one(), None, None, None), ("a.png", PNG, None, None, None)]))

    def test_symlink_member(self):
        got = self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0o120777 << 16, None, None)])
        self.assertEqual(got, 'item 0: "a.png" is not a regular file')

    def test_directory_member_by_mode_and_by_slash(self):
        got = self.chk([(self.M, self.one(), None, None, None), ("a.png", b"", 0o040755 << 16, None, None)])
        self.assertEqual(got, 'item 0: "a.png" is not a regular file')
        got = self.chk([(self.M, self.one(path="d/"), None, None, None), ("d/", b"", 0o100644 << 16, None, None)])
        self.assertEqual(got, 'item 0: "d/" is not a regular file')

    def test_other_irregular_types(self):
        for t in (0o010000, 0o020000, 0o060000, 0o140000):
            got = self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", (t | 0o644) << 16, None, None)])
            self.assertEqual(got, 'item 0: "a.png" is not a regular file', oct(t))

    def test_zero_type_field_is_regular_like_go(self):
        self.assertIsNone(self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0o644 << 16, None, None)]))
        # setuid/sticky bits do not make a file irregular in Go
        self.assertIsNone(self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0o104755 << 16, None, None)]))

    def test_msdos_creator_directory_attribute(self):
        got = self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0x10, 0, None)])
        self.assertEqual(got, 'item 0: "a.png" is not a regular file')
        self.assertIsNone(self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0x01, 11, None)]))
        # an unknown creator system yields mode 0: regular
        self.assertIsNone(self.chk([(self.M, self.one(), None, None, None), ("a.png", b"x", 0o120777 << 16, 7, None)]))

    def test_missing_member_uses_go_quote(self):
        # (a backslash would fail Decode's path rule first, so pin '"', a
        # control character and printable non-ASCII here; go_quote's own
        # cases are in TestGoQuote)
        got = self.chk([(self.M, self.one(path='x"\ty\u00e9.png'), None, None, None), ("a.png", b"x", None, None, None)])
        self.assertEqual(got, 'item 0: no archive member named "x\\"\\ty\u00e9.png"')

    def test_duplicate_entry_names(self):
        with self.assertWarns(UserWarning):
            got = self.chk(
                [(self.M, self.one(), None, None, None), ("a.png", b"x", None, None, None), ("a.png", b"y", None, None, None)]
            )
        self.assertEqual(got, "archive has duplicate entry names")

    def test_more_than_10000_entries(self):
        entries = [(self.M, self.one(), None, None, None)]
        entries += [(f"e{i}", b"", None, None, zipfile.ZIP_STORED) for i in range(10000)]
        self.assertEqual(self.chk(entries), "archive holds more than 10000 entries")

    def test_no_manifest_is_not_a_set(self):
        self.assertEqual(self.chk([("a.png", PNG, None, None, None)]), "archive carries no set manifest")
        p = os.path.join(self.tmp, "nz.zip")
        with open(p, "wb") as f:
            f.write(b"not a zip")
        self.assertEqual(cs.check_archive(p), "archive carries no set manifest: not a zip archive")

    def test_manifest_size_boundary_is_ge_in_parse(self):
        base = self.one()
        at = base + b" " * (262144 - len(base))
        below = base + b" " * (262143 - len(base))
        self.assertEqual(
            self.chk([(self.M, at, None, None, None), ("a.png", b"x", None, None, None)]),
            "manifest is larger than 262144 bytes",
        )
        self.assertIsNone(self.chk([(self.M, below, None, None, None), ("a.png", b"x", None, None, None)]))

    def test_manifest_with_unsupported_method(self):
        got = self.chk([(self.M, self.one(), None, None, zipfile.ZIP_BZIP2), ("a.png", b"x", None, None, None)])
        self.assertEqual(got, "manifest member could not be opened")

    def test_declared_member_size(self):
        got = self.chk([(self.M, self.one(), None, None, None), ("a.png", b"\0" * (64 * MIB + 1), None, None, None)])
        self.assertEqual(got, "item 0: declares more than 67108864 bytes")

    def test_decode_errors_surface_verbatim(self):
        got = self.chk([(self.M, self.one(title=""), None, None, None), ("a.png", b"x", None, None, None)])
        self.assertEqual(got, "item 0: title is required")

    def test_miscased_key_is_producer_only(self):
        it = item()
        it["Title"] = it.pop("title")
        got = self.chk([(self.M, manifest([it]), None, None, None), ("a.png", b"x", None, None, None)])
        self.assertIsNotNone(got)
        self.assertTrue(got.startswith("producer-only: "), got)
        self.assertIn('"Title"', got)

    def test_missing_description_is_producer_only(self):
        it = item()
        del it["description"]
        got = self.chk([(self.M, manifest([it]), None, None, None), ("a.png", b"x", None, None, None)])
        self.assertTrue(got.startswith("producer-only: "), got)
        self.assertIn("description", got)

    def test_encrypted_member_is_producer_only(self):
        p = self.raw_zip([(self.M, self.one(), None, None, None), ("a.png", b"x", None, None, None)])
        set_flag(p, 0x1)
        got = cs.check_archive(p)
        self.assertTrue(got.startswith("producer-only: "), got)
        self.assertIn("encrypted", got)

    def test_check_cli_prints_server_wording_only(self):
        p = self.raw_zip([(self.M, self.one(title=""), None, None, None), ("a.png", b"x", None, None, None)])
        rc, out, err = run("check", p)
        self.assertEqual(rc, 3)
        self.assertEqual(out, "item 0: title is required\n")
        self.assertNotIn("hint", out + err)
        self.assertNotIn("chute-set:", out)


class TestGoQuote(unittest.TestCase):
    def test_cases(self):
        q = cs.go_quote
        self.assertEqual(q("a.png"), '"a.png"')
        self.assertEqual(q('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(q("\n\t\r\a\b\f\v"), '"\\n\\t\\r\\a\\b\\f\\v"')
        self.assertEqual(q("\x01\x7f"), '"\\x01\\x7f"')
        self.assertEqual(q("é \U0001F600"), '"é \U0001F600"')
        self.assertEqual(q("​ "), '"\\u200b\\u00a0"')
        self.assertEqual(q("\U000e0001"), '"\\U000e0001"')


class TestGoTrim(unittest.TestCase):
    def test_space_set(self):
        self.assertEqual(cs.go_trim("\t\n\v\f\r \u0085 x       　"), "x")
        self.assertEqual(cs.go_trim("\u001cx\u001f"), "\u001cx\u001f")
        self.assertEqual(cs.go_trim("​x"), "​x")


class TestHeader(unittest.TestCase):
    def test_conformed_to(self):
        self.assertRegex(cs.CONFORMED_TO_CHUTE, r"^[0-9a-f]{40}$")
        with open(SCRIPT, "rb") as f:
            head = f.read()
        self.assertTrue(head.startswith(b"#!/usr/bin/env python3\n"))
        self.assertNotIn(b"\r", head)


if __name__ == "__main__":
    unittest.main()
