"""Client HTTP minimal en stdlib pur (``urllib``).

Volontairement sans ``httpx``/``requests`` : ni l'un ni l'autre ne figure
dans la stack imposée, et de simples appels JSON synchrones + un
téléchargement binaire avec retry ne justifient pas la dépendance.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    """Erreur réseau ou HTTP normalisée pour toutes les étapes du pipeline."""


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise HttpError(f"HTTP {e.code} sur {url} : {detail[:500]}") from e
    except urllib.error.URLError as e:
        raise HttpError(f"Échec réseau sur {url} : {e}") from e


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise HttpError(f"HTTP {e.code} sur {url} : {detail[:500]}") from e
    except urllib.error.URLError as e:
        raise HttpError(f"Échec réseau sur {url} : {e}") from e


def download_binary(url: str, timeout: float = 60.0, retries: int = 3) -> bytes:
    """Téléchargement avec retry exponentiel (utilisé par steps/images.py,
    Phase 2) : 3 essais, pause 1s/2s/4s entre les tentatives."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise HttpError(f"Échec après {retries} tentatives sur {url} : {last_error}")
