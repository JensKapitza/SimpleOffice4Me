import os
import click
from flask import current_app, g
from flask.cli import with_appcontext

from pathlib import Path
import subprocess
import time

def getFile(fileName):
    return os.path.join(current_app.config['DATABASE_FILEDIR'],fileName)

def getFileFromDir(dirName,fileName):
    baseDir = os.path.join(current_app.config['DATABASE_FILEDIR'],dirName)
    return os.path.join(baseDir,fileName)

def listFilesFromDir(dirName):
    pathName = getFile(dirName)
    dirPath = Path(pathName)
    if not dirPath.is_dir():
        return []
    else:
        return list(dirPath.glob('*'))

@click.command('list-files')
@click.argument('dir', default='.')
@with_appcontext
def list_files(dir):
    for x in listFilesFromDir(dir):
        click.echo(x)


 
@click.command('import-file')
@click.argument('file')
@with_appcontext
def import_file(file):
    click.echo(file)
    cmd = f"file {file}"
    res = subprocess.check_output(cmd, shell=True)
    # import and use unoconv
    originals = getFile("originals")
    outdir = getFile("pdf_dir")
    script = getFile("../textcleaner")
    nodict = getFile("../no-dict.cfg")
    xo = Path(originals)
    if not xo.exists():
        xo.mkdir()
    x = Path(outdir)
    if not x.exists():
        x.mkdir()


    
    outdirImg = xo.joinpath( str(int(time.time())) + "_" + Path(file).name)
    subprocess.check_output(f"cp {file} {outdirImg}", shell=True)
    outdir = x.joinpath(Path(file).name +"_"+ str(int(time.time()))+ ".pdf")

    try:
        result = res.decode('UTF-8')
        if "image" in result.lower():
            outdirImg = x.joinpath( str(int(time.time())) + "_" + Path(file).name)
            outdirImg2 = x.joinpath( str(int(time.time())) + "clean_" + Path(file).name)
            subprocess.check_output(f"unpaper -dv 1 -dr 10 -dn top --overwrite  {file} {outdirImg}", shell=True)

            subprocess.check_output(f"{script} {outdirImg} {outdirImg2}", shell=True)
            r=subprocess.check_output(f"unoconv -o {outdir} {outdirImg2}", shell=True)
            click.echo(f"{r}")
            outdirocrfin = x.joinpath(Path(file).name +"_"+ str(int(time.time()))+ "ocrfinal.pdf")
            r=subprocess.check_output(f"unoconv -o {outdirocrfin} {outdirImg}", shell=True)
            click.echo(f"{r}")
            
            outdirocr = x.joinpath(Path(file).name +"_"+ str(int(time.time()))+ "ocr.pdf")
            subprocess.check_output(f"ocrmypdf -l deu+eng  --tesseract-config {nodict} {outdir}  {outdirocr}", shell=True)
            subprocess.check_output(f"pdftk {outdirocr} multistamp {outdirocrfin} output {outdir}", shell=True)

            os.remove(outdirocr)
            os.remove(outdirocrfin)
            os.remove(outdirImg)
            os.remove(outdirImg2)
            click.echo(f"isImage")
        else:
            subprocess.check_output(f"unoconv -o {outdir} {file}", shell=True)
    except:
        click.echo("error on cmd line")
 



 
@click.command('import-pdffile')
@click.argument('pdffile')
@with_appcontext
def import_file(pdffile):
    click.echo(pdffile)
    # import and use unoconv
    originals = getFile("originals")
    outdir = getFile("pdf_dir")
    xo = Path(originals)
    if not xo.exists():
        xo.mkdir()
    x = Path(outdir)
    if not x.exists():
        x.mkdir()

    script = getFile("../pdfrepack")

    
    outdirImg = xo.joinpath( str(int(time.time())) + "_" + Path(pdffile).name)
    subprocess.check_output(f"cp {pdffile} {outdirImg}", shell=True)

    outdir = x.joinpath(Path(pdffile).name +"_"+ str(int(time.time()))+ ".pdf")
    # todo unpaper und 
    # pdf entpacken
    # wieder zusammenbauen

    try:
        subprocess.check_output(f"{script} {pdffile} {outdir}", shell=True)
    except:
        click.echo("error on cmd line")
 




def init_app(app):
    app.cli.add_command(list_files)
    app.cli.add_command(import_file)