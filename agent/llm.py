import os
import time
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

try:
    from openai import OpenAI
    _openai_available = True
except ImportError:
    _openai_available = False


class LLMClient:
    """OpenRouter-backed LLM client targeting DeepSeek V3."""

    DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324"

    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self._client: Optional[object] = None

        if self.api_key and _openai_available:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://github.com/tenacious-ce/conversion-engine",
                    "X-Title": "Tenacious Conversion Engine",
                },
            )

    def is_available(self) -> bool:
        return self._client is not None

    def generate(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        system: Optional[str] = None,
    ) -> dict:
        if not self.is_available():
            return {"text": "", "error": "LLM client not configured (missing OPENROUTER_API_KEY)"}

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.time() - t0) * 1000)
            content = resp.choices[0].message.content or ""
            usage = resp.usage
            return {
                "text": content.strip(),
                "model": model,
                "latency_ms": latency_ms,
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            }
        except Exception as e:
            return {"text": "", "error": str(e), "latency_ms": int((time.time() - t0) * 1000)}

    def generate_json(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        system: Optional[str] = None,
    ) -> dict:
        """Generate and parse JSON response."""
        import json, re
        result = self.generate(prompt, model=model, temperature=temperature,
                               max_tokens=max_tokens, system=system)
        text = result.get("text", "")
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                result["parsed"] = json.loads(match.group())
            except json.JSONDecodeError:
                result["parsed"] = {}
        else:
            result["parsed"] = {}
        return result
