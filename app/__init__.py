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
    g


if str(locale.getpreferredencoding()).lower in ["utf-8", "utf8"]:
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
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SECRET_KEY'] = 'web-session' + str(random.random())[2:]


# blueprints

from . import auth
app.register_blueprint(auth.bp)

from . import db
db.init_app(app)

from . import translationdb as tdb
tdb.init_app(app)

from . import filedb as fdb
fdb.init_app(app)


@app.template_filter('datetime')
def format_datetime(value, format='%Y-%m-%d'):
    return value.strftime(format)


@app.after_request
def add_header(response):
    res = False
    app.logger.debug(f"debugging ist {app.debug}")
    if not app.debug and "text/css" in str(response.content_type) or "application/javascript" in str(response.content_type):
        then = datetime.datetime.now() + datetime.timedelta(minutes=5)
        response.headers['Cache-Control'] = 'public,max-age=1000'
        response.headers['Expires'] = then.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return response



@app.route('/favicon.ico')
@app.route('/static/<path:dirname>/<path:filename>')
def staticfile(dirname="", filename=""):
    return download_file(static_dir,dirname,filename)


@app.route('/')
@app.route('/<myFile>', methods=['GET', 'POST'])
@app.route('/<lang>/<myFile>', methods=['GET', 'POST'])
def index(myFile="index.html",lang=None):
    if lang is not None:
        g.current_lang = lang

    return renderwithbs4(myFile)



if __name__ == '__main__':
    print("startup using flask internal or gunicorn3 -b :80 app ")
    #app.run(host="0.0.0.0", debug=True)
