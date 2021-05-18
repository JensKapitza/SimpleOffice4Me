#!/usr/bin/env python3

import os
import random
import sys

from flask import Flask, send_from_directory, \
    render_template_string, render_template, \
    request, session, redirect, abort, send_file

if sys.version_info < (3,):
    raise BaseException("Wrong Python Version")

script_dir = os.path.dirname(os.path.abspath(__file__))

template_dir = os.path.join(script_dir, "../templates")
static_dir = os.path.join(script_dir, "../static")


app = Flask(__name__,template_folder=template_dir,static_folder=static_dir)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SECRET_KEY'] = 'web-session' + str(random.random())[2:]


@app.route('/favicon.ico')
@app.route('/static/<path:dirname>/<path:filename>')
def download_file(dirname="", filename=""):
    if request.path == "/favicon.ico":
        return ""

    dirpath = os.path.join(static_dir, dirname)
    return send_from_directory(dirpath, filename, as_attachment=True)


@app.route('/')
@app.route('/<myFile>', methods=['GET', 'POST'])
@app.route('/<lang>/<myFile>', methods=['GET', 'POST'])
def myindex(lang=None, myFile="-"):
    resultString = render_template(myFile, mylanguage=lang)
    return resultString



if __name__ == '__main__':
    print("startup using flask internal or gunicorn3 -b :80 app ")
    #app.run(host="0.0.0.0", debug=True)