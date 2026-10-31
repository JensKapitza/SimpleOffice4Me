"""Document tag suggestions never reach the Meta cloud.

Split from ``tests.test_contextflow_doc_tags`` (file-size gate);
this refusal class is self-contained.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.contextflow.doc_tags import (
    suggest_image_for_document,
    suggest_tags_for_document,
)
from app.contextflow.vision import META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL


class DocTagMetaCloudRefusal(unittest.TestCase):
    """Document suggestions never reach the Meta cloud client.

    Both suggestion entries take no consent argument and select
    their backend via ``_require_local_provider`` (per-request
    consent hardcoded ``False``), so even a fully configured Meta
    setup — provider, server consent, key, endpoint, model — must
    fail closed before any transport is touched.
    """

    def _full_consent_meta_settings(self) -> dict:
        return {
            "provider": "meta",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.2:1b",
            "ollama_timeout": 60,
            "meta_consent": True,
            "meta_api_key": "test-key-not-a-secret",
            "meta_endpoint": META_MODEL_API_ENDPOINT,
            "meta_model": META_MODEL_API_MODEL,
        }

    def test_text_tags_refuse_meta_cloud_even_with_full_consent(self) -> None:
        document = {"document_id": "doc-1", "last_path": "notizen.txt", "tags": []}
        with (
            patch("app.contextflow.vision.local_urlopen") as local_opened,
            patch(
                "app.contextflow.vision.MetaVisionProvider._post_response"
            ) as cloud,
        ):
            with self.assertRaises(PermissionError):
                suggest_tags_for_document(
                    document=document,
                    settings=self._full_consent_meta_settings(),
                    root="/tmp/none",
                    actor="anna",
                )
        local_opened.assert_not_called()
        cloud.assert_not_called()

    def test_image_description_refuses_meta_cloud_even_with_full_consent(
        self,
    ) -> None:
        document = {"document_id": "doc-1", "last_path": "foto.png"}
        with (
            patch("app.contextflow.vision.local_urlopen") as local_opened,
            patch(
                "app.contextflow.vision.MetaVisionProvider._post_response"
            ) as cloud,
        ):
            with self.assertRaises(PermissionError):
                suggest_image_for_document(
                    document=document,
                    settings=self._full_consent_meta_settings(),
                    root="/tmp/none",
                    actor="anna",
                )
        local_opened.assert_not_called()
        cloud.assert_not_called()


if __name__ == "__main__":
    unittest.main()
