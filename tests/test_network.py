import socket
import unittest

from installer.network import (
    IPv4Candidate,
    candidates_from_payload,
    choose_admin_candidate,
    reserve_random_port,
    verify_port_closed,
)


class NetworkTests(unittest.TestCase):
    def test_candidates_prioritize_default_private_address(self) -> None:
        routes = [{"dev": "eth0", "prefsrc": "10.10.20.30"}]
        addresses = [
            {"ifname": "eth1", "addr_info": [{"family": "inet", "scope": "global", "local": "192.168.5.2"}]},
            {"ifname": "eth0", "addr_info": [{"family": "inet", "scope": "global", "local": "10.10.20.30"}]},
        ]
        candidates = candidates_from_payload(routes, addresses)
        self.assertEqual(candidates[0].address, "10.10.20.30")
        self.assertTrue(candidates[0].is_default)

    def test_public_only_falls_back_to_loopback(self) -> None:
        decision = choose_admin_candidate([IPv4Candidate("8.8.8.8", "eth0", True)], interactive=False)
        self.assertEqual(decision.bind_address, "127.0.0.1")
        self.assertTrue(decision.tunnel_required)

    def test_public_requested_needs_explicit_override(self) -> None:
        candidates = [IPv4Candidate("8.8.8.8", "eth0", True)]
        blocked = choose_admin_candidate(candidates, requested="8.8.8.8", allow_public=False)
        allowed = choose_admin_candidate(candidates, requested="8.8.8.8", allow_public=True)
        self.assertEqual(blocked.bind_address, "127.0.0.1")
        self.assertEqual(allowed.bind_address, "8.8.8.8")

    def test_multiple_private_non_interactive_uses_default(self) -> None:
        candidates = [
            IPv4Candidate("192.168.1.8", "eth1", False),
            IPv4Candidate("10.0.0.8", "eth0", True),
        ]
        decision = choose_admin_candidate(candidates, interactive=False)
        self.assertEqual(decision.bind_address, "10.0.0.8")

    def test_port_collision_retries_without_releasing_selected_socket(self) -> None:
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        occupied_port = occupied.getsockname()[1]

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()

        picks = iter([occupied_port, free_port])
        reservation = reserve_random_port(
            "127.0.0.1",
            port_min=min(occupied_port, free_port),
            port_max=max(occupied_port, free_port),
            attempts=2,
            chooser=lambda _a, _b: next(picks),
        )
        try:
            self.assertEqual(reservation.port, free_port)
            second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self.assertRaises(OSError):
                second.bind(("127.0.0.1", free_port))
            second.close()
        finally:
            reservation.close()
            occupied.close()
        self.assertTrue(verify_port_closed("127.0.0.1", free_port))


if __name__ == "__main__":
    unittest.main()
