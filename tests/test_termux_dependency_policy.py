from pathlib import Path


def test_normal_termux_web_start_excludes_sftp_dependencies():
    root = Path(__file__).resolve().parents[1]
    script = (root / "start.sh").read_text(encoding="utf-8")

    assert "python-cryptography python-pillow" in script
    assert "python-bcrypt" not in script
    assert "python-pynacl" not in script
    assert "paramiko>=3.5,<6" not in script
    assert '"$ROOT[sftp]"' not in script
    assert "PIP_ONLY_BINARY=\"cryptography\"" in script
    assert "--only-binary=:all:" in script
    assert "termux_native_dependencies_ok" in script
    assert "pip check" in script


def test_optional_sftp_starter_uses_termux_pkg_for_pynacl_and_bcrypt():
    root = Path(__file__).resolve().parents[1]
    script = (root / "start-sftp.sh").read_text(encoding="utf-8")

    assert "pkg install -y python-cryptography python-bcrypt python-pynacl" in script
    assert "--system-site-packages" in script
    assert "--only-binary=:all: --no-deps 'paramiko>=3.5,<6'" in script
    assert 'importlib.import_module(module)' in script
    assert 'for module in ("cryptography", "bcrypt", "nacl")' in script
    assert '"$ROOT[sftp]"' in script  # non-Termux explicit SFTP path remains available


def test_termux_web_source_fallback_only_builds_web_runtime_packages():
    root = Path(__file__).resolve().parents[1]
    script = (root / "start.sh").read_text(encoding="utf-8")

    assert "clang make pkg-config libffi openssl" in script
    assert "libsodium" not in script
    assert "PyNaCl" not in script
    assert "bcrypt" not in script
