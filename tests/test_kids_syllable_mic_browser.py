"""Chromium integration with a real Web Audio graph and purely generated PCM.

The fixture supplies its own generated MediaStream, never a physical microphone,
child voice, or saved recording. The production analyser consumes the stream.
"""
from __future__ import annotations

import threading
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from tests.http_api_fixture import PublicHttpTestCase
from tests.test_kids_syllable_mic import GAME

SYNTHETIC_INPUT = r"""(() => {
  if (!navigator.mediaDevices) return;
  const state = window.micFixture = {requests: [], played: [], streams: [], deny: false, deferred: false};
  const rawPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    if (state.current) state.played.push(state.current.track.readyState);
    return rawPlay.apply(this, args);
  };
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    state.requests.push(constraints);
    if (state.deny) throw new DOMException('test denial', 'NotAllowedError');
    const previous = state.current;
    if (previous) void previous.context.close().catch(() => {});
    const context = new AudioContext();
    const destination = context.createMediaStreamDestination();
    const track = destination.stream.getAudioTracks()[0];
    state.current = {context, destination, track};
    state.streams.push(track);
    await context.resume();
    if (state.deferred) await new Promise(r => {state.release = r;});
    return destination.stream;
  };
  state.feed = (sounds, ms) => {
    const {context, destination} = state.current;
    const rate = context.sampleRate;
    const buffer = context.createBuffer(1, Math.ceil(rate * ms / 1000), rate);
    const pcm = buffer.getChannelData(0);
    let seed = 700821;
    const noise = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 0x80000000 - 1;
    for (let i = 0; i < pcm.length; ++i) {
      const t = i * 1000 / rate;
      let v = noise() * .0008;
      for (const [start, duration, kind, hz = 200] of sounds) {
        const n = t - start;
        if (n < 0 || n >= duration) continue;
        if (kind === 'clap') v += noise() * .8 * Math.exp(-n / 10);
        if (kind === 'noise') v += noise() * .24;
        if (kind === 'voice') {
          const phase = n * 2 * Math.PI * hz / 1000;
          v += .25 * Math.min(1, n / 12, (duration - n) / 12) *
            (.78 * Math.sin(phase) + .18 * Math.sin(phase * 2) + .1 * Math.sin(phase * 3));
        }
      }
      pcm[i] = v;
    }
    const source = context.createBufferSource();
    state.signalFinished = false;
    source.addEventListener('ended', () => { state.signalFinished = true; }, {once: true});
    source.buffer = buffer;
    source.connect(destination);
    source.start(context.currentTime + .05);
  };
})();"""


@unittest.skipIf(sync_playwright is None, "Playwright extra and Chromium required")
class SyllableMicrophoneBrowserTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/documents/settings/language", data={"language": "de"},
                         headers=self._csrf_headers(self.client))
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        cookie = self.client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.cookie = {"name": cookie.key, "value": cookie.value, "url": self.origin}

    def _stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _game(self, browser):
        context = browser.new_context(service_workers="block")
        context.add_cookies([self.cookie])
        context.add_init_script(SYNTHETIC_INPUT)
        page = context.new_page()
        outside = []
        page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(self.origin + "/")
                   else (outside.append(r.request.url), r.abort()))
        page.goto(self.origin + "/kids/", wait_until="domcontentloaded")
        page.locator(f'main a[href="{GAME}"]').click()
        expect(page).to_have_url(self.origin + GAME)
        page.wait_for_function("window.KidsSyllables && window.KidsSyllableMic")
        # Reorder shipped dictionary rows only for a deterministic three-syllable
        # task. Use the public level click and original prompts/answer UI.
        page.evaluate("""() => {
          const words = window.KidsSyllables.WORDS.de;
          const favorites = ['Banane', 'Tomate', 'Giraffe'];
          words.splice(0, words.length, ...favorites.map(name => words.find(row => row.word === name)));
          Math.random = () => .99;
        }""")
        page.locator('[data-syll-level="clap"]').click()
        page.locator('[data-syll-mic] summary').click()
        self.assertEqual(page.evaluate("micFixture.requests.length"), 0)
        return context, page, outside

    def test_voiced_blocks_review_and_example_input_are_separate(self):
        with sync_playwright() as pw, pw.chromium.launch(args=["--use-fake-device-for-media-stream"]) as browser:
            context, page, outside = self._game(browser)
            try:
                voice = page.locator('[data-syll-mic-mode="voice"]')
                status = page.locator("[data-syll-mic-status]")
                count = page.locator("[data-syll-mic-count]")
                voice.click()
                expect(status).to_contain_text("Jetzt bist du dran")
                expect(page.locator("[data-syll-mic-target]")).to_have_text("3")
                self.assertTrue(page.evaluate("micFixture.requests.every(option => option.video === false && option.audio)"))
                page.evaluate("micFixture.feed([[220,220,'voice',210],[630,230,'voice',300],[1050,250,'voice',370]], 1900)")
                expect(count).to_have_text("3")
                self.assertTrue(page.locator("[data-syll-next]").is_hidden(),
                                "Measurement must not automatically commit an answer")
                page.locator(".kids-syll-example").click()
                expect(status).to_contain_text("Nach dem Ton")
                self.assertEqual(page.evaluate("micFixture.current.track.readyState"), "ended")
                page.wait_for_function("micFixture.played.length > 0")
                self.assertTrue(page.evaluate("micFixture.played.every(state => state === 'ended')"))
                self.assertEqual(page.evaluate("micFixture.requests.length"), 1)
                expect(count).to_have_text("3")
                page.locator("[data-syll-mic-subtract]").click()
                page.locator("[data-syll-mic-use]").click()
                expect(status).to_contain_text("passt noch nicht")
                expect(count).to_have_text("2")
                page.locator("[data-syll-mic-add]").click()
                page.locator("[data-syll-mic-use]").click()
                expect(page.locator("[data-syll-next]")).to_be_visible()
                self.assertEqual(outside, [])
            finally:
                context.close()

    def test_clap_impulses_noise_denial_and_late_grant_from_old_task(self):
        with sync_playwright() as pw, pw.chromium.launch(args=["--use-fake-device-for-media-stream"]) as browser:
            context, page, outside = self._game(browser)
            try:
                clap = page.locator('[data-syll-mic-mode="clap"]')
                voice = page.locator('[data-syll-mic-mode="voice"]')
                status = page.locator("[data-syll-mic-status]")
                count = page.locator("[data-syll-mic-count]")
                page.evaluate("micFixture.deny = true")
                clap.click()
                expect(status).to_contain_text("Kein Mikrofonzugriff")
                expect(page.locator(".kids-syll-clap")).to_be_enabled()
                page.locator(".kids-syll-clap").click()
                expect(page.locator('.kids-syll-dot').first).to_have_text("●")
                page.evaluate("micFixture.deny = false")
                clap.click()
                expect(status).to_contain_text("Jetzt bist du dran")
                page.evaluate("micFixture.feed([[220,50,'clap'],[305,45,'clap'],[710,510,'noise']], 1500)")
                page.wait_for_function("micFixture.signalFinished")
                expect(count).to_have_text("1")
                page.evaluate("micFixture.feed([[220,52,'clap'],[660,52,'clap']], 1100)")
                expect(count).to_have_text("3")
                page.locator("[data-syll-mic-pause]").click()
                self.assertEqual(page.evaluate("micFixture.current.track.readyState"), "ended")
                page.evaluate("micFixture.deferred = true")
                voice.click()
                page.wait_for_function("typeof micFixture.release === 'function'")
                page.locator("[data-syll-back]").click()
                page.evaluate("micFixture.release()")
                page.wait_for_function("micFixture.current.track.readyState === 'ended'")
                expect(page.locator("[data-syll-mic]")).to_be_hidden()
                expect(page.locator('[data-syll-level="rhyme"]')).to_be_visible()
                self.assertEqual(outside, [])
            finally:
                context.close()
