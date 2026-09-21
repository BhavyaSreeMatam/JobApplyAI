"""Anthropic client factory and structured-output helper.

Every LLM call in this project goes through here so key handling, model choice,
effort and error translation stay in one place.

Structured output is requested two ways. The fast path is `messages.parse`, which
validates server-side. If the API rejects the schema itself - it closes the schema
and can refuse one it considers too complex - we fall back to asking for plain JSON
and validating locally with the same pydantic model. The fallback is remembered per
model class so the rejection is paid at most once per process.
"""
import json
import re

import anthropic

from app.core import settings


class ClaudeUnavailable(RuntimeError):
    """Raised when the API key is missing or the API rejected the request."""


# Model classes whose schema the API has already refused this process.
_needs_json_fallback: set[str] = set()

SCHEMA_REJECTION = re.compile(r"schema is too complex|schema.*not supported|invalid schema", re.I)


def client() -> anthropic.Anthropic:
    key = settings.load()["anthropic_api_key"]
    if not key:
        raise ClaudeUnavailable(
            "No Anthropic API key configured. Open Settings and paste your key "
            "(console.anthropic.com), or set ANTHROPIC_API_KEY before starting the backend."
        )
    return anthropic.Anthropic(api_key=key, timeout=600.0, max_retries=3)


def _translate(error: Exception) -> ClaudeUnavailable:
    if isinstance(error, anthropic.AuthenticationError):
        return ClaudeUnavailable("Anthropic rejected the API key. Check it in Settings.")
    if isinstance(error, anthropic.RateLimitError):
        return ClaudeUnavailable("Anthropic rate limit reached. Wait a moment and retry.")
    if isinstance(error, anthropic.APIConnectionError):
        return ClaudeUnavailable("Could not reach the Anthropic API. Check your connection.")
    if isinstance(error, anthropic.APIStatusError):
        return ClaudeUnavailable(f"Anthropic API error: {error.message}")
    return ClaudeUnavailable(f"{type(error).__name__}: {error}")


def _check(response):
    if response.stop_reason == "refusal":
        raise ClaudeUnavailable("The model declined this request. Review the job text and retry.")
    if response.stop_reason == "max_tokens":
        raise ClaudeUnavailable("The model reached its output limit. Shorten the source material and retry.")


def _thinking_options(config: dict, effort: str | None) -> dict:
    # Haiku 4.5 supports neither adaptive thinking nor effort. The settings UI
    # offers it, so sending the adaptive options unconditionally breaks it.
    if config["model"].startswith("claude-haiku-4-5"):
        return {}
    return {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort or config["effort"]},
    }


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a text response, tolerating code fences."""
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ClaudeUnavailable("The model did not return JSON. Retry.")
    return stripped[start : end + 1]


def _parse_via_json(*, system, user, output_format, effort, max_tokens, config):
    """Schema-limit-free path: ask for JSON, validate locally with pydantic."""
    schema = json.dumps(output_format.model_json_schema(), indent=1)
    instruction = (
        f"{user}\n\n"
        "Respond with a single JSON object and nothing else - no prose, no code "
        "fence, no commentary. It must validate against this JSON Schema, including "
        "every required field:\n\n" + schema
    )
    last_error = ""
    for attempt in range(2):
        try:
            response = client().messages.create(
                model=config["model"],
                max_tokens=max_tokens,
                system=system,
                **_thinking_options(config, effort),
                messages=[{"role": "user", "content": instruction if attempt == 0 else
                           instruction + f"\n\nYour previous reply failed validation: {last_error}\n"
                                         "Return corrected JSON only."}],
            )
        except Exception as error:
            raise _translate(error) from error
        _check(response)
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return output_format.model_validate_json(_extract_json(text))
        except ClaudeUnavailable:
            raise
        except Exception as error:
            last_error = str(error)[:600]
    raise ClaudeUnavailable(
        f"The model could not produce valid {output_format.__name__} JSON. Last error: {last_error[:200]}"
    )


def parse(
    *,
    system: str,
    user,
    output_format,
    effort: str | None = None,
    max_tokens: int = 16000,
):
    """One structured-output call. Returns the validated pydantic model."""
    config = settings.load()
    name = output_format.__name__

    if name in _needs_json_fallback:
        return _parse_via_json(
            system=system, user=user, output_format=output_format,
            effort=effort, max_tokens=max_tokens, config=config,
        )

    try:
        response = client().messages.parse(
            model=config["model"],
            max_tokens=max_tokens,
            system=system,
            **_thinking_options(config, effort),
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
        )
    except anthropic.BadRequestError as error:
        message = getattr(error, "message", "") or str(error)
        if SCHEMA_REJECTION.search(message):
            # The API will not accept this schema; use the JSON path from now on.
            _needs_json_fallback.add(name)
            return _parse_via_json(
                system=system, user=user, output_format=output_format,
                effort=effort, max_tokens=max_tokens, config=config,
            )
        raise _translate(error) from error
    except Exception as error:
        raise _translate(error) from error

    _check(response)
    if response.parsed_output is None:
        raise ClaudeUnavailable("The model returned no structured output. Retry.")
    return response.parsed_output


def available() -> bool:
    return bool(settings.load()["anthropic_api_key"])
