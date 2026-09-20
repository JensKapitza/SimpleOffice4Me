import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "admin"


class MiniServicesInlineHelpTests(unittest.TestCase):
    @staticmethod
    def _template(name: str) -> str:
        return (TEMPLATES / name).read_text(encoding="utf-8")

    def test_network_fields_have_accessible_format_and_binding_help(self):
        template = self._template("mini_services.html")
        for control_id, help_id in (
            ("dhcp-interface", "dhcp-interface-help"),
            ("mini-dhcp-bind", "mini-dhcp-bind-help"),
            ("mini-dhcp-port", "mini-dhcp-port-help"),
            ("mini-network", "mini-network-help"),
            ("mini-dns-bind", "mini-dns-bind-help"),
            ("mini-dns-port", "mini-dns-port-help"),
        ):
            self.assertIn(f'id="{control_id}"', template)
            self.assertIn(f'aria-describedby="{help_id}"', template)
            self.assertIn(f'id="{help_id}"', template)
        self.assertIn("IPv4-CIDR", template)
        self.assertIn("kein automatisch geratenes Netz", template)

    def test_audio_help_documents_rtp_pair_and_unconfirmed_playback(self):
        streamer = self._template("audio_streamer.html")
        output = self._template("audio_output.html")
        self.assertIn('aria-describedby="stream-targets-help"', streamer)
        self.assertIn('aria-describedby="receiver-port-help"', streamer)
        self.assertIn("RTCP-Port", streamer)
        self.assertIn("Recovery", streamer)
        self.assertIn('aria-describedby="announcement-remote-port-help"', output)
        self.assertIn("nicht die physische Wiedergabe", output)

    def test_boot_help_documents_autostart_permissions_and_bounded_retry(self):
        template = self._template("network_boot_federation.html")
        self.assertIn("Autostart", template)
        self.assertIn("UDP 69", template)
        self.assertIn("passende Dienstrechte", template)
        self.assertIn("begrenzt wiederholt", template)
        self.assertIn("noch automatisch mit erhöhten Rechten gestartet", template)


if __name__ == "__main__":
    unittest.main()
