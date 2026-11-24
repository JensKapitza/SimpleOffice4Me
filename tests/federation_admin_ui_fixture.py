"""Shared local Flask fixture for federation administrator UI tests."""

import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.federation_identity import FederationIdentity
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


class FederationAdminUiFixture:
    """Supply isolated application state without collecting tests itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("federation-ui-admin", generate_password_hash("federation-ui-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("federation-ui-user", generate_password_hash("federation-user-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "federation-ui-admin", "password": "federation-ui-password"},
        )
        self.assertLess(response.status_code, 400)
        # Suite asserts German page content: pin the session to German
        # (app default for new sessions is English).
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _remember_pairing_peer(self):
        with app.app_context():
            identity = FederationIdentity(self.root / "remote-identity").public_identity()
        FederationStore(self.root).save_peer(
            "office-b", "Office B", "https://office-b.example.test", "", enabled=False,
        )
        FederationTrustStore(self.root).remember(
            "office-b", "DE", identity["fingerprint"], "qr", identity["public_key"],
        )
