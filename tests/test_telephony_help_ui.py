import re
import unittest
from pathlib import Path


class TelephonyHelpUiTest(unittest.TestCase):
    def test_manual_help_disables_generic_field_help(self):
        template = Path("templates/admin/telephony.html").read_text(encoding="utf-8")
        self.assertIn('data-field-help="manual"', template)
        for name in (
            "registrar_host",
            "registrar_port",
            "transport",
            "realm",
            "stun_server",
            "extension",
            "display_name",
            "device_kind",
        ):
            pattern = rf'<(?:input|select)[^>]*\bname="{re.escape(name)}"[^>]*\bdata-no-help="true"'
            self.assertRegex(template, pattern, msg=f"{name} must not receive a second automatic help control")

    def test_global_helper_supports_manual_opt_out(self):
        script = Path("static/js/global_ui.js").read_text(encoding="utf-8")
        self.assertIn('control.dataset.noHelp === "true"', script)


if __name__ == "__main__":
    unittest.main()
