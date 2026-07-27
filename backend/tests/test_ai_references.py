import pytest

from app.services.ai_references import ModelReferenceMap


def test_model_references_are_short_stable_and_strict():
    references = ModelReferenceMap(
        "M",
        ["uuid-first", "uuid-second", "uuid-first"],
    )

    assert references.encode("uuid-first") == "M1"
    assert references.encode("uuid-second") == "M2"
    assert references.decode_many(["M2", "M1", "M2"], limit=3) == [
        "uuid-second",
        "uuid-first",
    ]
    with pytest.raises(ValueError, match="unknown reference"):
        references.decode("uuid-first")
