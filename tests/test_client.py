"""Тести без ДПС: локальний HTTP-сервер імітує відповіді.

Вони перевіряють логіку програми (запасна адреса, помилки, білий список,
ліміт частоти), а НЕ те, що формат запиту збігається з вимогами ДПС:
це можна дізнатися лише з опису API й реальної відповіді сервера.

Запуск: python -m unittest discover -s tests -t .
"""

import contextlib
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fop_info
from dps.client import COMMAND_KEY, TIMESTAMP_KEY, DpsClient
from dps.errors import DpsError, DpsHttpError, NetworkError, NoDataError
from dps.throttle import RateLimiter
from dps.whitelist import ALLOWED_COMMANDS, ForbiddenCommandError, assert_allowed


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        srv = self.server
        srv.requests.append((self.command, self.path, self.headers.get("Content-Type"), body))
        if srv.delay:
            time.sleep(srv.delay)
        status, payload = srv.response
        self.send_response(status)
        if status == 204 or payload is None:
            self.end_headers()
            return
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FakeServer:
    def __init__(self, status=200, payload=None, delay=0.0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.requests = []
        self.httpd.delay = delay
        self.httpd.response = (status, payload if payload is not None else {TIMESTAMP_KEY: "2026-11-17T12:00:00"})
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/fs"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def requests(self):
        return self.httpd.requests

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/fs"


def fast_limiter() -> RateLimiter:
    return RateLimiter(2.0, sleep=lambda s: None)


class WhitelistTests(unittest.TestCase):
    def test_exactly_six_commands(self):
        self.assertEqual(
            ALLOWED_COMMANDS,
            {"ServerState", "Objects", "Operators", "TransactionsRegistrarState", "Shifts", "ZRep"},
        )
        for cmd in ALLOWED_COMMANDS:
            assert_allowed(cmd)

    def test_forbidden_rejected(self):
        for cmd in ("DeviceRegister", "Documents", "Check", "Shift", "objects", "", None, 7):
            with self.subTest(cmd=cmd), self.assertRaises(ForbiddenCommandError):
                assert_allowed(cmd)

    def test_only_cmd_path_allowed(self):
        for path in ("/doc", "/DOC", "/fs/doc", "/cmd/", "/other"):
            with self.subTest(path=path), self.assertRaises(ForbiddenCommandError):
                assert_allowed("Objects", path)


class ClientTests(unittest.TestCase):
    def test_server_state_request_shape(self):
        with FakeServer() as srv:
            client = DpsClient([srv.url], limiter=fast_limiter())
            response = client.server_state()
        self.assertEqual(response[TIMESTAMP_KEY], "2026-11-17T12:00:00")
        method, path, ctype, body = srv.requests[0]
        self.assertEqual((method, path, ctype), ("POST", "/fs/cmd", "application/json"))
        self.assertEqual(json.loads(body), {COMMAND_KEY: "ServerState"})

    def test_falls_back_to_second_address_when_first_unreachable(self):
        with FakeServer() as second:
            client = DpsClient([closed_port_url(), second.url], limiter=fast_limiter())
            client.server_state()
            self.assertEqual(client.last_server, second.url)
            self.assertEqual(len(second.requests), 1)

    def test_first_address_used_when_it_works(self):
        with FakeServer() as first, FakeServer() as second:
            client = DpsClient([first.url, second.url], limiter=fast_limiter())
            client.server_state()
            self.assertEqual(client.last_server, first.url)
            self.assertEqual(second.requests, [])

    def test_all_addresses_down_gives_readable_error(self):
        a, b = closed_port_url(), closed_port_url()
        client = DpsClient([a, b], limiter=fast_limiter())
        with self.assertRaises(NetworkError) as ctx:
            client.server_state()
        text = str(ctx.exception)
        self.assertIn(a, text)
        self.assertIn(b, text)
        self.assertIn("відхилено", text)
        self.assertNotIn("Traceback", text)

    def test_timeout_gives_readable_error(self):
        with FakeServer(delay=1.5) as slow:
            client = DpsClient([slow.url], timeout=0.3, limiter=fast_limiter())
            with self.assertRaises(NetworkError) as ctx:
                client.server_state()
        self.assertIn("немає відповіді за 0.3 с", str(ctx.exception))

    def test_204_means_no_data(self):
        with FakeServer(status=204) as srv:
            with self.assertRaises(NoDataError) as ctx:
                DpsClient([srv.url], limiter=fast_limiter()).server_state()
        self.assertIn("204", str(ctx.exception))

    def test_http_errors_have_ukrainian_text_and_no_fallback(self):
        expected = {401: "Не авторизовано", 403: "Доступ заборонено", 404: "Не знайдено"}
        for status, phrase in expected.items():
            with self.subTest(status=status), FakeServer(status=status, payload="деталі".encode()) as first, FakeServer() as second:
                client = DpsClient([first.url, second.url], limiter=fast_limiter())
                with self.assertRaises(DpsHttpError) as ctx:
                    client.server_state()
                self.assertEqual(ctx.exception.status, status)
                self.assertIn(phrase, str(ctx.exception))
                self.assertIn("деталі", str(ctx.exception))
                self.assertEqual(second.requests, [], "інша адреса не мала викликатися")

    def test_unknown_http_error_shows_code(self):
        with FakeServer(status=500, payload=b"boom") as srv:
            with self.assertRaises(DpsHttpError) as ctx:
                DpsClient([srv.url], limiter=fast_limiter()).server_state()
        self.assertIn("500", str(ctx.exception))

    def test_non_json_response(self):
        with FakeServer(payload=b"<html>") as srv:
            with self.assertRaises(DpsError):
                DpsClient([srv.url], limiter=fast_limiter()).server_state()

    def test_forbidden_commands_never_reach_the_network(self):
        with FakeServer() as srv:
            client = DpsClient([srv.url], limiter=fast_limiter())
            for cmd in ("DeviceRegister", "Documents", "Check"):
                with self.subTest(cmd=cmd), self.assertRaises(ForbiddenCommandError):
                    client.send(cmd)
                with self.subTest(cmd=cmd, signed=True), self.assertRaises(ForbiddenCommandError):
                    client.send(cmd, body=b"signed")
        self.assertEqual(srv.requests, [])

    def test_signed_body_is_sent_as_is(self):
        with FakeServer(payload={"ok": True}) as srv:
            DpsClient([srv.url], limiter=fast_limiter()).send(
                "Objects", body=b"\x01\x02signed", content_type="application/octet-stream"
            )
        self.assertEqual(srv.requests[0][2:], ("application/octet-stream", b"\x01\x02signed"))

    def test_requests_are_spaced_by_rate_limiter(self):
        sleeps = []
        now = [100.0]

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
        with FakeServer() as srv:
            client = DpsClient([srv.url], limiter=limiter)
            for _ in range(3):
                client.server_state()
        self.assertEqual(sleeps, [0.5, 0.5])

    def test_rate_limiter_refuses_more_than_two_per_second(self):
        with self.assertRaises(ValueError):
            RateLimiter(3)
        with self.assertRaises(ValueError):
            RateLimiter(0)


class TimeTests(unittest.TestCase):
    def test_parse(self):
        self.assertIsNotNone(fop_info.parse_server_time("2026-11-17T12:00:00"))
        self.assertIsNotNone(fop_info.parse_server_time("2026-11-17T12:00:00+02:00"))
        for bad in ("вчора", "", None, 5):
            self.assertIsNone(fop_info.parse_server_time(bad))

    def test_difference_aware(self):
        now = datetime(2026, 11, 17, 10, 0, 0, tzinfo=timezone.utc)
        server = datetime(2026, 11, 17, 12, 0, 5, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(fop_info.clock_difference(server, now), timedelta(seconds=5))

    def test_difference_naive(self):
        now = datetime(2026, 11, 17, 12, 0, 0)
        server = datetime(2026, 11, 17, 11, 59, 58)
        self.assertEqual(fop_info.clock_difference(server, now), timedelta(seconds=-2))


class MainTests(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fop_info.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_config(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.toml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_server_state_prints_time_and_difference(self):
        with FakeServer() as srv:
            cfg = self.write_config(f'servers = ["{srv.url}"]\n')
            code, out, err = self.run_main(["--config", cfg, "--server-state"])
        self.assertEqual(code, 0, err)
        self.assertIn("Час сервера: 2026-11-17T12:00:00", out)
        self.assertIn("Різниця з годинником ПК", out)
        self.assertIn("без шифрування", out)  # локальний тест іде по http

    def test_falls_back_and_reports_address(self):
        with FakeServer() as second:
            cfg = self.write_config(f'servers = ["{closed_port_url()}", "{second.url}"]\n')
            code, out, _ = self.run_main(["--config", cfg, "--server-state"])
        self.assertEqual(code, 0)
        self.assertIn(second.url, out)

    def test_network_failure_is_a_message_not_a_traceback(self):
        cfg = self.write_config(f'servers = ["{closed_port_url()}"]\n')
        code, out, err = self.run_main(["--config", cfg, "--server-state"])
        self.assertEqual(code, 1)
        self.assertIn("Помилка:", err)
        self.assertNotIn("Traceback", err)

    def test_config_rate_above_two_is_rejected(self):
        cfg = self.write_config("max_requests_per_second = 5\n")
        code, _, err = self.run_main(["--config", cfg, "--server-state"])
        self.assertEqual(code, 2)
        self.assertIn("перевищує дозволену", err)

    def test_missing_config_exits_with_message(self):
        with self.assertRaises(SystemExit) as ctx:
            fop_info.load_config("/nonexistent/config.toml")
        self.assertIn("Конфіг не знайдено", str(ctx.exception))

    def test_card_not_implemented_yet(self):
        code, _, err = self.run_main([])
        self.assertEqual(code, 2)
        self.assertIn("--server-state", err)


if __name__ == "__main__":
    unittest.main()
