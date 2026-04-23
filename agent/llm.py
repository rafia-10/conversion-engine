import os
from dotenv import load_dotenv

load_dotenv()

try:
    import openai
except ImportError:
    openai = None


class LLMClient:
    def __init__(self):
        # Use OpenRouter as the primary LLM provider
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")  # fallback

        # Set OpenAI to use OpenRouter if available
        if self.openrouter_key and openai:
            openai.api_key = self.openrouter_key
            openai.api_base = "https://openrouter.ai/api/v1"

    def is_available(self) -> bool:
        return bool(self.openrouter_key or self.openai_key)

    def generate(self, prompt: str, model: str = "deepseek/deepseek-chat", temperature: float = 0.3) -> dict:
        if not self.is_available():
            return {"text": ""}

        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=2000,
            )
            choices = response.get("choices", [])
            if choices:
                return {"text": choices[0].get("message", {}).get("content", "").strip()}
            return {"text": ""}
        except Exception as e:
            print(f"LLM generation failed: {e}")
            return {"text": ""}
