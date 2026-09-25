"""Offline XRechnung validation through the pinned KoSIT runtime."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from tools.install_xrechnung_validator import (
    CONFIG_DIR,
    CONFIG_RELEASE,
    VALIDATOR_VERSION,
    XRECHNUNG_VERSION,
    verify_installation,
)


MAX_XML_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 90


def validator_status() -> dict[str, Any]:
    java = shutil.which("java")
    if not java:
        return {
            "available": False,
            "reason": "java_unavailable",
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }
    try:
        jar, scenario = verify_installation()
    except (OSError, RuntimeError, ValueError):
        return {
            "available": False,
            "reason": "validator_unavailable",
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }
    return {
        "available": True,
        "reason": "",
        "java": str(java),
        "jar": str(jar),
        "scenario": str(scenario),
        "xrechnung_version": XRECHNUNG_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "configuration_release": CONFIG_RELEASE,
    }


def validate_xrechnung(
    xml: bytes,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = bytes(xml)
    if not payload or len(payload) > MAX_XML_BYTES:
        return {
            "validated": False,
            "acceptable": False,
            "reason": "xml_size_invalid",
            "exit_code": None,
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }
    try:
        DefusedElementTree.fromstring(payload)
    except DefusedXmlException:
        return {
            "validated": False,
            "acceptable": False,
            "reason": "unsafe_xml",
            "exit_code": None,
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }
    except (ET.ParseError, UnicodeDecodeError, ValueError):
        return {
            "validated": False,
            "acceptable": False,
            "reason": "xml_not_well_formed",
            "exit_code": None,
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }

    status = validator_status()
    if not status["available"]:
        return {
            "validated": False,
            "acceptable": False,
            "reason": str(status["reason"]),
            "exit_code": None,
            "xrechnung_version": XRECHNUNG_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "configuration_release": CONFIG_RELEASE,
        }
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 300
    ):
        raise ValueError("XRechnung validation timeout must be between 1 and 300 seconds")

    with tempfile.TemporaryDirectory(prefix="simpleoffice-xrechnung-") as temp:
        source = Path(temp) / "invoice.xml"
        source.write_bytes(payload)
        command = [
            str(status["java"]),
            "-Xmx512m",
            "-jar",
            str(status["jar"]),
            "-s",
            str(status["scenario"]),
            "-r",
            str(CONFIG_DIR),
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=temp,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "validated": False,
                "acceptable": False,
                "reason": "validator_timeout",
                "exit_code": None,
                "xrechnung_version": XRECHNUNG_VERSION,
                "validator_version": VALIDATOR_VERSION,
                "configuration_release": CONFIG_RELEASE,
            }
        except OSError:
            return {
                "validated": False,
                "acceptable": False,
                "reason": "validator_start_failed",
                "exit_code": None,
                "xrechnung_version": XRECHNUNG_VERSION,
                "validator_version": VALIDATOR_VERSION,
                "configuration_release": CONFIG_RELEASE,
            }

    return {
        "validated": True,
        "acceptable": completed.returncode == 0,
        "reason": "acceptable" if completed.returncode == 0 else "rejected",
        "exit_code": int(completed.returncode),
        "xrechnung_version": XRECHNUNG_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "configuration_release": CONFIG_RELEASE,
    }
