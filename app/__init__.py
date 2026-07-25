#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#export PYTHONIOENCODING=utf8
from pathlib import Path

import os
import random
import sys
import datetime
import locale

from .applogging import initlogging

from .bs4 import download_file, renderwithbs4

from flask import Flask, send_from_directory, \
    render_template_string, render_template, \
    request, session, redirect, abort, send_file, \
    g, url_for
from werkzeug.middleware.proxy_fix import ProxyFix


# ensure the environment uses UTF-8 encoding
if str(locale.getpreferredencoding()).lower() not in ["utf-8", "utf8"]:
    raise BaseException("Wrong encoding use utf8")

if sys.version_info < (3,):
    raise BaseException("Wrong Python Version")

script_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(script_dir, "..")

database_dir = os.path.join(app_dir, "database")
filebase_dir = os.path.join(database_dir, "files")
template_dir = os.path.join(app_dir, "templates")
static_dir = os.path.join(app_dir, "static")

for p in [database_dir, filebase_dir]:
    x = Path(p)
    if not x.exists():
        x.mkdir()

initlogging()

#see here 4mail logging
#https://flask.palletsprojects.com/en/1.1.x/logging/
app = Flask(__name__,template_folder=template_dir,static_folder=static_dir)
app.config['DATABASE_FILEDIR'] = filebase_dir
app.config['DATABASE'] = os.path.join(database_dir, "my.sqlite")
app.config['DATABASE_TRANSLATION'] = os.path.join(database_dir, "translation.sqlite")
app.config['DOCUMENT_ROOT'] = os.environ.get('SIMPLEOFFICE_DOCUMENT_ROOT', os.path.join(database_dir, "documents"))
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SECRET_KEY'] = 'web-session' + str(random.random())[2:]
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SIMPLEOFFICE_HTTPS', '').lower() in ('1', 'true', 'yes')

# Trust forwarded headers only when an administrator explicitly configures the
# number of reverse proxies. This keeps externally generated CardDAV/share URLs
# correct without accepting spoofed headers in the default local installation.
try:
    trusted_proxy_hops = int(os.environ.get('SIMPLEOFFICE_TRUSTED_PROXY_HOPS', '0'))
except ValueError:
    trusted_proxy_hops = 0
if trusted_proxy_hops > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_hops, x_proto=trusted_proxy_hops, x_host=trusted_proxy_hops, x_prefix=trusted_proxy_hops)


# blueprints

from . import auth
app.register_blueprint(auth.bp)

from . import db
db.init_app(app)
with app.app_context():
    db.ensure_auth_database()

from . import translationdb as tdb
tdb.init_app(app)

from . import filedb as fdb
fdb.init_app(app)

from . import document_store as document_store
document_store.init_app(app)

from . import documents
app.register_blueprint(documents.bp)

from . import carddav
app.register_blueprint(carddav.bp)

from .settings_store import SettingsStore, translate, ui_literal_translations


@app.before_request
def load_interface_preferences():
    settings = SettingsStore(app.config["DOCUMENT_ROOT"]).settings()
    language = session.get("simpleoffice_language", settings["interface"]["default_language"])
    g.language = language if language in ("de", "en") else settings["interface"]["default_language"]
    g.app_settings = settings


@app.context_processor
def template_preferences():
    language = getattr(g, "language", "de")
    return {"tr": lambda key: translate(language, key), "ui_literal_translations": ui_literal_translations(language)}


@app.template_filter('datetime')
def format_datetime(value, format='%Y-%m-%d'):
    return value.strftime(format)


@app.after_request
def add_header(response):
    """Add caching headers for static assets when not in debug mode."""
    app.logger.debug(f"debugging ist {app.debug}")
    if not app.debug and (
        "text/css" in str(response.content_type)
        or "application/javascript" in str(response.content_type)
    ):
        then = datetime.datetime.now() + datetime.timedelta(minutes=5)
        response.headers["Cache-Control"] = "public,max-age=1000"
        response.headers["Expires"] = then.strftime("%a, %d %b %Y %H:%M:%S GMT")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response



@app.route('/favicon.ico')
@app.route('/static/<path:dirname>/<path:filename>')
def staticfile(dirname="", filename=""):
    return download_file(static_dir,dirname,filename)


@app.route('/<myFile>', methods=['GET', 'POST'])
@app.route('/<lang>/<myFile>', methods=['GET', 'POST'])
def index(myFile="index.html",lang=None):
    if lang is not None:
        g.current_lang = lang

    return renderwithbs4(myFile)


@app.get('/')
def home():
    """Use the application dashboard as the entrypoint after authentication."""
    if getattr(g, "user", None) is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for("documents.dashboard"))



if __name__ == '__main__':
    print("startup using flask internal or gunicorn3 -b :80 app ")
    #app.run(host="0.0.0.0", debug=True)
