"""Client HTTP minimal en stdlib pur (``urllib``).

Volontairement sans ``httpx``/``requests`` : ni l'un ni l'autre ne figure
dans la stack imposée, et de simples appels JSON synchrones + un
téléchargement binaire avec retry ne justifient pas la dépendance.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger("storygen.http")

# Constat réel (Phase 2) : Pollinations (derrière Cloudflare) renvoie 403 sur
# le User-Agent par défaut d'urllib ("Python-urllib/3.x"), alors qu'il laisse
# passer curl ou une requête sans User-Agent. On s'identifie explicitement
# plutôt que de se faire passer pour autre chose.
DEFAULT_USER_AGENT = "StoryGen/0.1 (+https://github.com/BoubacarThiam/storygen)"


class HttpError(RuntimeError):
    """Erreur réseau ou HTTP normalisée pour toutes les étapes du pipeline."""


def _with_default_ua(headers: dict[str, str] | None) -> dict[str, str]:
    return {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers=_with_default_ua(headers), method="GET")
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
    req_headers = _with_default_ua({"Content-Type": "application/json", **(headers or {})})
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise HttpError(f"HTTP {e.code} sur {url} : {detail[:500]}") from e
    except urllib.error.URLError as e:
        raise HttpError(f"Échec réseau sur {url} : {e}") from e


# Constat réel (usage intensif de Pollinations pendant les tests) : un 429
# (Too Many Requests) ne se dissipe pas en 1s/2s/4s — le retry générique
# abandonnait avant que la limite ne se lève. Un 429 attend donc beaucoup
# plus longtemps (45s/60s, ou la valeur de l'en-tête Retry-After si fourni)
# qu'une erreur réseau ordinaire.
#
# Pollinations renvoie aussi 402 quand le quota anonyme est atteint (et 503
# quand l'API Classic est saturée) : ce ne sont pas des erreurs définitives
# mais des limites de débit. Elles doivent donc attendre aussi longtemps que
# le 429, sinon les 5 tentatives s'épuisent en quelques secondes.
_RATE_LIMIT_CODES = frozenset({402, 429, 503})
_RATE_LIMIT_BACKOFF_S = 45.0
_RATE_LIMIT_BACKOFF_MAX_S = 60.0

# Erreurs pour lesquelles réessayer ne sert à rien (clé absente/invalide,
# modèle interdit, URL inconnue) : on échoue tout de suite avec un message
# actionnable plutôt que d'attendre 5 tentatives.
_NON_RETRYABLE_CODES = frozenset({401, 403, 404})


def _backoff_seconds(error: Exception, attempt: int) -> float:
    if isinstance(error, urllib.error.HTTPError) and error.code in _RATE_LIMIT_CODES:
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if retry_after:
            try:
                return min(float(retry_after), _RATE_LIMIT_BACKOFF_MAX_S)
            except ValueError:
                pass
        return min(_RATE_LIMIT_BACKOFF_S * (attempt + 1), _RATE_LIMIT_BACKOFF_MAX_S)
    return float(2**attempt)


def _error_hint(error: Exception | None) -> str:
    """Suffixe actionnable ajouté au message final (sans fuite de clé API :
    la clé voyage dans un en-tête, jamais dans l'URL loggée)."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (401, 402, 403):
            return (
                " — Pollinations exige une clé API (avec du Pollen gratuit via "
                "les Quests) : renseigne STORYGEN_POLLINATIONS_API_KEY, clé "
                "créée sur https://enter.pollinations.ai/keys. Sans clé, l'API "
                "Classic est limitée à 1 image toutes les 45 s par IP."
            )
        if error.code == 503:
            return (
                " — l'API Pollinations gratuite est saturée ; réessaie plus "
                "tard ou ajoute STORYGEN_POLLINATIONS_API_KEY."
            )
    return ""


def download_binary(
    url: str,
    timeout: float = 60.0,
    retries: int = 3,
    validate: Callable[[bytes], bool] | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """Téléchargement avec retry (utilisé par steps/images.py) : backoff
    exponentiel (1s/2s/4s...) pour une erreur réseau ordinaire, backoff bien
    plus long pour un 429 (voir ``_backoff_seconds``). Si ``validate`` est
    fourni et renvoie False (contenu vide/corrompu/page d'erreur déguisée en
    200), le téléchargement est traité comme un échec et retenté comme les
    autres. Vérifie aussi que la taille reçue correspond à Content-Length
    quand l'en-tête est présent, pour détecter un flux tronqué.

    Un 402/429/503 (quota Pollinations) bénéficie d'un backoff long (voir
    ``_backoff_seconds``) ; un 401/403/404 échoue immédiatement avec une
    indication de correction (réessayer ne changerait rien)."""
    last_error: Exception | None = None
    attempts_made = 0
    extra_headers = headers or {}
    for attempt in range(retries):
        attempts_made = attempt + 1
        try:
            req = urllib.request.Request(url, headers=_with_default_ua(extra_headers), method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                content_length = resp.headers.get("Content-Length")
                if content_length is not None and int(content_length) != len(data):
                    raise HttpError(
                        f"Téléchargement tronqué : {len(data)}/{content_length} octets reçus"
                    )
            if validate is not None and not validate(data):
                raise HttpError("Contenu téléchargé invalide (échec de la validation)")
            return data
        except (urllib.error.URLError, TimeoutError, HttpError) as e:
            last_error = e
            if isinstance(e, urllib.error.HTTPError) and e.code in _NON_RETRYABLE_CODES:
                break
            if attempt < retries - 1:
                wait = _backoff_seconds(e, attempt)
                logger.warning(
                    "Échec téléchargement %s (%s) — attente %.0fs avant tentative %d/%d...",
                    url, e, wait, attempt + 2, retries,
                )
                time.sleep(wait)
    raise HttpError(
        f"Échec après {attempts_made} tentative(s) sur {url} : {last_error}{_error_hint(last_error)}"
    )
