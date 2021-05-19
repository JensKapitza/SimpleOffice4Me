from logging.config import dictConfig
# logging to file
def initlogging():
    dictConfig({
        'version': 1,
        'formatters': {'default': {
            'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        }},
        'handlers': {'wsgi': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        },
            'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'default',
            'filename': 'logconfig.log',
            'maxBytes': 1024*1024,
            'backupCount': 3
        }

        },
        'root': {
            'level': 'ERROR', # INFO ERROR
            'handlers': ['wsgi','file']
        }
    })