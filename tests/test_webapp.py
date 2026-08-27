"""Tests de webapp.py : le routage/la validation sont testés directement
(aucun réseau). L'exécution des jobs remplace les 5 étapes du pipeline —
déjà couvertes par de vrais tests ailleurs (test_script.py, test_images.py,
test_voice.py, test_subtitles.py, test_montage.py) — par des doubles
rapides, pour vérifier l'orchestration (statuts, thread, endpoints) sans
attendre plusieurs minutes de génération réelle à chaque run de la suite.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from storygen import webapp
from storygen.models import Story


class TestIndexAndHealthz(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(webapp.app)

    def test_index_serves_form(self) -> None:
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("<form", res.text)
        self.assertIn("StoryGen", res.text)

    def test_healthz(self) -> None:
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})


class TestGenerateValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(webapp.app)

    def test_rejects_empty_prompt(self) -> None:
        res = self.client.post("/generate", data={"prompt": "   "})
        self.assertEqual(res.status_code, 400)

    def test_rejects_unknown_format(self) -> None:
        res = self.client.post("/generate", data={"prompt": "test", "format": "4:3"})
        self.assertEqual(res.status_code, 400)

    def test_rejects_out_of_range_duration(self) -> None:
        res = self.client.post("/generate", data={"prompt": "test", "duration": 9999})
        self.assertEqual(res.status_code, 400)

    def test_unknown_job_status_returns_404(self) -> None:
        res = self.client.get("/status/does-not-exist")
        self.assertEqual(res.status_code, 404)


STORY = Story.model_validate(
    {
        "title": "T",
        "logline": "L",
        "style_bible": {"art_style": "x", "palette": "y", "seed": 1},
        "characters": [],
        "scenes": [
            {"index": 1, "narration": "n", "image_prompt": "x", "characters_present": [], "duration_hint": 2.0}
        ],
    }
)


def _poll_until_final(client: TestClient, job_id: str) -> dict:
    data = {"status": "queued"}
    for _ in range(50):
        data = client.get(f"/status/{job_id}").json()
        if data["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    return data


class TestJobOrchestration(unittest.TestCase):
    def test_job_reaches_done_with_video_url(self) -> None:
        with (
            mock.patch("storygen.webapp.generate_story", return_value=(STORY, Path("/tmp/x"))),
            mock.patch("storygen.webapp.generate_images", return_value=[]),
            mock.patch("storygen.webapp.generate_voices", return_value={1: 2.0}),
            mock.patch("storygen.webapp.generate_subtitles", return_value=Path("/tmp/x/subtitles.srt")),
            mock.patch("storygen.webapp.generate_montage", return_value=Path("/tmp/output/x.mp4")),
        ):
            client = TestClient(webapp.app)
            res = client.post("/generate", data={"prompt": "un test"})
            self.assertEqual(res.status_code, 200)

            data = _poll_until_final(client, res.json()["job_id"])
            self.assertEqual(data["status"], "done")
            self.assertEqual(data["video_url"], "/videos/x.mp4")

    def test_job_reports_error_status(self) -> None:
        with mock.patch("storygen.webapp.generate_story", side_effect=RuntimeError("boom")):
            client = TestClient(webapp.app)
            res = client.post("/generate", data={"prompt": "un test"})

            data = _poll_until_final(client, res.json()["job_id"])
            self.assertEqual(data["status"], "error")
            self.assertIn("boom", data["error"])


if __name__ == "__main__":
    unittest.main()
