from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class OllamaClient:
    base_url: str
    model: str

    def generate(self, prompt: str, *, timeout_seconds: int = 240) -> str:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "top_p": 0.9,
                },
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("response", "")
        if not isinstance(text, str):
            return ""
        return text.strip()

