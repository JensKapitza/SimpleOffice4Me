#!/usr/bin/env bash
# Linux/Termux starter. Owns only the local .venv in this project directory.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
IS_TERMUX=0
CHECK_SYSTEM=0
NATIVE_PM=""
DISTRO_NAME="Linux"
USE_SYSTEM_SITE_PACKAGES=0
NATIVE_PACKAGES_FOUND=0

usage() {
  cat <<'EOF'
SimpleOffice4Me starten

Optionen:
  --google-json DATEI         Google OAuth JSON-Datei (Web-Anwendung)
  --public-url URL            Öffentliche HTTPS-Basis-URL; überschreibt die JSON-Callback-URI
  --google-redirect-uri URL   Vollständige Google OAuth Callback-URL
  --secret-key-file DATEI     Datei mit dauerhaftem SimpleOffice-Session-Schlüssel
  --trusted-proxy-hops ANZAHL Anzahl vertrauenswürdiger Reverse-Proxies
  --host ADRESSE              Bind-Adresse (Standard: 127.0.0.1)
  --port PORT                 HTTP-Port (Standard: aus Ersteinrichtung)
  --threads ANZAHL            Waitress-Worker-Threads (Standard: 4)
  --channel-timeout SEKUNDEN  Leerlaufzeit einer Verbindung (Standard: 120)
  --reindex-osm               OSM-Index aus vorhandenem Download neu aufbauen
  --check-system              Systemwerkzeuge prüfen, ohne Serverstart/Installation
  --help                      Diese Hilfe anzeigen

Beim Start werden unter Linux zuerst vorhandene/native Distribution-Pakete
verwendet. Unterstützt werden Termux pkg, apt, dnf/yum, pacman, apk und zypper.
Die gefundenen Python-Pakete werden vor dem Dependency-Install gegen die
SimpleOffice-Versionsanforderungen geprüft. Nur fehlende oder zu alte Pakete
fallen anschließend auf pip in der lokalen .venv zurück.

Der normale Webstart installiert ausdrücklich keine optionalen SFTP/SSH-Pakete.
Paramiko, PyNaCl und bcrypt werden erst von ./start-sftp.sh benötigt und dort
separat installiert. Unter Termux kommt cryptography weiterhin bevorzugt aus
pkg; pip probiert für die übrigen Laufzeitpakete zuerst fertige Wheels.

Native Paketinstallation kann mit SIMPLEOFFICE_NATIVE_PACKAGES=0 deaktiviert
werden. Eine vorhandene .venv wird nur dann neu erzeugt, wenn native Pakete
sonst durch ihre Isolation nicht sichtbar wären.

Beispiel:
  ./start.sh --google-json /etc/simpleoffice/google-oauth.json \
    --public-url https://office.example.de --trusted-proxy-hops 1
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --google-json)
      [ "$#" -ge 2 ] || { echo "--google-json benötigt einen Dateipfad." >&2; exit 2; }
      export SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE="$2"; shift 2 ;;
    --public-url)
      [ "$#" -ge 2 ] || { echo "--public-url benötigt eine URL." >&2; exit 2; }
      case "$2" in
        https://*) ;;
        *) echo "--public-url muss mit https:// beginnen." >&2; exit 2 ;;
      esac
      export SIMPLEOFFICE_GOOGLE_REDIRECT_URI="${2%/}/auth/google/callback"; shift 2 ;;
    --google-redirect-uri)
      [ "$#" -ge 2 ] || { echo "--google-redirect-uri benötigt eine URL." >&2; exit 2; }
      export SIMPLEOFFICE_GOOGLE_REDIRECT_URI="$2"; shift 2 ;;
    --secret-key-file)
      [ "$#" -ge 2 ] || { echo "--secret-key-file benötigt einen Dateipfad." >&2; exit 2; }
      [ -r "$2" ] || { echo "Session-Schlüsseldatei ist nicht lesbar: $2" >&2; exit 2; }
      IFS= read -r SIMPLEOFFICE_SECRET_KEY < "$2" || true
      [ -n "$SIMPLEOFFICE_SECRET_KEY" ] || { echo "Session-Schlüsseldatei ist leer: $2" >&2; exit 2; }
      export SIMPLEOFFICE_SECRET_KEY; shift 2 ;;
    --trusted-proxy-hops)
      [ "$#" -ge 2 ] || { echo "--trusted-proxy-hops benötigt eine Anzahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Proxy-Anzahl muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -le 16 ] || { echo "Proxy-Anzahl muss zwischen 0 und 16 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_TRUSTED_PROXY_HOPS="$2"; shift 2 ;;
    --host)
      [ "$#" -ge 2 ] || { echo "--host benötigt eine Adresse." >&2; exit 2; }
      [ -n "$2" ] || { echo "--host darf nicht leer sein." >&2; exit 2; }
      export SIMPLEOFFICE_HOST="$2"; shift 2 ;;
    --port)
      [ "$#" -ge 2 ] || { echo "--port benötigt eine Zahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Port muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || { echo "Port muss zwischen 1 und 65535 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_PORT="$2"; shift 2 ;;
    --threads)
      [ "$#" -ge 2 ] || { echo "--threads benötigt eine Anzahl." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Thread-Anzahl muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 1 ] && [ "$2" -le 64 ] || { echo "Thread-Anzahl muss zwischen 1 und 64 liegen." >&2; exit 2; }
      export SIMPLEOFFICE_WSGI_THREADS="$2"; shift 2 ;;
    --channel-timeout)
      [ "$#" -ge 2 ] || { echo "--channel-timeout benötigt Sekunden." >&2; exit 2; }
      case "$2" in *[!0-9]*|'') echo "Timeout muss eine ganze Zahl sein." >&2; exit 2 ;; esac
      [ "$2" -ge 10 ] && [ "$2" -le 3600 ] || { echo "Timeout muss zwischen 10 und 3600 Sekunden liegen." >&2; exit 2; }
      export SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT="$2"; shift 2 ;;
    --reindex-osm)
      export SIMPLEOFFICE_OSM_REINDEX_ON_START=1; shift ;;
    --check-system)
      CHECK_SYSTEM=1; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "${TERMUX_VERSION:-}" ] \
  || { [ -n "${PREFIX:-}" ] && [ -x "${PREFIX}/bin/pkg" ]; } \
  || [ -x /data/data/com.termux/files/usr/bin/pkg ] \
  || command -v termux-info >/dev/null 2>&1; then
  IS_TERMUX=1
fi

detect_linux_distribution() {
  if [ "$IS_TERMUX" -eq 1 ]; then
    DISTRO_NAME="Termux"
    return 0
  fi
  if [ -r /etc/os-release ]; then
    # /etc/os-release is the standard Linux distribution metadata source.
    # shellcheck disable=SC1091
    DISTRO_NAME="$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-${ID:-Linux}}")"
  else
    DISTRO_NAME="$(uname -s 2>/dev/null || printf 'Linux')"
  fi
}

detect_native_package_manager() {
  if [ "$IS_TERMUX" -eq 1 ] && command -v pkg >/dev/null 2>&1; then
    NATIVE_PM="pkg"
  elif command -v apt-get >/dev/null 2>&1; then
    NATIVE_PM="apt"
  elif command -v dnf >/dev/null 2>&1; then
    NATIVE_PM="dnf"
  elif command -v yum >/dev/null 2>&1; then
    NATIVE_PM="yum"
  elif command -v pacman >/dev/null 2>&1; then
    NATIVE_PM="pacman"
  elif command -v apk >/dev/null 2>&1; then
    NATIVE_PM="apk"
  elif command -v zypper >/dev/null 2>&1; then
    NATIVE_PM="zypper"
  else
    NATIVE_PM=""
  fi
}

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    if [ -t 0 ]; then
      sudo "$@"
    else
      sudo -n "$@"
    fi
  else
    return 126
  fi
}

native_package_installed() {
  package="$1"
  case "$NATIVE_PM" in
    pkg|apt)
      command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q '^install ok installed$'
      ;;
    dnf|yum|zypper)
      command -v rpm >/dev/null 2>&1 && rpm -q "$package" >/dev/null 2>&1
      ;;
    pacman)
      pacman -Q "$package" >/dev/null 2>&1
      ;;
    apk)
      apk info -e "$package" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

native_package_available() {
  package="$1"
  native_package_installed "$package" && return 0
  case "$NATIVE_PM" in
    pkg)
      pkg show "$package" >/dev/null 2>&1
      ;;
    apt)
      command -v apt-cache >/dev/null 2>&1 && apt-cache show "$package" >/dev/null 2>&1
      ;;
    dnf)
      dnf -q list --available "$package" >/dev/null 2>&1
      ;;
    yum)
      yum -q list available "$package" >/dev/null 2>&1
      ;;
    pacman)
      pacman -Si "$package" >/dev/null 2>&1
      ;;
    apk)
      apk search -e "$package" 2>/dev/null | grep -q .
      ;;
    zypper)
      zypper --non-interactive info "$package" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

install_native_packages() {
  [ "$#" -gt 0 ] || return 0
  case "$NATIVE_PM" in
    pkg)
      pkg install -y "$@"
      ;;
    apt)
      run_privileged apt-get install -y --no-install-recommends "$@"
      ;;
    dnf)
      run_privileged dnf install -y "$@"
      ;;
    yum)
      run_privileged yum install -y "$@"
      ;;
    pacman)
      run_privileged pacman -S --needed --noconfirm "$@"
      ;;
    apk)
      run_privileged apk add --no-cache "$@"
      ;;
    zypper)
      run_privileged zypper --non-interactive install -y "$@"
      ;;
    *)
      return 1
      ;;
  esac
}

python_runtime_packages() {
  case "$NATIVE_PM" in
    pkg) printf '%s\n' "python" ;;
    apt) printf '%s\n' "python3 python3-venv" ;;
    dnf|yum) printf '%s\n' "python3" ;;
    pacman) printf '%s\n' "python" ;;
    apk) printf '%s\n' "python3 py3-pip" ;;
    zypper) printf '%s\n' "python3" ;;
    *) printf '%s\n' "" ;;
  esac
}

native_python_packages() {
  case "$NATIVE_PM" in
    pkg)
      printf '%s\n' "tzdata python-cryptography python-pillow"
      ;;
    apt)
      printf '%s\n' "python3-venv python3-flask python3-bs4 python3-reportlab python3-pypdf python3-waitress python3-watchdog python3-cryptography python3-pil"
      ;;
    dnf|yum)
      printf '%s\n' "python3-flask python3-beautifulsoup4 python3-reportlab python3-pypdf python3-waitress python3-watchdog python3-cryptography python3-pillow"
      ;;
    pacman)
      printf '%s\n' "python-flask python-beautifulsoup4 python-reportlab python-pypdf python-waitress python-watchdog python-cryptography python-pillow"
      ;;
    apk)
      printf '%s\n' "py3-flask py3-beautifulsoup4 py3-reportlab py3-pypdf py3-waitress py3-watchdog py3-cryptography py3-pillow"
      ;;
    zypper)
      printf '%s\n' "python3-Flask python3-beautifulsoup4 python3-reportlab python3-pypdf python3-waitress python3-watchdog python3-cryptography python3-Pillow"
      ;;
    *)
      printf '%s\n' ""
      ;;
  esac
}

termux_build_packages() {
  printf '%s\n' "clang make pkg-config libffi openssl"
}

python_is_compatible() {
  command -v "$PYTHON" >/dev/null 2>&1 || return 1
  "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

ensure_python_runtime() {
  if python_is_compatible; then
    return 0
  fi

  if command -v "$PYTHON" >/dev/null 2>&1; then
    current_version="$($PYTHON -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || printf 'unbekannt')"
    echo "Vorhandenes $PYTHON ($current_version) ist zu alt; benötigt wird Python >= 3.10." >&2
  else
    echo "Python 3 wurde nicht gefunden; prüfe native Pakete für $DISTRO_NAME ..."
  fi

  packages="$(python_runtime_packages)"
  [ -n "$packages" ] || return 1

  available=""
  for package in $packages; do
    if native_package_available "$package"; then
      available="$available $package"
    fi
  done
  [ -n "$available" ] || return 1

  echo "  installiere/aktualisiere Python-Laufzeit über $NATIVE_PM:$available"
  # shellcheck disable=SC2086
  install_native_packages $available || return 1
  python_is_compatible
}

prepare_native_python_packages() {
  [ -n "$NATIVE_PM" ] || return 0
  [ "${SIMPLEOFFICE_NATIVE_PACKAGES:-1}" != "0" ] || {
    echo "Native Python-Pakete wurden über SIMPLEOFFICE_NATIVE_PACKAGES=0 deaktiviert."
    return 0
  }

  echo "$DISTRO_NAME erkannt: prüfe Python-Pakete über $NATIVE_PM vor pip ..."
  candidates="$(native_python_packages)"
  [ -n "$candidates" ] || return 0

  missing=""
  for package in $candidates; do
    if native_package_installed "$package"; then
      echo "  nutze Systempaket: $package"
      NATIVE_PACKAGES_FOUND=1
    elif native_package_available "$package"; then
      missing="$missing $package"
    fi
  done

  if [ -n "$missing" ]; then
    echo "  installiere verfügbare Systempakete:$missing"
    # Ein fehlendes sudo oder ein Repository-Problem darf den sicheren pip-Fallback
    # nicht verhindern. Termux bleibt absichtlich strikt, weil native Wheels dort
    # oft nicht zuverlässig gebaut werden können.
    # shellcheck disable=SC2086
    if install_native_packages $missing; then
      NATIVE_PACKAGES_FOUND=1
    elif [ "$IS_TERMUX" -eq 1 ]; then
      echo "Termux-Systempakete konnten nicht installiert werden." >&2
      exit 1
    else
      echo "  Systempakete konnten nicht vollständig installiert werden; pip ergänzt später nur Fehlendes." >&2
    fi
  fi

  if [ "$NATIVE_PACKAGES_FOUND" -eq 1 ]; then
    USE_SYSTEM_SITE_PACKAGES=1
  fi
}

venv_uses_system_site_packages() {
  [ -f "$VENV/pyvenv.cfg" ] && grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV/pyvenv.cfg"
}

native_dependency_versions_ok() {
  "$VENV/bin/python" - <<'PY'
from importlib.metadata import PackageNotFoundError, version

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.version import Version

requirements = {
    "Flask": ">=3.0,<4",
    "beautifulsoup4": ">=4.12,<5",
    "Pillow": ">=12.2,<13",
    "reportlab": ">=4.0,<6",
    "pypdf": ">=5.0,<7",
    "waitress": ">=3.0,<4",
    "cryptography": ">=48.0.1,<51",
    "watchdog": ">=6,<7",
}

problems = []
for distribution, specifier in requirements.items():
    try:
        installed = version(distribution)
    except PackageNotFoundError:
        problems.append(f"{distribution}: fehlt ({specifier})")
        continue
    try:
        compatible = Version(installed) in SpecifierSet(specifier)
    except Exception:
        compatible = False
    if not compatible:
        problems.append(f"{distribution}: {installed} passt nicht zu {specifier}")

if problems:
    print("Systempakete decken noch nicht alle Python-Anforderungen ab:")
    for problem in problems:
        print(f"  - {problem}")
    raise SystemExit(1)

print("Native/System-Python-Pakete erfüllen alle SimpleOffice-Web-Anforderungen.")
PY
}

termux_native_dependencies_ok() {
  "$VENV/bin/python" - <<'PY'
import importlib
from importlib.metadata import PackageNotFoundError, version
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.version import Version

requirements = {
    "cryptography": (">=48.0.1,<51", "cryptography"),
    "Pillow": (">=12.2,<13", "PIL"),
}
problems = []
for distribution, (specifier, module) in requirements.items():
    try:
        installed = version(distribution)
        if Version(installed) not in SpecifierSet(specifier):
            problems.append(f"{distribution}: {installed} passt nicht zu {specifier}")
            continue
        importlib.import_module(module)
    except PackageNotFoundError:
        problems.append(f"{distribution}: fehlt ({specifier})")
    except Exception as exc:
        problems.append(f"{distribution}/{module}: nicht importierbar: {exc}")

try:
    ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:
    problems.append("tzdata: Zeitzone Europe/Berlin fehlt")

if problems:
    print("Termux-native Web-Abhängigkeiten sind nicht verwendbar:")
    for problem in problems:
        print(f"  - {problem}")
    raise SystemExit(1)

print("Termux-native Web-Pakete und Zeitzonendaten sind vorhanden und verwendbar.")
PY
}

detect_linux_distribution
detect_native_package_manager

if [ "$CHECK_SYSTEM" -eq 1 ]; then
  if ! python_is_compatible; then
    echo "--check-system verändert das System nicht. Für die Prüfung wird Python 3.10 oder neuer benötigt." >&2
    exit 1
  fi
  exec "$PYTHON" "$ROOT/tools/system_requirements.py"
fi

if ! ensure_python_runtime; then
  echo "Keine geeignete Python-Laufzeit gefunden. Benötigt wird Python 3.10 oder neuer." >&2
  if [ -n "$NATIVE_PM" ]; then
    echo "Erkannter Paketmanager: $NATIVE_PM ($DISTRO_NAME)." >&2
  fi
  exit 1
fi

"$PYTHON" "$ROOT/tools/system_requirements.py" --missing-only
prepare_native_python_packages

if [ "$USE_SYSTEM_SITE_PACKAGES" -eq 1 ] && [ -x "$VENV/bin/python" ] && ! venv_uses_system_site_packages; then
  echo "Vorhandene .venv isoliert native Systempakete; erstelle sie kompatibel neu."
  rm -rf "$VENV"
fi

if [ ! -x "$VENV/bin/python" ]; then
  if [ "$USE_SYSTEM_SITE_PACKAGES" -eq 1 ]; then
    "$PYTHON" -m venv --system-site-packages "$VENV"
  else
    "$PYTHON" -m venv "$VENV"
  fi
fi

if [ "$IS_TERMUX" -eq 1 ]; then
  if ! termux_native_dependencies_ok; then
    echo "Termux-Webpakete und Zeitzonendaten werden erneut installiert/aktualisiert, bevor pip verwendet wird."
    install_native_packages tzdata python-cryptography python-pillow
    termux_native_dependencies_ok || {
      echo "Die benötigten nativen Termux-Webpakete oder Zeitzonendaten sind weiterhin nicht verwendbar; cryptography wird absichtlich nicht aus Source gebaut." >&2
      exit 1
    }
  fi

  # Android/Termux kann manylinux/musllinux Wheels nicht als native Android-Wheels
  # verwenden. cryptography kommt deshalb ausschließlich aus pkg und bleibt über
  # --system-site-packages sichtbar. SFTP-Kryptografie ist nicht Teil des Webstarts.
  export PIP_ONLY_BINARY="cryptography"
  export PIP_PREFER_BINARY=1

  termux_runtime_requirements=(
    'Flask>=3.0,<4'
    'beautifulsoup4>=4.12,<5'
    'reportlab>=4.0,<6'
    'pypdf>=5.0,<7'
    'waitress>=3.0,<4'
    'watchdog>=6,<7'
  )

  echo "Termux: versuche zuerst ausschließlich fertige Wheels für Web-Laufzeitpakete ..."
  if ! "$VENV/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: "${termux_runtime_requirements[@]}"; then
    echo "Nicht alle Web-Laufzeitpakete besitzen ein kompatibles Android-Wheel; installiere Build-Werkzeuge für den kontrollierten Fallback."
    build_packages="$(termux_build_packages)"
    # shellcheck disable=SC2086
    install_native_packages $build_packages
    "$VENV/bin/python" -m pip install --disable-pip-version-check --prefer-binary "${termux_runtime_requirements[@]}"
  fi

  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
  "$VENV/bin/python" -m pip check
elif [ "$USE_SYSTEM_SITE_PACKAGES" -eq 1 ] && native_dependency_versions_ok; then
  echo "Alle benötigten Python-Abhängigkeiten kommen passend aus der Linux-Umgebung; pip installiert nur SimpleOffice selbst."
  "$VENV/bin/python" -m pip install --disable-pip-version-check --no-deps --editable "$ROOT"
else
  echo "pip ergänzt nur fehlende oder nicht passende Web-Abhängigkeiten in der lokalen .venv."
  "$VENV/bin/python" -m pip install --disable-pip-version-check --editable "$ROOT"
fi

"$VENV/bin/python" "$ROOT/tools/install_invoice_validator.py" || true
cd "$ROOT"
exec "$VENV/bin/python" -m tools.launcher start "$@"