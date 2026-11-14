import json
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv
import httpx

load_dotenv()


class LLMServiceError(RuntimeError):
    pass


class LLMService:
    """Ollama-first async LLM client with optional cloud API adapter and safe JSON parsing."""

    def __init__(
        self,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        cloud_model: Optional[str] = None,
        cloud_base_url: Optional[str] = None,
        timeout: float = 45.0,
    ) -> None:
        self.ollama_base_url = (
            ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.api_key = (
            api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        )
        self.provider = (
            provider or os.getenv("LLM_PROVIDER", "ollama")
        ).lower()
        self.cloud_model = cloud_model or os.getenv("LLM_CLOUD_MODEL", "gemini-2.0-flash")
        self.cloud_base_url = (
            cloud_base_url
            if cloud_base_url is not None
            else os.getenv("LLM_BASE_URL", "")
        ).rstrip("/")
        self.timeout = timeout

    @property
    def is_cloud_enabled(self) -> bool:
        return bool(self.api_key.strip())

    async def _call_cloud(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if self.cloud_base_url or self.provider in ("openai", "openai_compatible"):
                base = self.cloud_base_url or "https://api.openai.com/v1"
                messages: list[dict[str, str]] = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.cloud_model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return str(data["choices"][0]["message"]["content"]).strip()

            # Default cloud adapter: Gemini REST API
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.cloud_model}:generateContent?key={self.api_key}"
            )
            payload: dict[str, Any] = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if system_prompt:
                payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise LLMServiceError("Cloud LLM returned no candidates")
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(str(p.get("text", "")) for p in parts).strip()

    async def _call_ollama(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_thread": 4,
                "num_ctx": 2048,
                "num_predict": max_tokens,
                "temperature": temperature,
                "top_p": 0.85,
                "repeat_penalty": 1.15,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.ollama_base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("message", {}).get("content", "")).strip()
        except Exception as exc:
            raise LLMServiceError(f"Ollama request failed: {exc}") from exc

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 320,
        temperature: float = 0.2,
    ) -> str:
        if self.is_cloud_enabled:
            try:
                return await self._call_cloud(prompt, system_prompt, max_tokens, temperature)
            except Exception:
                # Fall back to local Ollama automatically on cloud failure.
                pass

        return await self._call_ollama(prompt, system_prompt, max_tokens, temperature)

    @staticmethod
    def _extract_json_object(raw: str) -> Optional[dict[str, Any]]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        fallback: Optional[dict[str, Any]] = None,
        max_tokens: int = 320,
    ) -> dict[str, Any]:
        default = dict(fallback) if fallback is not None else {}
        try:
            raw = await self.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=0.1,
            )
            parsed = self._extract_json_object(raw)
            return parsed if parsed is not None else default
        except Exception:
            return default
