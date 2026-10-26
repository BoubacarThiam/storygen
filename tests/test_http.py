"""Tests de utils/http.py — logique de backoff (aucun réseau).

Constat réel qui motive ce test : un 429 (Too Many Requests) de
Pollinations ne se dissipe pas avec le backoff exponentiel générique
(1s/2s/4s) utilisé pour les erreurs réseau ordinaires — il lui faut un
délai bien plus long, voir steps/images.py."""

from __future__ import annotations

import unittest
import urllib.error

from storygen.utils.http import _backoff_seconds


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    import email.message

    msg = email.message.Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return urllib.error.HTTPError(url="http://x", code=code, msg="", hdrs=msg, fp=None)


class TestBackoffSeconds(unittest.TestCase):
    def test_ordinary_error_uses_short_exponential_backoff(self) -> None:
        error = TimeoutError("timed out")
        self.assertEqual(_backoff_seconds(error, 0), 1.0)
        self.assertEqual(_backoff_seconds(error, 1), 2.0)
        self.assertEqual(_backoff_seconds(error, 2), 4.0)

    def test_rate_limit_uses_much_longer_backoff(self) -> None:
        error = _http_error(429)
        self.assertGreaterEqual(_backoff_seconds(error, 0), 10.0)
        self.assertGreater(_backoff_seconds(error, 1), _backoff_seconds(error, 0))

    def test_payment_required_is_treated_as_rate_limit(self) -> None:
        # Pollinations renvoie 402 quand le quota anonyme (1 image / 45 s) est
        # dépassé : ce n'est pas une erreur définitive, il faut attendre.
        error = _http_error(402)
        self.assertGreaterEqual(_backoff_seconds(error, 0), 45.0)

    def test_service_unavailable_is_treated_as_rate_limit(self) -> None:
        error = _http_error(503)
        self.assertGreaterEqual(_backoff_seconds(error, 0), 45.0)

    def test_rate_limit_honors_retry_after_header(self) -> None:
        error = _http_error(429, headers={"Retry-After": "5"})
        self.assertEqual(_backoff_seconds(error, 0), 5.0)

    def test_rate_limit_caps_backoff(self) -> None:
        error = _http_error(429, headers={"Retry-After": "99999"})
        self.assertLessEqual(_backoff_seconds(error, 0), 60.0)

    def test_non_rate_limit_http_error_uses_short_backoff(self) -> None:
        error = _http_error(500)
        self.assertEqual(_backoff_seconds(error, 0), 1.0)


if __name__ == "__main__":
    unittest.main()
