import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "chat" / "index.html"


class ChatListQuickFilterTests(unittest.TestCase):
    def test_chat_filter_controls_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="chat-filter"', template)
        self.assertIn('data-chat-row', template)
        self.assertIn('id="chat-filter-count"', template)
        self.assertIn('id="chat-filter-empty"', template)
        self.assertIn("chatInput.addEventListener('input',apply)", template)


if __name__ == "__main__":
    unittest.main()
