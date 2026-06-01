import json
import re
from collections.abc import Iterator
from typing import Any


def strip_json_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def try_parse_json(text: str, *, allow_array: bool = False) -> Any | None:
    for candidate in (text, strip_json_trailing_commas(text)):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) or (allow_array and isinstance(parsed, list)):
            return parsed
    return None


def iter_fenced_blocks(text: str) -> Iterator[str]:
    parts = text.split("```")
    for i in range(1, len(parts), 2):
        block = parts[i]
        lowered = block.lower().lstrip()
        if lowered.startswith("json"):
            lines = block.splitlines()
            yield "\n".join(lines[1:]) if lines else ""
        else:
            yield block


def extract_json_payload(content: str, *, allow_array: bool = False) -> Any | None:
    text = (content or "").strip()
    if not text:
        return None

    parsed = try_parse_json(text, allow_array=allow_array)
    if parsed is not None:
        return parsed

    for block in iter_fenced_blocks(text):
        parsed = try_parse_json(block.strip(), allow_array=allow_array)
        if parsed is not None:
            return parsed

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "[{":
            continue
        if char == "[" and not allow_array:
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) or (allow_array and isinstance(parsed, list)):
            return parsed

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return try_parse_json(text[start:end + 1], allow_array=allow_array)

    return None
