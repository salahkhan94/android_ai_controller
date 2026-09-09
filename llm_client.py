"""OpenAI Responses API adapter for structured draft generation.

Send a prepared text-only request with strict JSON output and storage disabled.
Reject incomplete responses and refusals before JSON parsing. Credentials come
from OPENAI_API_KEY in the environment or the project's .env file; existing
environment values take precedence. No Android controls or sending tools exist.
"""

import json
import os
from pathlib import Path


def load_project_env(path=None):
    """Load the project's .env without replacing exported environment values."""
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=path if path is not None else Path(__file__).resolve().parent / ".env",
                override=False, interpolate=False)


def generate_json(request, model, client=None):
    owns_client = client is None
    if owns_client:
        load_project_env()
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("Set OPENAI_API_KEY in the project's .env file or local environment.")
        from openai import OpenAI
        client = OpenAI(base_url="https://api.openai.com/v1", timeout=60.0, max_retries=0)
    try:
        response = client.responses.create(model=model, store=False, max_output_tokens=2500, **request)
        if response.status != "completed":
            raise ValueError("Model response was incomplete; no drafts were saved. Try a larger output budget or another model.")
        for output in response.output:
            if output.type == "message":
                if any(part.type == "refusal" for part in output.content):
                    raise ValueError("Model declined the draft request; no drafts were saved.")
        if not response.output_text:
            raise ValueError("Model returned no draft text.")
        result = json.loads(response.output_text)
        return result, {"response_id": response.id, "model": response.model,
                        "usage": response.usage.model_dump() if response.usage else None}
    finally:
        if owns_client:
            client.close()
