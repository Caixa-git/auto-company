"""
DeepSeek API 직접 호출 래퍼.
어떤 외부 LLM 프레임워크도 사용하지 않음.
"""

import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class RateLimitError(LLMError):
    pass


class LLMClient:
    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.base_url = config["base_url"].rstrip("/")
        self.model = config["model"]
        self.max_tokens = config.get("max_tokens", 2048)
        self.temperature = config.get("temperature", 0.7)

        self._max_retries = 3
        self._retry_delay = 2.0  # seconds

    def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        DeepSeek chat completion 호출.

        Args:
            system_prompt: 에이전트 역할 정의
            messages: [{"role": "user"|"assistant", "content": "..."}]
            temperature: 오버라이드 (없으면 config 기본값)
            max_tokens: 오버라이드 (없으면 config 기본값)

        Returns:
            LLM 응답 텍스트
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }

        for attempt in range(1, self._max_retries + 1):
            try:
                return self._do_request(payload)
            except RateLimitError as e:
                wait = self._retry_delay * attempt
                logger.warning(f"Rate limit hit (attempt {attempt}/{self._max_retries}). Waiting {wait}s...")
                time.sleep(wait)
            except LLMError:
                raise
            except Exception as e:
                if attempt == self._max_retries:
                    raise LLMError(f"LLM 호출 실패 (최대 재시도 초과): {e}") from e
                logger.warning(f"LLM 호출 오류 (attempt {attempt}): {e}. 재시도 중...")
                time.sleep(self._retry_delay)

        raise LLMError("LLM 호출 실패: 최대 재시도 초과")

    def _do_request(self, payload: dict) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                raise RateLimitError(f"Rate limit: {body}")
            raise LLMError(f"HTTP {e.code}: {body}")

        except urllib.error.URLError as e:
            raise LLMError(f"네트워크 오류: {e.reason}")
