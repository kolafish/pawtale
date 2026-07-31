from collections.abc import Iterable, Mapping
import re


class ModelReferenceMap:
    def __init__(
        self,
        prefix: str,
        identifiers: Iterable[str],
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        values = tuple(dict.fromkeys(identifiers))
        self._references = {
            identifier: f"{prefix}{index}"
            for index, identifier in enumerate(values, start=1)
        }
        self._identifiers = {
            reference: identifier
            for identifier, reference in self._references.items()
        }
        self._labels = {
            self._references[identifier]: label
            for identifier, label in (labels or {}).items()
            if identifier in self._references
        }

    def encode(self, identifier: str) -> str:
        try:
            return self._references[identifier]
        except KeyError as exc:
            raise ValueError("internal identifier is not mapped") from exc

    def decode(self, reference: object) -> str:
        value = reference.strip() if isinstance(reference, str) else ""
        try:
            return self._identifiers[value]
        except KeyError as exc:
            raise ValueError("model returned an unknown reference") from exc

    def decode_many(self, references: object, *, limit: int) -> list[str]:
        if not isinstance(references, list):
            return []
        result: list[str] = []
        for reference in references:
            identifier = self.decode(reference)
            if identifier not in result:
                result.append(identifier)
            if len(result) >= limit:
                break
        return result

    def render_text(self, value: object) -> str:
        text = value if isinstance(value, str) else ""
        if not self._labels:
            return text
        pattern = "|".join(
            re.escape(reference)
            for reference in sorted(self._labels, key=len, reverse=True)
        )
        return re.sub(
            rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])",
            lambda match: self._labels[match.group(0)],
            text,
        )
