"""
Thin wrapper around Groq's chat completions API (model set via LLM_MODEL).
Isolating this means swapping to Gemini/OpenAI later is a one-file change
everywhere else in the app just calls llm_client.generate().
"""
import logging

from groq import Groq

from app.config import settings

logger = logging.getLogger(__name__)

_client = Groq(api_key=settings.groq_api_key)


def generate(prompt: str, temperature: float = 0.1, max_tokens: int = 800) -> str:
    """
    Sends a single-turn completion request to Groq and returns the raw text.
    Low temperature (0.1) by default since SQL generation needs determinism,
    not creativity.
    """
    try:
        response = _client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except Exception as exc:
        logger.error(f"LLM call failed: {exc}", exc_info=True)
        raise