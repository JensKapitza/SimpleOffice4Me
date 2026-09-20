import socket
import tempfile
import unittest
from pathlib import Path

from simpleoffice_media_renderer import (
    load_media_renderer_settings,
    resolve_media_uri,
    save_media_renderer_settings,
    validate_media_renderer_settings,
)


def _resolver(*addresses):
    def resolve(host, port, type=0):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6 if ":" in address else 0,
                "",
                (address, port, 0, 0) if ":" in address else (address, port),
            )
            for address in addresses
        ]
    return resolve


class MediaRendererSettingsTests(unittest.TestCase):
    def test_settings_are_private_persistent_and_keep_stable_udn(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mini-services.json"
            first = load_media_renderer_settings(config)
            second = save_media_renderer_settings(
                {"friendly_name": "Wohnzimmer", "bind": "192.168.20.5", "port": 8201},
                config,
            )
            third = load_media_renderer_settings(config)
            self.assertEqual(first["udn"], second["udn"])
            self.assertEqual(second, third)
            self.assertEqual("Wohnzimmer", third["friendly_name"])
            mode = (Path(temp) / "mini-services" / "media-renderer.json").stat().st_mode & 0o777
            self.assertEqual(0o600, mode)

    def test_settings_reject_unsafe_bind_and_unknown_fields(self):
        for value in ("0.0.0.0", "224.0.0.1", "::1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_media_renderer_settings({"bind": value})
        with self.assertRaises(ValueError):
            validate_media_renderer_settings({"surprise": True})


class MediaRendererUriPolicyTests(unittest.TestCase):
    def test_lan_mode_accepts_private_media_and_pins_resolution(self):
        result = resolve_media_uri(
            "http://media.example:8080/movie.mp4?token=opaque",
            resolver=_resolver("192.168.20.44"),
        )
        self.assertEqual(["192.168.20.44"], result["addresses"])
        self.assertEqual(8080, result["port"])
        self.assertEqual("/movie.mp4?token=opaque", result["path"])

    def test_default_policy_rejects_public_special_and_mixed_dns_results(self):
        rejected = (
            ("8.8.8.8",),
            ("127.0.0.1",),
            ("169.254.1.2",),
            ("192.168.20.4", "8.8.8.8"),
        )
        for addresses in rejected:
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                resolve_media_uri("http://media.example/file", resolver=_resolver(*addresses))

    def test_explicit_remote_policy_allows_public_but_not_local_special_ranges(self):
        result = resolve_media_uri(
            "https://media.example/file",
            allow_remote=True,
            resolver=_resolver("8.8.8.8"),
        )
        self.assertEqual(["8.8.8.8"], result["addresses"])
        for address in ("127.0.0.1", "169.254.1.2", "224.0.0.1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                resolve_media_uri(
                    "https://media.example/file",
                    allow_remote=True,
                    resolver=_resolver(address),
                )

    def test_credentials_fragments_and_non_http_schemes_are_rejected(self):
        for url in (
            "http://user:secret@192.168.1.2/file",
            "http://192.168.1.2/file#fragment",
            "file:///etc/passwd",
            "ftp://192.168.1.2/file",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                resolve_media_uri(url, resolver=_resolver("192.168.1.2"))


if __name__ == "__main__":
    unittest.main()
