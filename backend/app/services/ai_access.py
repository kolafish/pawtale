from __future__ import annotations

from contextvars import ContextVar, Token

from app.config import DEFAULT_AI_TEXT_MODEL


PUBLIC_AI_TEXT_MODELS = frozenset({DEFAULT_AI_TEXT_MODEL})
_REQUEST_AI_MODEL_SCOPE: ContextVar[str | None] = ContextVar(
    "request_ai_model_scope",
    default=None,
)


def set_request_ai_model_scope(*, is_admin: bool) -> Token[str | None]:
    """Apply the model scope for the current authenticated request."""
    return _REQUEST_AI_MODEL_SCOPE.set("admin" if is_admin else "public")


def reset_request_ai_model_scope(token: Token[str | None]) -> None:
    _REQUEST_AI_MODEL_SCOPE.reset(token)


def restrict_requested_ai_model(model: str) -> str:
    """Keep public requests on the low-cost product model.

    Background workers have no request scope and keep their explicitly stored
    model so admin-created jobs and internal workflows continue to work.
    """
    if _REQUEST_AI_MODEL_SCOPE.get() != "public":
        return model
    return model if model in PUBLIC_AI_TEXT_MODELS else DEFAULT_AI_TEXT_MODEL
