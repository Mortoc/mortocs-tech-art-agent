from tech_art_agent import project_memory as pm


def test_no_project_means_no_project_memory():
    assert pm.read(None) == {"available": False, "text": "", "prev": None}
    assert pm.read("$JOB")["available"] is False
    try:
        pm.write(None, "x", None)
        raise AssertionError("should refuse")
    except RuntimeError as error:
        assert "$JOB" in str(error)


def test_round_trip_with_one_step_of_rollback(tmp_path):
    pm.write(str(tmp_path), "new", "old")
    assert pm.read(str(tmp_path)) == {"available": True, "text": "new", "prev": "old"}
    assert (tmp_path / ".tech-art-agent" / "memory.md").read_text() == "new"
    pm.write(str(tmp_path), "newer", None)
    assert pm.read(str(tmp_path))["prev"] is None


def test_a_project_from_the_houdini_only_days_keeps_its_file(tmp_path):
    (tmp_path / ".houdini-pi").mkdir()
    (tmp_path / ".houdini-pi" / "memory.md").write_text("old conventions")
    assert pm.read(str(tmp_path))["text"] == "old conventions"
    pm.write(str(tmp_path), "edited", "old conventions")
    assert (tmp_path / ".houdini-pi" / "memory.md").read_text() == "edited"
    assert not (tmp_path / ".tech-art-agent").exists()


def test_once_the_new_file_exists_it_wins(tmp_path):
    (tmp_path / ".houdini-pi").mkdir()
    (tmp_path / ".houdini-pi" / "memory.md").write_text("old")
    (tmp_path / ".tech-art-agent").mkdir()
    (tmp_path / ".tech-art-agent" / "memory.md").write_text("new")
    assert pm.read(str(tmp_path))["text"] == "new"
