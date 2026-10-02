"""ops.load_dotenv: appen læser selv .env, når den kører direkte (fx på Windows)."""
import ops


def test_reads_keys_and_ignores_comments_blank_lines_and_quotes(tmp_path, monkeypatch):
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text('\ufeff# kommentar\n\nA_KEY=abc\nB_KEY = "med anførselstegn"\nexport C_KEY=\'x\'\nD_KEY=\nikke en linje\n', "utf-8")
    assert sorted(ops.load_dotenv(tmp_path / ".env")) == ["A_KEY", "B_KEY", "C_KEY"]
    import os
    assert (os.environ["A_KEY"], os.environ["B_KEY"], os.environ["C_KEY"]) == ("abc", "med anførselstegn", "x")
    assert "D_KEY" not in os.environ                                   # tom værdi sættes ikke


def test_variables_already_set_with_setx_or_docker_win(tmp_path, monkeypatch):
    monkeypatch.setenv("FAMILIEPLAN_PASSWORD", "fra-setx")
    (tmp_path / ".env").write_text("FAMILIEPLAN_PASSWORD=fra-env-fil\n", "utf-8")
    assert ops.load_dotenv(tmp_path / ".env") == []
    import os
    assert os.environ["FAMILIEPLAN_PASSWORD"] == "fra-setx"


def test_a_missing_file_or_the_same_file_twice_is_fine(tmp_path, monkeypatch):
    monkeypatch.delenv("E_KEY", raising=False)
    (tmp_path / ".env").write_text("E_KEY=1\n", "utf-8")
    assert ops.load_dotenv(tmp_path / "findes-ikke", tmp_path / ".env", tmp_path / ".env") == ["E_KEY"]
