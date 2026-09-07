"""Build-time licensing master identity.

Packaged builds overwrite this file inside the staging tree. Runtime settings
must never replace these values: changing the accounting master requires a new
build. Source checkouts can intentionally edit this file or use the packaging
build variables to create their own master.
"""

LICENSE_MASTER_URL = ""
LICENSE_MASTER_PEER_ID = "license-master"
LICENSE_MASTER_MODE = False
