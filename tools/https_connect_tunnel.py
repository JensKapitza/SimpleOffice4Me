#!/usr/bin/env python3
"""Compatibility wrapper for the packaged SimpleOffice HTTPS-CONNECT client."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simpleoffice_https_connect_tunnel import main


if __name__ == "__main__":
    main()
