from __future__ import annotations

import json
import mimetypes
import ssl
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

from installer.constants import BOOTSTRAP_SESSION_TTL_SECONDS, MAX_REQUEST_BODY_BYTES, SESSION_COOKIE_NAME
from installer.network import PortReservation
from installer.security import BootstrapToken, Session, SessionStore


_SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=(), payment=()",
}


class BootstrapWebState:
    def __init__(
        self,
        *,
        web_root: Path,
        bootstrap_token: BootstrapToken,
        session_store: SessionStore,
        host: str,
        port: int,
    ) -> None:
        self.web_root = Path(web_root).resolve()
        self.bootstrap_token = bootstrap_token
        self.session_store = session_store
        self.host = host
        self.port = port
        self.expected_host_header = f"{host}:{port}"
        self.expected_origin = f"https://{host}:{port}"


class ThreadedHTTPSServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        reservation: PortReservation,
        handler_class: type[BaseHTTPRequestHandler],
        *,
        ssl_context: ssl.SSLContext,
        state: BootstrapWebState,
    ) -> None:
        super().__init__((reservation.host, reservation.port), handler_class, bind_and_activate=False)
        self.socket.close()
        raw_socket = reservation.detach()
        self.socket = ssl_context.wrap_socket(raw_socket, server_side=True)
        self.server_address = (state.host, state.port)
        self.server_name = state.host
        self.server_port = state.port
        self.state = state


def build_ssl_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(certfile=str(certificate), keyfile=str(private_key))
    return context


class BootstrapRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HESTIA-Installer"
    sys_version = ""

    @property
    def app(self) -> BootstrapWebState:
        return self.server.state  # type: ignore[attr-defined]

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path = urlsplit(self.path).path
        sys.stderr.write(f"HESTIA Installer HTTP {self.command} {path} {code}\n")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def log_error(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("HESTIA Installer HTTP error\n")

    def _security_headers(self) -> None:
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)

    def _finish_headers(self, *, content_length: int = 0, content_type: str | None = None) -> None:
        self._security_headers()
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.end_headers()

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self._finish_headers(content_length=len(payload), content_type=content_type)
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(self, status: int, payload: dict, *, close_connection: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if close_connection:
            self.close_connection = True
        self.send_response(status)
        self._security_headers()
        if close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self.close_connection = True
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            phrase = "Bad Request"
        body = f"{code} {phrase}\n".encode("utf-8")
        self.send_response_only(code)
        self._security_headers()
        self.send_header("Connection", "close")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self._security_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reject_if_bad_host_or_query(self) -> bool:
        host = self.headers.get("Host", "")
        if host != self.app.expected_host_header:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Hôte invalide"}, close_connection=True)
            return True
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Paramètres d'URL interdits"}, close_connection=True)
            return True
        return False

    def _reject_unexpected_request_body(self) -> bool:
        if self.headers.get("Transfer-Encoding"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Corps de requête interdit"}, close_connection=True)
            return True
        raw_length = self.headers.get("Content-Length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length invalide"}, close_connection=True)
                return True
            if length != 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Corps de requête interdit"}, close_connection=True)
                return True
        return False

    def _session_id(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _session(self) -> Session | None:
        return self.app.session_store.get(self._session_id())

    def _origin_matches(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == self.app.expected_origin

    def _require_session(self) -> Session | None:
        session = self._session()
        if session is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Session requise"})
            return None
        return session

    def _require_csrf(self, session: Session) -> bool:
        if not self._origin_matches():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Origine invalide"}, close_connection=True)
            return False
        token = self.headers.get("X-Hestia-CSRF", "")
        if not token or token != session.csrf_token:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Protection CSRF invalide"}, close_connection=True)
            return False
        return True

    def _read_json_body(self) -> dict | None:
        if self.headers.get("Transfer-Encoding"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Transfer-Encoding non supporté"}, close_connection=True)
            return None
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON requis"}, close_connection=True)
            return None
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length requis"}, close_connection=True)
            return None
        if length < 1 or length > MAX_REQUEST_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Requête trop volumineuse"}, close_connection=True)
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON invalide"})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Objet JSON requis"})
            return None
        return payload

    def _discard_request_body(self) -> bool:
        if self.headers.get("Transfer-Encoding"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Transfer-Encoding non supporté"}, close_connection=True)
            return False
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return True
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length invalide"}, close_connection=True)
            return False
        if length < 0 or length > MAX_REQUEST_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Requête trop volumineuse"}, close_connection=True)
            return False
        if length:
            self.rfile.read(length)
        return True

    def _safe_static_path(self, relative: str) -> Path | None:
        relative_path = Path(relative)
        if relative_path.is_absolute() or not relative_path.parts:
            return None
        if any(part in {"", ".", ".."} for part in relative_path.parts):
            return None

        unresolved = self.app.web_root / relative_path
        current = self.app.web_root
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                return None

        candidate = unresolved.resolve()
        try:
            candidate.relative_to(self.app.web_root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _serve_static(self, relative: str) -> None:
        path = self._safe_static_path(relative)
        if path is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ressource introuvable"})
            return
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, payload, content_type)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if self._reject_if_bad_host_or_query() or self._reject_unexpected_request_body():
            return
        path = urlsplit(self.path).path

        if path == "/api/bootstrap/status":
            self._send_json(
                HTTPStatus.OK,
                {
                    "usable": self.app.bootstrap_token.usable,
                    "consumed": self.app.bootstrap_token.consumed,
                },
            )
            return

        if path == "/api/session":
            session = self._require_session()
            if session is not None:
                self._send_json(HTTPStatus.OK, {"authenticated": True, "csrf_token": session.csrf_token})
            return

        if path == "/bootstrap" or path == "/bootstrap.html":
            if self._session() is not None:
                self._redirect("/")
            else:
                self._serve_static("bootstrap.html")
            return

        if path.startswith("/assets/"):
            self._serve_static(path.lstrip("/"))
            return

        if path in {"/", "/index.html"}:
            if self._session() is None:
                self._redirect("/bootstrap")
            else:
                self._serve_static("index.html")
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route inconnue"})

    def do_POST(self) -> None:
        if self._reject_if_bad_host_or_query():
            return
        path = urlsplit(self.path).path

        if path == "/api/bootstrap/unlock":
            if not self._origin_matches():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Origine invalide"}, close_connection=True)
                return
            payload = self._read_json_body()
            if payload is None:
                return
            if set(payload) != {"code"} or not isinstance(payload.get("code"), str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Requête invalide"})
                return
            code = payload["code"].strip()
            if len(code) > 32 or not self.app.bootstrap_token.verify_and_consume(code):
                status = HTTPStatus.TOO_MANY_REQUESTS if self.app.bootstrap_token.locked else HTTPStatus.UNAUTHORIZED
                self._send_json(status, {"error": "Code incorrect ou expiré"})
                return
            session = self.app.session_store.create()
            response = b'{"authenticated":true}'
            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE_NAME}={session.session_id}; Path=/; Max-Age={BOOTSTRAP_SESSION_TTL_SECONDS}; Secure; HttpOnly; SameSite=Strict")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return

        if path == "/api/logout":
            if not self._discard_request_body():
                return
            session = self._require_session()
            if session is None or not self._require_csrf(session):
                return
            self.app.session_store.delete(session.session_id)
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.send_header("Set-Cookie", f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route inconnue"}, close_connection=True)

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self._security_headers()
        self.send_header("Allow", "GET, HEAD, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()
