from pathlib import Path


def test_termux_uses_native_crypto_and_wheel_first_policy():
    root = Path(__file__).resolve().parents[1]
    script = (root / "start.sh").read_text(encoding="utf-8")

    for package in ("python-cryptography", "python-pillow", "python-bcrypt", "python-pynacl"):
        assert package in script

    assert "PIP_ONLY_BINARY=\"PyNaCl,bcrypt,cryptography\"" in script
    assert "PIP_PREFER_BINARY=1" in script
    assert "--only-binary=:all:" in script
    assert "--no-deps 'paramiko>=3.5,<6'" in script
    assert "termux_native_dependencies_ok" in script
    assert "pkg-config" in script
    assert "libsodium" in script


def test_termux_source_fallback_never_rebuilds_native_crypto_dependencies():
    root = Path(__file__).resolve().parents[1]
    script = (root / "start.sh").read_text(encoding="utf-8")

    termux_block = script[script.index('if [ "$IS_TERMUX" -eq 1 ]; then'):]
    assert "install_native_packages python-cryptography python-pillow python-bcrypt python-pynacl" in termux_block
    assert "SODIUM_INSTALL=system" in termux_block
    assert "pip check" in termux_block
