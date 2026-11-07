import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    path = str(ROOT / "scripts" / "check-secrets")
    loader = importlib.machinery.SourceFileLoader("check_secrets", path)
    spec = importlib.util.spec_from_loader("check_secrets", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cs = load()


def run(tmp_path, files: dict[str, str], monkeypatch, *args):
    """Run the checker over files laid out in a fake repo."""
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    return cs.main([*args, *files])


def test_a_clean_file_passes(tmp_path, monkeypatch, capsys):
    assert run(tmp_path, {"a.py": "x = 1\n"}, monkeypatch, "--strict") == 0


def test_secret_shaped_strings_fail_without_printing_them(tmp_path, monkeypatch, capsys):
    key = "sk-or-v1-" + "a1b2c3d4" * 6
    token = "gh" + "p_" + "A1b2C3d4E5" * 4
    age = "AGE-SECRET-KEY-1" + "Q" * 40
    assert run(tmp_path, {"a.py": f"KEY = '{key}'\nTOK = '{token}'\n", "b.txt": age + "\n"}, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "OpenRouter" in err and "GitHub token" in err and "age SECRET key" in err
    assert key not in err and token not in err and "QQQQQQ" not in err  # the report never leaks what it found


def test_files_that_must_never_be_committed_fail_by_name(tmp_path, monkeypatch, capsys):
    for name in (".env", "deploy/.env.production", "keys.txt", "wrapped.age", "id_ed25519", "server.pem"):
        assert run(tmp_path, {name: "x\n"}, monkeypatch) == 1, name
    assert run(tmp_path, {".env.example": "OPENROUTER_API_KEY=\n"}, monkeypatch) == 0


def test_a_sops_encrypted_env_is_allowed_but_plaintext_in_secrets_is_not(tmp_path, monkeypatch, capsys):
    good = "OPENROUTER_API_KEY=ENC[AES256_GCM,data:abc,iv:x,tag:y,type:str]\nsops_age__list_0__map_recipient=age1xyz\nsops_mac=ENC[AES256_GCM,data:m,iv:i,tag:t,type:str]\n"
    assert run(tmp_path, {"secrets/app.env": good}, monkeypatch) == 0
    assert run(tmp_path, {"secrets/app.env": "OPENROUTER_API_KEY=plain\n"}, monkeypatch) == 1
    assert "PLAINTEXT" in capsys.readouterr().err
    half = good.replace("sops_age", "OTHER=visible\nsops_age", 1)
    assert run(tmp_path, {"secrets/app.env": half}, monkeypatch) == 1
    assert "partially plaintext" in capsys.readouterr().err


def test_company_data_is_a_warning_that_strict_turns_into_a_failure(tmp_path, monkeypatch, capsys):
    body = "ref = 'op://Some Vault/Item/field'\nhome = '/home/someone/work'\nip = '192.168.1.20'\nmail = 'a.person@corp.com'\n"  # secrets-ok: fixtures that the checker is supposed to flag
    assert run(tmp_path, {"doc.md": body}, monkeypatch) == 0
    err = capsys.readouterr().err
    assert err.count("WARNING") == 4
    assert run(tmp_path, {"doc.md": body}, monkeypatch, "--strict") == 1


def test_a_noreply_address_and_a_suppressed_line_are_fine(tmp_path, monkeypatch, capsys):
    body = "12345+me@users.noreply.github.com\nsee op://vault/item  # secrets-ok: documentation example\n"
    assert run(tmp_path, {"doc.md": body}, monkeypatch, "--strict") == 0


def test_this_repository_is_clean_right_now():
    # The real tree, exactly as CI checks it.
    import subprocess

    result = subprocess.run([str(ROOT / "scripts" / "check-secrets"), "--all", "--strict"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
