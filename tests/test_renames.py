"""scripts/_renames.py, scripts/rename and the checker's private-name rules. Every name below is invented."""

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from _renames import Renames  # noqa: E402


def load(name):
    path = str(ROOT / "scripts" / name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


rename = load("rename")
checker = load("check-secrets")

TABLE = "# comment\nzorblax\tbanana\nmortoc-quux\thost-apple\nGlimmerCorp\tAcmeCorp\n\n"


def table():
    return Renames.parse(TABLE)


def test_replacement_covers_case_variants_and_respects_word_boundaries():
    new, n = table().sanitize("Zorblax and zorblax and ZORBLAX but not zorblaxer or xzorblax.")
    assert new == "Banana and banana and BANANA but not zorblaxer or xzorblax."
    assert n == 3


def test_longest_name_wins_and_hyphenated_names_work():
    new, _ = table().sanitize("host mortoc-quux runs quux-zorblax")
    assert new == "host host-apple runs quux-banana"


def test_an_explicit_line_beats_the_generated_variant():
    t = Renames.parse("glimmer\tfoo\nGlimmer\tBarely\n")
    assert t.sanitize("glimmer Glimmer GLIMMER")[0] == "foo Barely FOO"


def test_loose_replaces_inside_identifiers_too():
    t = table()
    assert t.sanitize("fooZorblaxBar")[1] == 0
    assert t.sanitize("fooZorblaxBar", loose=True)[0] == "foobananaBar"


def test_detection_is_stricter_than_replacement():
    hits = table().find("clean line\nthe fooZORBLAXbar one\nmortoc-quux here")
    assert hits == [(2, "banana"), (3, "host-apple")]


def test_reveal_turns_aliases_back_for_reading_only():
    t = table()
    assert t.reveal("Banana lives on host-apple") == "Zorblax lives on mortoc-quux"


def test_adding_is_validated():
    t = table()
    assert t.problem_with("newname", "kiwi") is None
    assert "contains the private name" in t.problem_with("newname", "newname-ish")
    assert "already used" in t.problem_with("other", "banana")
    assert "already in the table" in t.problem_with("ZORBLAX", "cherry")
    assert "contains another private name" in t.problem_with("other", "my-zorblax")
    assert "required" in t.problem_with("", "x")


def test_a_malformed_table_is_reported_with_its_line_number():
    with pytest.raises(ValueError, match="line 2"):
        Renames.parse("ok\tfine\nbroken line without a tab\n")


def make_repo(tmp_path, files):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return tmp_path


def test_the_cli_dry_runs_first_then_writes_and_never_shows_the_name(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, {"doc.md": "a note about zorblax\nand mortoc-quux\n", "ok.md": "nothing\n"})
    (repo / ".renames").write_text(TABLE)
    monkeypatch.setattr(rename, "ROOT", repo)
    monkeypatch.setenv("RENAMES_FILE", str(repo / ".renames"))

    assert rename.main([]) == 0
    out = capsys.readouterr().out
    assert "would rewrite doc.md: 2" in out and "zorblax" not in out
    assert (repo / "doc.md").read_text().startswith("a note about zorblax")  # dry run changed nothing

    assert rename.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "doc.md:1" in err and "alias: banana" in err and "zorblax" not in err

    assert rename.main(["--write"]) == 0
    assert (repo / "doc.md").read_text() == "a note about banana\nand host-apple\n"
    assert rename.main(["--check"]) == 0


def test_a_file_name_holding_a_private_name_is_reported(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, {"notes-zorblax.md": "fine\n"})
    (repo / ".renames").write_text(TABLE)
    monkeypatch.setattr(rename, "ROOT", repo)
    monkeypatch.setenv("RENAMES_FILE", str(repo / ".renames"))
    assert rename.main(["--check"]) == 1
    assert "(file name)" in capsys.readouterr().err


def test_add_lookup_list_and_reveal(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".renames"
    monkeypatch.setenv("RENAMES_FILE", str(path))
    assert rename.main(["--add", "snorfle", "kiwi"]) == 0
    assert oct(path.stat().st_mode & 0o777) == "0o600"  # it lists the very names it hides
    assert rename.main(["--add", "snorfle", "plum"]) == 2  # duplicate refused
    assert rename.main(["--add", "other", "snorfle-ish"]) == 2  # an alias must not give away another private name
    assert rename.main(["--add", "other", "mango"]) == 0
    capsys.readouterr()
    assert rename.main(["--lookup", "KIWI"]) == 0 and "snorfle" in capsys.readouterr().out
    assert rename.main(["--lookup", "nothing-here"]) == 1
    assert rename.main(["--list"]) == 0 and capsys.readouterr().out.count("\t") == 2
    (tmp_path / "x.md").write_text("see kiwi\n")
    assert rename.main(["--reveal", str(tmp_path / "x.md")]) == 0
    assert capsys.readouterr().out == "see snorfle\n"
    assert (tmp_path / "x.md").read_text() == "see kiwi\n"  # reveal goes to stdout; the file is untouched


def run_checker(tmp_path, monkeypatch, files, *args):
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    (tmp_path / ".renames").write_text(TABLE)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setenv("RENAMES_FILE", str(tmp_path / ".renames"))
    return checker.main([*args, *files])


def test_the_checker_fails_on_private_names_and_prints_only_the_alias(tmp_path, monkeypatch, capsys):
    assert run_checker(tmp_path, monkeypatch, {"a.md": "we ran it on mortoc-quux\n", "b.md": "fine\n"}) == 1
    err = capsys.readouterr().err
    assert "a.md:1" in err and "alias: host-apple" in err and "mortoc-quux" not in err
    assert run_checker(tmp_path, monkeypatch, {"c.md": "we ran it on host-apple\n"}) == 0


def test_the_checker_reads_commit_messages(tmp_path, monkeypatch, capsys):
    message = tmp_path / "MSG"
    message.write_text("fix the zorblax path\n\n# Please enter the commit message\n")
    (tmp_path / ".renames").write_text(TABLE)
    monkeypatch.setenv("RENAMES_FILE", str(tmp_path / ".renames"))
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    assert checker.main(["--message", str(message)]) == 1
    assert "commit message:1" in capsys.readouterr().err
    message.write_text("fix the banana path\n# zorblax only in a git comment line\n")
    assert checker.main(["--message", str(message)]) == 0


def test_without_a_table_the_name_check_is_skipped_as_in_ci(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.md").write_text("zorblax\n")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setenv("RENAMES_FILE", str(tmp_path / "does-not-exist"))
    assert checker.main(["a.md"]) == 0


def test_the_table_itself_can_never_be_committed(tmp_path, monkeypatch, capsys):
    assert run_checker(tmp_path, monkeypatch, {".renames": TABLE}) == 1
    assert "private-name table" in capsys.readouterr().err
