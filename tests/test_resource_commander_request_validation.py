"""Untrusted request body boundary tests for resource mutations."""
import unittest
from unittest.mock import patch

from flask import Flask

from app.resource_commander import bp


class ResourceMutationInputTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.access = patch("app.resource_commander._api_access")
        self.access.start()
        self.addCleanup(self.access.stop)

    def test_mutation_endpoints_reject_non_object_json_without_resource_access(self):
        for endpoint in ("mkdir", "delete", "move", "copy"):
            for body in ("[]", "null", '"string"', "42", "true", "{broken"):
                with self.subTest(endpoint=endpoint, body=body):
                    with patch("app.resource_commander._registry") as registry:
                        response = self.client.post(
                            "/resource-commander/api/" + endpoint,
                            data=body,
                            content_type="application/json",
                        )
                        self.assertEqual(response.status_code, 400)
                        registry.assert_not_called()

    def test_string_fields_reject_type_confusion_and_null_bytes(self):
        from app.resource_commander import _json_text
        with self.app.test_request_context("/"):
            from werkzeug.exceptions import BadRequest
            for bad in ([], {}, True, 17, None, "../\\x00secret", "x" * 4097):
                with self.subTest(value=bad), self.assertRaises(BadRequest):
                    _json_text({"path": bad}, "path")

    def test_copy_smart_flag_accepts_only_json_boolean(self):
        from app.resource_commander import _json_boolean
        from werkzeug.exceptions import BadRequest
        for bad in ("false", "true", 0, 1, [], {}):
            with self.subTest(value=bad):
                with self.app.test_request_context("/"):
                    with self.assertRaises(BadRequest):
                        _json_boolean({"source_smart": bad}, "source_smart")
        with self.app.test_request_context("/"):
            self.assertFalse(_json_boolean({}, "source_smart"))
            self.assertTrue(_json_boolean({"source_smart": True}, "source_smart"))


if __name__ == "__main__":
    unittest.main()
