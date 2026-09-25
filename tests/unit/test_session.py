import unittest

from jittersim.api.session import (
    SESSION_SECONDS,
    SignInLimiter,
    cleared_session_cookie,
    issue_session,
    session_cookie,
    session_from_cookie_header,
    session_is_valid,
)


class SessionTests(unittest.TestCase):
    def test_an_issued_session_is_valid_until_it_expires(self) -> None:
        value = issue_session("token", now=1_000)

        self.assertTrue(session_is_valid("token", value, now=1_000))
        self.assertTrue(session_is_valid("token", value, now=1_000 + SESSION_SECONDS - 1))
        self.assertFalse(session_is_valid("token", value, now=1_000 + SESSION_SECONDS))

    def test_a_changed_token_or_tampered_cookie_is_refused(self) -> None:
        value = issue_session("token", now=1_000)
        expires, nonce, signature = value.split(".")

        self.assertFalse(session_is_valid("other", value, now=1_000))
        self.assertFalse(session_is_valid(None, value, now=1_000))
        for tampered in (
            f"{int(expires) + 60}.{nonce}.{signature}",
            f"{expires}.x{nonce}.{signature}",
            f"{expires}.{nonce}.{signature[:-1]}{'B' if signature[-1] == 'A' else 'A'}",
            f"{expires}.{nonce}.{signature}é",
            "",
            "garbage",
            "1.2",
            None,
        ):
            with self.subTest(tampered=tampered):
                self.assertFalse(session_is_valid("token", tampered, now=1_000))

    def test_each_sign_in_gets_its_own_cookie(self) -> None:
        self.assertNotEqual(issue_session("token", now=1), issue_session("token", now=1))

    def test_cookie_attributes(self) -> None:
        self.assertEqual(
            session_cookie("abc", secure=True),
            "session=abc; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict; Secure",
        )
        self.assertEqual(
            session_cookie("abc", secure=False),
            "session=abc; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict",
        )
        self.assertEqual(
            cleared_session_cookie(secure=True),
            "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict; Secure",
        )

    def test_reads_the_session_from_a_cookie_header(self) -> None:
        self.assertEqual(session_from_cookie_header("a=1; session=abc.def; b=2"), "abc.def")
        self.assertIsNone(session_from_cookie_header("a=1"))
        self.assertIsNone(session_from_cookie_header(None))
        self.assertIsNone(session_from_cookie_header('bad"cookie'))


class SignInLimiterTests(unittest.TestCase):
    def test_five_failures_lock_an_address_for_fifteen_minutes(self) -> None:
        limiter = SignInLimiter()
        for second in range(5):
            self.assertFalse(limiter.is_locked("10.0.0.2", now=second))
            limiter.record_failure("10.0.0.2", now=second)

        self.assertTrue(limiter.is_locked("10.0.0.2", now=10))
        self.assertFalse(limiter.is_locked("10.0.0.3", now=10))
        self.assertFalse(limiter.is_locked("10.0.0.2", now=4 + 15 * 60))

    def test_old_failures_expire_and_success_clears_the_count(self) -> None:
        limiter = SignInLimiter()
        for second in range(4):
            limiter.record_failure("10.0.0.2", now=second)
        # The first four are older than the window by now.
        limiter.record_failure("10.0.0.2", now=15 * 60 + 1)
        self.assertFalse(limiter.is_locked("10.0.0.2", now=15 * 60 + 1))

        for second in range(4):
            limiter.record_failure("10.0.0.2", now=15 * 60 + 2 + second)
        limiter.clear("10.0.0.2")
        self.assertFalse(limiter.is_locked("10.0.0.2", now=15 * 60 + 10))

    def test_the_limiter_keeps_a_bounded_number_of_addresses(self) -> None:
        limiter = SignInLimiter(max_addresses=3)
        for index in range(10):
            limiter.record_failure(f"10.0.0.{index}", now=index)

        self.assertLessEqual(len(limiter._failures), 3)
        # The most recent addresses are the ones kept.
        self.assertIn("10.0.0.9", limiter._failures)


if __name__ == "__main__":
    unittest.main()
