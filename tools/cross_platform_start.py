"""Cross-platform virtualenv setup and launcher for macOS and Windows Git Bash.

Invoked by start.sh. Uses only the Python standard library before setup.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = {(3, 11), (3, 12), (3, 13), (3, 14)}
REQUIRED = ("flask", "argon2-cffi", "defusedxml", "Pillow",
            "reportlab", "pypdf", "waitress", "cryptography", "watchdog", "tzdata")


def venv_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def supported_runtime(executable: str) -> bool:
    result = subprocess.run(
        [executable, "-c",
         "import platform,sys; assert platform.python_implementation() == 'CPython'; "
         "assert sys.version_info[:2] in {(3,11),(3,12),(3,13),(3,14)}; "
         "assert sys.version_info.releaselevel == 'final'"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def probe_candidates() -> list[list[str]]:
    # Prefer 3.14; never alter the system interpreter.
    if os.name == "nt":
        return [["py", "-3.14"], ["py", "-3.13"], ["py", "-3.12"],
                ["py", "-3.11"], ["python"], ["python3"]]
    return [["python3.14"], ["python3.13"], ["python3.12"],
            ["python3.11"], ["python3"], ["python"]]


def choose_bootstrap() -> list[str]:
    explicit = os.getenv("PYTHON")
    candidates = [[explicit]] if explicit else probe_candidates()
    for command in candidates:
        try:
            check = subprocess.run(
                [*command, "-c",
                 "import platform,sys; assert platform.python_implementation() == 'CPython'; "
                 "assert sys.version_info[:2] in {(3,11),(3,12),(3,13),(3,14)}; "
                 "assert sys.version_info.releaselevel == 'final'"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if check.returncode == 0:
                return command
        except OSError:
            continue
    raise RuntimeError("Kein freigegebenes CPython 3.11-3.14 gefunden. "
                       "Installiere vorzugsweise Python 3.14 oder setze PYTHON.")


def is_ready(python: Path) -> bool:
    if not python.is_file() or not supported_runtime(str(python)):
        return False
    check_script = """
import importlib.metadata as m
for name in %r:
    m.version(name)
m.version('simpleoffice4me')
""" % (REQUIRED,)
    check = subprocess.run([str(python), "-c", check_script],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
    if check.returncode:
        return False
    return subprocess.run([str(python), "-m", "pip", "check"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


def add_server_environment(args: argparse.Namespace) -> None:
    mapping = {
        "google_json": "SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE",
        "google_redirect_uri": "SIMPLEOFFICE_GOOGLE_REDIRECT_URI",
        "trusted_proxy_hops": "SIMPLEOFFICE_TRUSTED_PROXY_HOPS",
        "host": "SIMPLEOFFICE_HOST",
        "port": "SIMPLEOFFICE_PORT",
        "threads": "SIMPLEOFFICE_WSGI_THREADS",
        "channel_timeout": "SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT",
    }
    for key, variable in mapping.items():
        value = getattr(args, key)
        if value is not None:
            os.environ[variable] = str(value)
    if args.public_url:
        if not args.public_url.startswith("https://"):
            raise ValueError("--public-url muss mit https:// beginnen.")
        os.environ["SIMPLEOFFICE_GOOGLE_REDIRECT_URI"] = (
            args.public_url.rstrip("/") + "/auth/google/callback"
        )
    if args.secret_key_file:
        key = Path(args.secret_key_file).read_text(encoding="utf-8").splitlines()
        if not key or not key[0]:
            raise ValueError("Session-Schluesseldatei ist leer.")
        os.environ["SIMPLEOFFICE_SECRET_KEY"] = key[0]
    if args.reindex_osm:
        os.environ["SIMPLEOFFICE_OSM_REINDEX_ON_START"] = "1"


def main() -> int:
    parser = argparse.ArgumentParser(description="SimpleOffice4Me macOS/Windows-Starter")
    parser.add_argument("--venv", required=True)
    parser.add_argument("--mode", choices=("auto", "setup", "run"), default="auto")
    parser.add_argument("--google-json")
    parser.add_argument("--public-url")
    parser.add_argument("--google-redirect-uri")
    parser.add_argument("--secret-key-file")
    parser.add_argument("--trusted-proxy-hops", type=int)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--channel-timeout", type=int)
    parser.add_argument("--reindex-osm", action="store_true")
    parser.add_argument("--check-system", action="store_true")
    parser.add_argument("--standalone", action="store_true")
    args = parser.parse_args()
    add_server_environment(args)

    target = Path(args.venv).expanduser().resolve()
    if target == ROOT or target == target.parent:
        parser.error("Ungueltiger venv-Pfad.")
    executable = venv_python(target)
    print(f"Plattform: {platform.system()}, venv: {target}", flush=True)

    if args.check_system:
        runtime = str(executable) if executable.is_file() and supported_runtime(str(executable)) else choose_bootstrap()
        return subprocess.call([*(runtime if isinstance(runtime, list) else [runtime]),
                                str(ROOT / "tools" / "system_requirements.py")])

    if args.mode == "run" and not is_ready(executable):
        parser.error("Die angegebene venv ist nicht startbereit; zuerst 'setup' ausfuehren.")

    if args.mode == "setup" or not is_ready(executable):
        if executable.exists() and not supported_runtime(str(executable)):
            parser.error("Vorhandene venv enthaelt eine nicht unterstuetzte Python-Version. "
                         "Bitte einen anderen venv-Pfad angeben; nichts wird geloescht.")
        if not executable.exists():
            if target.exists() and any(target.iterdir()):
                parser.error("Ziel ist ein nichtleeres Verzeichnis ohne gueltige venv.")
            if args.standalone:
                parser.error("--standalone ist auf dieser Plattform nicht implementiert; "
                             "Python 3.14 installieren.")
            bootstrap = choose_bootstrap()
            subprocess.run([*bootstrap, "-m", "venv", str(target)], check=True)
        subprocess.run([str(executable), "-m", "pip", "install",
                        "--disable-pip-version-check", "--editable", str(ROOT)], check=True)
        subprocess.run([str(executable), "-m", "pip", "check"], check=True)
        if not is_ready(executable):
            parser.error("venv ist nach der Einrichtung nicht startbereit.")
        if args.mode == "setup":
            print("Einrichtung abgeschlossen.")
            return 0

    os.chdir(ROOT)
    os.execv(str(executable), [str(executable), "-m", "tools.launcher", "start"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError, RuntimeError) as exc:
        print(f"Start fehlgeschlagen: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
