#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="${PYTHON_FALLBACK:-python3}"
RUN_DIR="$ROOT/instance/run"
mkdir -p "$RUN_DIR"

ACTIVE_ROLES="$($PYTHON -c 'from tools.service_control import running_roles; print(" ".join(running_roles()))' 2>/dev/null || true)"
case " $ACTIVE_ROLES " in *" web "*) WEB_ACTIVE=1 ;; *) WEB_ACTIVE=0 ;; esac
case " $ACTIVE_ROLES " in *" index "*) INDEX_ACTIVE=1 ;; *) INDEX_ACTIVE=0 ;; esac
case " $ACTIVE_ROLES " in *" sftp "*) SFTP_ACTIVE=1 ;; *) SFTP_ACTIVE=0 ;; esac

if [ "$WEB_ACTIVE" -eq 0 ] && [ "$INDEX_ACTIVE" -eq 0 ] && [ "$SFTP_ACTIVE" -eq 0 ]; then
  echo "Keine aktiven SimpleOffice4Me-Dienste gefunden; nichts neu gestartet."
  exit 0
fi

"$PYTHON" "$ROOT/tools/service_control.py" stop

if [ "$WEB_ACTIVE" -eq 1 ]; then
  nohup "$ROOT/start.sh" >"$RUN_DIR/web.log" 2>&1 &
  echo "Webanwendung wird neu gestartet (PID $!)."
elif [ "$INDEX_ACTIVE" -eq 1 ]; then
  DOCUMENT_ROOT="$($PYTHON -c 'from tools.launcher import load_config; config = load_config() or {}; print(config.get("document_root", ""))')"
  if [ -z "$DOCUMENT_ROOT" ]; then
    echo "Indexer war aktiv, aber der Dokumentordner fehlt in der Konfiguration." >&2
    exit 1
  fi
  nohup "$PYTHON" -m tools.index_worker --root "$DOCUMENT_ROOT" >"$RUN_DIR/index.log" 2>&1 &
  echo "Indexer wird neu gestartet (PID $!)."
fi

if [ "$SFTP_ACTIVE" -eq 1 ]; then
  nohup "$ROOT/start-sftp.sh" run >"$RUN_DIR/sftp.log" 2>&1 &
  echo "SFTP/SSHFS-Dienst wird neu gestartet (PID $!)."
fi
