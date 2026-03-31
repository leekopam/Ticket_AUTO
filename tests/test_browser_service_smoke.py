from __future__ import annotations

import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from services.browser_service import BrowserService


@contextmanager
def _run_local_http_server(
    routes: dict[str, tuple[int, dict[str, str], str]],
):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status_code, headers, body = routes.get(
                self.path,
                (
                    404,
                    {"Content-Type": "text/plain; charset=utf-8"},
                    "not found",
                ),
            )
            self.send_response(status_code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@unittest.skipUnless(
    os.environ.get("TICKET_AUTO_RUN_PLAYWRIGHT_SMOKE") == "1",
    "Set TICKET_AUTO_RUN_PLAYWRIGHT_SMOKE=1 to run real Playwright smoke tests.",
)
class BrowserServicePlaywrightSmokeTest(unittest.TestCase):
    """Only interact with BrowserService through public APIs in smoke tests.

    Playwright sync objects are thread-affine, and BrowserService owns them on its
    worker thread. Reaching into `_context`/`_auth_page` from the test thread can
    fail even when the real product path is healthy.
    """

    @staticmethod
    def _build_data_url(html: str) -> str:
        return "data:text/html;charset=utf-8," + quote(html)

    @staticmethod
    def _build_service(
        user_data_dir: str,
        *,
        login_url: str = "data:text/html,<html><body>Ticket_AUTO Smoke</body></html>",
    ) -> BrowserService:
        return BrowserService(
            login_url=login_url,
            user_data_dir=user_data_dir,
            require_login_each_run=False,
            headless=True,
        )

    def test_start_and_stop_with_real_playwright_session(self) -> None:
        with tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                self.assertTrue(service._is_running)
                self.assertIsNotNone(service._context)
                self.assertIsNotNone(service._auth_page)
            finally:
                service.stop()

            self.assertFalse(service._is_running)

    def test_ensure_authenticated_returns_false_without_witchform_cookie_in_real_session(self) -> None:
        with tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.ensure_authenticated(timeout_sec=1)
            finally:
                service.stop()

        self.assertFalse(result)

    def test_discover_order_context_from_data_url_with_real_playwright_session(self) -> None:
        html = """
        <html>
          <body>
            <script>
              const order = "ABCD1234EF" + "_" + "ABCDEFGHIJKL";
              const phone = "010" + "-" + "1234" + "-" + "5678";
              document.body.innerHTML =
                "<div>\\uC8FC\\uBB38\\uBC88\\uD638: " + order + "</div>" +
                "<div>\\uC8FC\\uBB38\\uC790\\uBA85: Hong Gil Dong</div>" +
                "<div>\\uC5F0\\uB77D\\uCC98: " + phone + "</div>";
            </script>
          </body>
        </html>
        """

        with tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.discover_order_context_from_page(
                    self._build_data_url(html),
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertEqual(result.order_number, "ABCD1234EF_ABCDEFGHIJKL")
        self.assertEqual(result.buyer_name, "Hong Gil Dong")
        self.assertEqual(result.buyer_phone, "010-1234-5678")

    def test_discover_order_number_from_http_html_with_real_playwright_session(self) -> None:
        html = """
        <html>
          <body>
            <div>주문번호: ZXCV1234BN_QWER5678TYUI</div>
          </body>
        </html>
        """

        with _run_local_http_server(
            {
                "/order": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    html,
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.discover_order_number_from_page(
                    f"{base_url}/order",
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertEqual(result, "ZXCV1234BN_QWER5678TYUI")

    def test_discover_order_number_from_data_url_with_real_playwright_session(self) -> None:
        html = """
        <html>
          <body>
            <script>
              const order = "MNBV0987LK" + "_" + "POIU7654TREW";
              document.body.innerHTML = "<div>Order Number: " + order + "</div>";
            </script>
          </body>
        </html>
        """

        with tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.discover_order_number_from_page(
                    self._build_data_url(html),
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertEqual(result, "MNBV0987LK_POIU7654TREW")

    def test_discover_order_context_from_http_script_rendered_html_with_real_playwright_session(self) -> None:
        html = """
        <html>
          <body>
            <script>
              const order = "QAZX1234SW" + "_" + "EDCV5678RFVT";
              const phone = "010" + "-" + "2222" + "-" + "3333";
              document.body.innerHTML =
                "<div>\\uC8FC\\uBB38\\uBC88\\uD638: " + order + "</div>" +
                "<div>\\uC8FC\\uBB38\\uC790\\uBA85: Kim Na Ri</div>" +
                "<div>\\uC5F0\\uB77D\\uCC98: " + phone + "</div>";
            </script>
          </body>
        </html>
        """

        with _run_local_http_server(
            {
                "/order-script": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    html,
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.discover_order_context_from_page(
                    f"{base_url}/order-script",
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertEqual(result.order_number, "QAZX1234SW_EDCV5678RFVT")
        self.assertEqual(result.buyer_name, "Kim Na Ri")
        self.assertEqual(result.buyer_phone, "010-2222-3333")

    def test_resolve_qr_redirect_reports_auth_required_with_real_playwright_session(self) -> None:
        with _run_local_http_server(
            {
                "/qr-auth": (
                    302,
                    {
                        "Location": "/w/login?next=/orders/1",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    "",
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.resolve_qr_redirect(f"{base_url}/qr-auth", timeout_ms=5000)
            finally:
                service.stop()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.location, "/w/login?next=/orders/1")

    def test_resolve_qr_redirect_reports_auth_required_for_login_html_with_real_playwright_session(self) -> None:
        login_like_html = """
        <html>
          <body>
            <form>
              <input name="userid" />
              <input name="password" type="password" />
              <button type="submit">login</button>
            </form>
          </body>
        </html>
        """

        with _run_local_http_server(
            {
                "/qr-login-html": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    login_like_html,
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.resolve_qr_redirect(
                    f"{base_url}/qr-login-html",
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.location, "")

    def test_resolve_qr_redirect_keeps_non_login_html_200_as_ok_with_real_playwright_session(self) -> None:
        success_html = """
        <html>
          <body>
            <div>ticket receipt page</div>
          </body>
        </html>
        """

        with _run_local_http_server(
            {
                "/qr-html-ok": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    success_html,
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.resolve_qr_redirect(
                    f"{base_url}/qr-html-ok",
                    timeout_ms=5000,
                )
            finally:
                service.stop()

        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.location, "")
        self.assertEqual(result.error_code, "")

    def test_resolve_qr_redirect_keeps_success_redirect_with_real_playwright_session(self) -> None:
        with _run_local_http_server(
            {
                "/qr-ok": (
                    302,
                    {
                        "Location": "/orders/1",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    "",
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                result = service.resolve_qr_redirect(f"{base_url}/qr-ok", timeout_ms=5000)
            finally:
                service.stop()

        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.location, "/orders/1")

    def test_request_relogin_returns_true_and_keeps_service_responsive_with_real_playwright_session(self) -> None:
        with _run_local_http_server(
            {
                "/login": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    "<html><body>login page</body></html>",
                ),
                "/other": (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    "<html><body>other page</body></html>",
                ),
                "/qr-ok": (
                    302,
                    {
                        "Location": "/orders/1",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    "",
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            login_url = f"{base_url}/login"
            service = self._build_service(user_data_dir, login_url=login_url)

            try:
                service.start()
                result = service.request_relogin()
                follow_up_result = service.resolve_qr_redirect(f"{base_url}/qr-ok", timeout_ms=5000)
            finally:
                service.stop()

        self.assertTrue(result)
        self.assertTrue(follow_up_result.ok)
        self.assertEqual(follow_up_result.location, "/orders/1")

    def test_clear_auth_state_for_domain_returns_true_and_keeps_service_responsive_with_real_playwright_session(self) -> None:
        with _run_local_http_server(
            {
                "/qr-ok": (
                    302,
                    {
                        "Location": "/orders/2",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    "",
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                before_snapshot = service.get_auth_cookie_snapshot()
                cleared = service.clear_auth_state_for_domain()
                after_snapshot = service.get_auth_cookie_snapshot()
                follow_up_result = service.resolve_qr_redirect(f"{base_url}/qr-ok", timeout_ms=5000)
            finally:
                service.stop()

        self.assertEqual(before_snapshot, {})
        self.assertTrue(cleared)
        self.assertEqual(after_snapshot, {})
        self.assertTrue(follow_up_result.ok)
        self.assertEqual(follow_up_result.location, "/orders/2")

    def test_replace_auth_cookie_snapshot_supports_local_cookie_lifecycle_smoke(self) -> None:
        with _run_local_http_server(
            {
                "/qr-ok": (
                    302,
                    {
                        "Location": "/orders/3",
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    "",
                ),
            }
        ) as base_url, tempfile.TemporaryDirectory() as user_data_dir:
            service = self._build_service(user_data_dir)

            try:
                service.start()
                seeded = service.replace_auth_cookie_snapshot(
                    {
                        "PHPSESSID": "sess1234567890",
                        "AUTH_TOKEN": "token1234567890",
                        "theme": "dark",
                    }
                )
                before_snapshot = service.get_auth_cookie_snapshot()
                cleared = service.clear_auth_state_for_domain()
                after_snapshot = service.get_auth_cookie_snapshot()
                follow_up_result = service.resolve_qr_redirect(f"{base_url}/qr-ok", timeout_ms=5000)
            finally:
                service.stop()

        self.assertTrue(seeded)
        self.assertEqual(before_snapshot["PHPSESSID"], "sess1234567890")
        self.assertEqual(before_snapshot["AUTH_TOKEN"], "token1234567890")
        self.assertNotIn("theme", before_snapshot)
        self.assertTrue(cleared)
        self.assertEqual(after_snapshot, {})
        self.assertTrue(follow_up_result.ok)
        self.assertEqual(follow_up_result.location, "/orders/3")
