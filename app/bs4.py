from bs4 import BeautifulSoup
#pip3 install beautifulsoup4 flask

from flask import Flask, send_from_directory, \
    render_template_string, render_template, \
    request, session, redirect, abort, send_file, \
    g

 
import os

def download_file(static_dir="",dirname="", filename=""):
    if request.path == "/favicon.ico":
        return ""

    dirpath = os.path.join(static_dir, dirname)
    return send_from_directory(dirpath, filename, as_attachment=True)


def renderwithbs4(myFile="index.html"):
    if not myFile.endswith('html'):
        myFile += '.html'
    resultString = render_template(myFile)

    soup = BeautifulSoup(resultString)               #make BeautifulSoup
    prettyHTML = soup.prettify()    
    return prettyHTML

