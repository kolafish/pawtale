# Pawtale local embedding service

This private CPU service loads `BAAI/bge-small-zh-v1.5` from a local directory and exposes the
small HTTP contract used by the Pawtale backend. It does not download models at startup and must
listen on loopback only in production.

## Runtime layout

- Source: the deployed Pawtale `backend/` directory
- Python dependencies: `/opt/pawtale/embedding-service/.pythondeps`
- Model: `/opt/pawtale/embedding-service/models/bge-small-zh-v1.5`
- Listener: `127.0.0.1:8102`

Install the isolated dependencies with:

```bash
python3 -m pip install \
  --target /opt/pawtale/embedding-service/.pythondeps \
  --no-cache-dir \
  -r backend/embedding_service/requirements.txt
```

Set `PAWTALE_EMBEDDING_MODEL_PATH` if the model is stored elsewhere, then start the service with
`backend/scripts/start_embedding_service.sh`. Run `backend/scripts/smoke_embedding_service.py`
after startup to verify the health response, model identity, dimensions, vector count, and norms.

## HTTP contract

- `GET /health`
- `POST /v1/embeddings`

Request:

```json
{
  "model": "BAAI/bge-small-zh-v1.5",
  "texts": ["外婆家的院子", "第一次离开故乡"]
}
```

The service accepts 1–128 non-empty texts per request, limits each text to 20,000 characters, and
returns normalized 512-dimensional vectors in input order. Request logs never contain input text.
