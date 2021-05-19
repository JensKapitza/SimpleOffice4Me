import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext


def get_db():
    if 'dbtranslation' not in g:
        g.dbtranslation = sqlite3.connect(
            current_app.config['DATABASE_TRANSLATION'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.dbtranslation.row_factory = sqlite3.Row

    return g.dbtranslation


def close_db(e=None):
    db = g.pop('dbtranslation', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()

    with current_app.open_resource('schema_translation.sql') as f:
        db.executescript(f.read().decode('utf8'))


@click.command('init-translation')
@with_appcontext
def init_translationdb_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')




@click.command('translation-add')
@click.argument('key', default='')
@click.argument('value', default='')
@with_appcontext
def add_translation_command(key,value):
    db=get_db()
    db.execute(
                'INSERT INTO translation (key,value) VALUES (?,?) ',
                (key,value)
            )
    db.commit()


@click.command('translation-get')
@click.argument('key', default='')
@with_appcontext
def get_translation_command(key):
    db=get_db()
    entry=db.execute(
                'SELECT value FROM translation WHERE key=?',
                (key)
            ).fetchone()
    if entry is not None:
        click.echo(entry['value'])
        return entry['value']
    return None



@click.command('translation-del')
@click.argument('key', default='')
@with_appcontext
def del_translation_command(key):
    db=get_db()
    db.execute(
                'DELETE FROM translation WHERE key=?',
                (key)
            )
    db.commit()



def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_translationdb_command)
    app.cli.add_command(del_translation_command)
    app.cli.add_command(get_translation_command)
    app.cli.add_command(add_translation_command)