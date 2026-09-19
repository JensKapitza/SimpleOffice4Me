import unittest
from unittest.mock import Mock, patch

from simpleoffice_media_playback import RendererPlayback, SafeMediaFetcher
from simpleoffice_media_upnp import RendererState


class _Response:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name, self.headers.get(name.lower(), default))

    def close(self):
        self.closed = True


class MediaFetchSecurityTests(unittest.TestCase):
    def test_redirect_is_revalidated_and_connections_use_pinned_addresses(self):
        initial = {
            "url": "http://first.local/start",
            "scheme": "http",
            "hostname": "first.local",
            "port": 80,
            "addresses": ["192.168.1.10"],
            "path": "/start",
        }
        redirected = {
            "url": "http://second.local/movie",
            "scheme": "http",
            "hostname": "second.local",
            "port": 80,
            "addresses": ["192.168.1.20"],
            "path": "/movie",
        }
        resolver = Mock(return_value=redirected)
        fetcher = SafeMediaFetcher(allow_remote=False, resolver=resolver)
        first_connection = Mock()
        first_connection.getresponse.return_value = _Response(
            302, {"Location": "http://second.local/movie"}
        )
        second_connection = Mock()
        second_response = _Response(200, {"Content-Type": "video/mp4"})
        second_connection.getresponse.return_value = second_response

        with patch.object(
            fetcher,
            "_connection",
            side_effect=[first_connection, second_connection],
        ) as connection_factory:
            connection, response, target = fetcher.open(initial)

        self.assertIs(second_connection, connection)
        self.assertIs(second_response, response)
        self.assertEqual(redirected, target)
        resolver.assert_called_once_with(
            "http://second.local/movie", allow_remote=False
        )
        self.assertEqual(initial, connection_factory.call_args_list[0].args[0])
        self.assertEqual(redirected, connection_factory.call_args_list[1].args[0])
        self.assertEqual(
            "first.local",
            first_connection.request.call_args.kwargs["headers"]["Host"],
        )
        self.assertEqual(
            "second.local",
            second_connection.request.call_args.kwargs["headers"]["Host"],
        )

    def test_invalid_range_never_reaches_upstream(self):
        target = {
            "url": "http://media.local/file",
            "scheme": "http",
            "hostname": "media.local",
            "port": 80,
            "addresses": ["192.168.1.30"],
            "path": "/file",
        }
        fetcher = SafeMediaFetcher(allow_remote=False)
        connection = Mock()
        with patch.object(fetcher, "_connection", return_value=connection):
            with self.assertRaises(ValueError):
                fetcher.open(target, range_header="bytes=0-1,4-8")
        connection.request.assert_not_called()
        connection.close.assert_called_once()


class RendererPlaybackTests(unittest.TestCase):
    def settings(self):
        return {
            "allow_remote_media": False,
            "audio_output": "default",
            "video_mode": "window",
        }

    def test_player_receives_only_loopback_proxy_not_remote_url(self):
        target = {
            "url": "http://media.local/movie.mp4",
            "scheme": "http",
            "hostname": "media.local",
            "port": 80,
            "addresses": ["192.168.1.40"],
            "path": "/movie.mp4",
        }
        state = RendererState(uri_resolver=Mock(return_value=target))
        state.set_uri(target["url"])
        state.play()
        fake_proxy = Mock()
        fake_proxy.register.return_value = (
            "token",
            "http://127.0.0.1:49123/media/token",
        )
        process = Mock()
        process.poll.return_value = None
        fake_thread = Mock()

        with patch("simpleoffice_media_playback.MediaProxy", return_value=fake_proxy), patch(
            "simpleoffice_media_playback.shutil.which", return_value="/usr/bin/ffplay"
        ), patch(
            "simpleoffice_media_playback.subprocess.Popen", return_value=process
        ) as popen, patch(
            "simpleoffice_media_playback.threading.Thread", return_value=fake_thread
        ):
            playback = RendererPlayback(state, self.settings())
            playback.sync("Play")
            command = popen.call_args.args[0]
            self.assertIn("http://127.0.0.1:49123/media/token", command)
            self.assertNotIn(target["url"], command)
            self.assertFalse(any("192.168.1.40" in part for part in command))
            state.pause()
            playback.sync("Pause")
            process.terminate.assert_called_once()
            playback.close()

    def test_player_start_failure_does_not_leave_transport_playing(self):
        target = {
            "url": "http://media.local/movie.mp4",
            "scheme": "http",
            "hostname": "media.local",
            "port": 80,
            "addresses": ["192.168.1.40"],
            "path": "/movie.mp4",
        }
        state = RendererState(uri_resolver=Mock(return_value=target))
        state.set_uri(target["url"])
        state.play()
        fake_proxy = Mock()
        fake_proxy.register.return_value = ("token", "http://127.0.0.1:1/media/token")

        with patch("simpleoffice_media_playback.MediaProxy", return_value=fake_proxy), patch(
            "simpleoffice_media_playback.shutil.which", return_value=None
        ):
            playback = RendererPlayback(state, self.settings())
            with self.assertRaises(RuntimeError):
                playback.sync("Play")
            self.assertEqual("STOPPED", state.snapshot().state)
            self.assertEqual("playback_failed", playback.status()["last_error"])
            playback.close()


if __name__ == "__main__":
    unittest.main()
