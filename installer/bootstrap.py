from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from installer.constants import DEFAULT_RUNTIME_ROOT
from installer.httpd import BootstrapRequestHandler, BootstrapWebState, ThreadedHTTPSServer, build_ssl_context
from installer.network import BindDecision, choose_admin_candidate, discover_ipv4_candidates, reserve_random_port, verify_port_closed
from installer.runtime import cleanup_staging, create_private_staging
from installer.security import BootstrapToken, SessionStore
from installer.tls import TLSMaterial, generate_ephemeral_certificate


@dataclass(slots=True)
class PreparedBootstrap:
    bind_decision: BindDecision
    port: int
    bootstrap_code: str
    staging_dir: Path
    tls_material: TLSMaterial
    server: ThreadedHTTPSServer
    runtime_root: Path
    _closed: bool = False

    @property
    def url(self) -> str:
        return f"https://{self.bind_decision.advertised_address}:{self.port}"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.server.server_close()
            self.server.state.session_store.clear()  # type: ignore[attr-defined]
        finally:
            cleanup_staging(self.staging_dir, self.runtime_root)
        for _ in range(10):
            if verify_port_closed(self.bind_decision.bind_address, self.port):
                return
            time.sleep(0.05)
        raise RuntimeError("Le port HTTPS temporaire reste ouvert après l'arrêt")

    def __enter__(self) -> "PreparedBootstrap":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def prepare_bootstrap(
    *,
    web_root: Path,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    bind_address: str | None = None,
    allow_public: bool = False,
    interactive: bool | None = None,
) -> PreparedBootstrap:
    staging = create_private_staging(runtime_root)
    reservation = None
    server = None
    try:
        candidates = discover_ipv4_candidates()
        decision = choose_admin_candidate(
            candidates,
            requested=bind_address,
            allow_public=allow_public,
            interactive=interactive,
        )
        reservation = reserve_random_port(decision.bind_address)
        tls_material = generate_ephemeral_certificate(staging, decision.bind_address)
        code, token = BootstrapToken.generate()
        sessions = SessionStore()
        state = BootstrapWebState(
            web_root=web_root,
            bootstrap_token=token,
            session_store=sessions,
            host=decision.bind_address,
            port=reservation.port,
        )
        context = build_ssl_context(tls_material.certificate, tls_material.private_key)
        server = ThreadedHTTPSServer(
            reservation,
            BootstrapRequestHandler,
            ssl_context=context,
            state=state,
        )
        reservation = None
        return PreparedBootstrap(
            bind_decision=decision,
            port=state.port,
            bootstrap_code=code,
            staging_dir=staging,
            tls_material=tls_material,
            server=server,
            runtime_root=Path(runtime_root),
        )
    except Exception:
        if server is not None:
            server.server_close()
        if reservation is not None:
            reservation.close()
        try:
            cleanup_staging(staging, runtime_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def serve_in_thread(prepared: PreparedBootstrap) -> threading.Thread:
    thread = threading.Thread(target=prepared.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return thread
