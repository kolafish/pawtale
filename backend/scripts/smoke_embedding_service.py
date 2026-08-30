from __future__ import annotations

import argparse
import json
import math
from urllib.request import Request, urlopen


EXPECTED_MODEL = "BAAI/bge-small-zh-v1.5"
EXPECTED_DIMENSIONS = 512


def read_json(request: Request) -> dict[str, object]:
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed local URL by default
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8102")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    health = read_json(Request(f"{base_url}/health"))
    assert health == {
        "status": "ok",
        "ready": True,
        "model": EXPECTED_MODEL,
        "dimensions": EXPECTED_DIMENSIONS,
    }

    body = json.dumps(
        {"model": EXPECTED_MODEL, "texts": ["外婆家的院子", "第一次离开故乡"]},
        ensure_ascii=False,
    ).encode("utf-8")
    result = read_json(
        Request(
            f"{base_url}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    )
    assert result["model"] == EXPECTED_MODEL
    assert result["dimensions"] == EXPECTED_DIMENSIONS
    assert result["normalized"] is True
    embeddings = result["embeddings"]
    assert isinstance(embeddings, list) and len(embeddings) == 2
    for embedding in embeddings:
        assert isinstance(embedding, list) and len(embedding) == EXPECTED_DIMENSIONS
        norm = math.sqrt(sum(float(value) ** 2 for value in embedding))
        assert math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4)
    print(
        json.dumps(
            {
                "status": "ok",
                "model": result["model"],
                "dimensions": result["dimensions"],
                "vectors": len(embeddings),
                "elapsed_ms": result["elapsed_ms"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
