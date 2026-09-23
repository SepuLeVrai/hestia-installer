import unittest

from installer.security import BootstrapToken, SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class SecurityTests(unittest.TestCase):
    def test_bootstrap_token_is_one_time(self) -> None:
        code, token = BootstrapToken.generate()
        self.assertTrue(token.verify_and_consume(code))
        self.assertFalse(token.verify_and_consume(code))
        self.assertTrue(token.consumed)

    def test_bootstrap_token_expires(self) -> None:
        clock = FakeClock()
        token = BootstrapToken("HST-ABCD-EFGH", ttl_seconds=10, clock=clock)
        clock.value += 11
        self.assertFalse(token.verify_and_consume("HST-ABCD-EFGH"))
        self.assertTrue(token.expired)

    def test_bootstrap_token_locks_after_failed_attempts(self) -> None:
        token = BootstrapToken("HST-ABCD-EFGH", max_attempts=2)
        self.assertFalse(token.verify_and_consume("HST-XXXX-XXXX"))
        self.assertFalse(token.verify_and_consume("HST-YYYY-YYYY"))
        self.assertTrue(token.locked)
        self.assertFalse(token.verify_and_consume("HST-ABCD-EFGH"))

    def test_sessions_expire_and_can_be_deleted(self) -> None:
        clock = FakeClock()
        store = SessionStore(ttl_seconds=5, clock=clock)
        session = store.create()
        self.assertIsNotNone(store.get(session.session_id))
        clock.value += 6
        self.assertIsNone(store.get(session.session_id))
        second = store.create()
        store.delete(second.session_id)
        self.assertIsNone(store.get(second.session_id))


if __name__ == "__main__":
    unittest.main()
