"""Runtime integration for automatic country discovery."""
import os
import threading
import time

import click
from flask import current_app

from .federation_discovery_service import discover_country


def configured_country():
    return os.environ.get("SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY", "").strip().upper()[:2]


def configured_interval():
    raw = os.environ.get("SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS", "21600")
    try:
        value = int(raw)
    except ValueError:
        value = 21600
    return max(3600, min(value, 7 * 86400))


def _worker(app):
    country = configured_country()
    if not country:
        return
    while True:
        with app.app_context():
            try:
                result = discover_country(current_app.config["DOCUMENT_ROOT"], country)
                app.logger.info("federation autoscan country=%s peers=%s errors=%s", country, len(result["peers"]), len(result["errors"]))
            except Exception as exc:
                app.logger.warning("federation autoscan failed: %s", exc)
        time.sleep(configured_interval())


@click.command("federation-discover")
@click.option("--country", required=True, help="ISO-3166 alpha-2 country code")
def discover_command(country):
    result = discover_country(current_app.config["DOCUMENT_ROOT"], country)
    click.echo(f"peers={len(result['peers'])} errors={len(result['errors'])}")


def init_app(app):
    app.cli.add_command(discover_command)
    if configured_country() and not app.testing:
        thread = threading.Thread(target=_worker, args=(app,), daemon=True, name="federation-country-discovery")
        thread.start()
