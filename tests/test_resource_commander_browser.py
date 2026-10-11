"""Exercise the shipped Commander template, CSS and JS with isolated provider APIs.

Run with: python -m unittest discover -s tests -p test_resource_commander_browser.py -v
Requires the existing web-export extra and `python -m playwright install chromium`.
"""
import importlib.util
import json
import unittest
from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Requires Playwright")
class CommanderBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        if not Path(cls.pw.chromium.executable_path).is_file():
            cls.pw.stop()
            raise unittest.SkipTest("Requires Chromium")
        cls.browser = cls.pw.chromium.launch(args=["--no-sandbox"])
        env = Environment(loader=ChoiceLoader([
            DictLoader({"layout.html": '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="csrf-token" content="test-token">{% block script %}{% endblock %}{% block body %}{% endblock %}<script src="/static/js/security.js"></script>'}),
            FileSystemLoader(ROOT / "templates"),
        ]), autoescape=True)
        cls.html = env.get_template("resource_commander.html").render(
            g={"user": {"username": "tester"}},
            url_for=lambda endpoint, **kw: "/static/" + kw["filename"] if endpoint == "static" else "/example",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        self.page = self.context.new_page()
        self.posts = []
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        caps = dict.fromkeys(["read", "write", "folders", "delete", "copy", "move", "search"], True)

        def route(request_route):
            request = request_route.request
            path = request.url.split("http://commander.test", 1)[-1]
            if path.startswith("/static/"):
                file = ROOT / path.lstrip("/")
                request_route.fulfill(path=str(file), content_type=(
                    "text/javascript; charset=utf-8" if file.suffix == ".js" else
                    "text/css; charset=utf-8" if file.suffix == ".css" else "image/png"
                ))
                return
            if "/api/" not in path:
                request_route.fulfill(body=self.html, content_type="text/html")
                return
            if request.method == "POST":
                self.posts.append((path, request.post_data_json, request.headers))
                data = {}
            elif "/providers" in path:
                data = {"providers": [{"provider_id": "self", "label": "Lokal", "capabilities": caps}]}
            else:
                data = {"capabilities": caps, "entries": [
                    {"resource_id": "a", "name": '<img src=x onerror=alert(1)>.txt', "kind": "file", "size": 10},
                    {"resource_id": "b", "name": "Ordner", "kind": "folder"},
                ]}
            request_route.fulfill(body=json.dumps(data), content_type="application/json")

        self.page.route("**/*", route)
        self.page.goto("http://commander.test/")
        for css in ["vendor/bootstrap-5.3.8/css/bootstrap.min.css", "css/app.css", "css/quality-wins.css", "css/mobile-viewport.css"]:
            self.page.add_style_tag(path=str(ROOT / "static" / css))
        # Commander stylesheet is loaded after shared styles in the real layout.
        self.page.add_style_tag(path=str(ROOT / "static/css/resource_commander.css"))
        self.page.wait_for_selector("#right .resource-commander-row")

    def tearDown(self):
        self.context.close()
        self.assertEqual([], self.errors)

    def open_delete(self):
        self.page.locator("#left .resource-commander-row").first.click()
        self.page.locator("#delete").click()
        self.page.wait_for_selector("#commander-dialog[open]")

    def test_delete_hidden_fields_cancel_escape_and_explicit_confirmation(self):
        for action in ["Escape", "#commander-dialog-cancel", "#commander-dialog-close"]:
            self.open_delete()
            self.assertFalse(self.page.locator("#commander-dialog-fields").is_visible())
            self.assertTrue(self.page.locator("#commander-dialog-primary").is_disabled())
            self.assertEqual(0, self.page.locator("#commander-dialog-message img").count())
            self.assertTrue(self.page.locator("#commander-dialog-cancel").evaluate("e => e === document.activeElement"))
            if action == "Escape":
                self.page.keyboard.press(action)
            else:
                self.page.locator(action).click()
            self.assertFalse(self.page.locator("#commander-dialog").is_visible())
            self.assertTrue(self.page.locator("#delete").evaluate("e => e === document.activeElement"))
            self.assertEqual([], self.posts)
        self.open_delete()
        self.page.locator("#commander-dialog-confirm").click()
        self.page.wait_for_function("!document.querySelector('#commander-dialog').open")
        self.assertEqual(1, len(self.posts))
        self.assertEqual(("/resource-commander/api/delete", {"provider": "self", "id": "a"}), self.posts[0][:2])
        self.assertEqual("test-token", self.posts[0][2].get("x-csrf-token"))

    def test_focus_trap_fields_and_keyboard_pane_selection(self):
        self.page.locator("#right .resource-commander-row").first.focus()
        self.page.keyboard.press("F8")
        self.assertTrue(self.page.locator("#commander-dialog").is_visible())
        self.page.keyboard.press("Escape")
        self.page.locator("#right .path").focus()
        self.page.locator("#mkdir").click()
        self.assertTrue(self.page.locator("#commander-dialog-primary").evaluate("e => e === document.activeElement"))
        self.assertFalse(self.page.locator("#commander-dialog-secondary").is_visible())
        for _ in range(8):
            self.page.keyboard.press("Tab")
            self.assertTrue(self.page.evaluate("document.activeElement === document.body || document.querySelector('#commander-dialog').contains(document.activeElement)"))
        # Chromium may focus browser chrome at the tab boundary (activeElement=body),
        # but the inert background must never accept focus.
        self.page.locator("#mkdir").evaluate("e => e.focus()")
        self.assertFalse(self.page.locator("#mkdir").evaluate("e => e === document.activeElement"))
        self.page.locator("#commander-dialog-primary").fill("Berichte")
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("!document.querySelector('#commander-dialog').open")
        self.assertEqual("Berichte", self.posts[-1][1]["name"])
        self.page.locator("#commander-link").click()
        self.assertTrue(self.page.locator("#commander-dialog-secondary").is_visible())
        self.page.keyboard.press("Escape")

    def test_function_keys_do_not_reload_or_mutate_during_dialog(self):
        self.page.locator("#mkdir").click()
        for key in ["F5", "F6", "F7", "F8"]:
            prevented = self.page.evaluate("""key => {
                const event = new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true});
                document.activeElement.dispatchEvent(event);
                return event.defaultPrevented;
            }""", key)
            self.assertTrue(prevented, key)
        self.assertEqual([], self.posts)
        self.page.keyboard.press("Escape")

    def test_mobile_desktop_and_short_viewports(self):
        for width, height in [(320, 568), (390, 844), (760, 360), (1024, 768), (1440, 900)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.locator("#commander-link").click()
                box = self.page.locator("#commander-dialog").bounding_box()
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)
                self.assertGreaterEqual(box["y"], 0)
                self.assertLessEqual(box["y"] + box["height"], height)
                for selector in ["#commander-dialog-confirm", "#commander-dialog-close", "#commander-dialog-cancel"]:
                    self.assertGreaterEqual(self.page.locator(selector).bounding_box()["height"], 44)
                self.assertTrue(self.page.locator("#commander-dialog").evaluate("e => e.scrollWidth <= e.clientWidth"))
                self.page.locator("#commander-dialog-cancel").click()
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_touch_targets_and_simulated_keyboard(self):
        for selector in ["#left .up", "#left .reload"]:
            box = self.page.locator(selector).bounding_box()
            self.assertGreaterEqual(box["width"], 44)
            self.assertGreaterEqual(box["height"], 44)
        self.page.locator("#commander-link").tap()
        self.page.evaluate("""() => {
            document.documentElement.dataset.soKeyboardOpen = '1';
            document.documentElement.style.setProperty('--so-keyboard-inset', '300px');
            document.documentElement.style.setProperty('--so-visual-viewport-height', '544px');
        }""")
        box = self.page.locator("#commander-dialog").bounding_box()
        self.assertLessEqual(box["y"] + box["height"], 544)
        self.page.locator("#commander-dialog-cancel").tap()
        self.assertEqual([], self.posts)
