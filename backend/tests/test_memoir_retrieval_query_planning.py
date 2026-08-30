from __future__ import annotations

import json

import pytest

from app.services.ai_calls import AIInvalidStructuredOutputError
from app.services.ai_factory import DEEPSEEK_V4_FLASH_MODEL
from app.services.memoir.hybrid_retrieval import HybridRetrievalTrace
from app.services.memoir import retrieval_query_planning
from app.services.memoir.retrieval_query_planning import (
    RETRIEVAL_UNIT_PROMPT_VERSION,
    RetrievalUnitPurpose,
    generate_retrieval_units,
    plan_and_retrieve_memory_candidates,
)


class FakeAIClient:
    provider_name = "fake-provider"
    model_name = "fake-flash"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=None,
        return_raw=False,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "return_raw": return_raw,
            }
        )
        return json.dumps(self.payload, ensure_ascii=False), {
            "model": self.model_name,
            "choices": [{"finish_reason": "stop"}],
            "usage": {"total_tokens": 80},
        }


def _empty_hybrid_trace(project_id: str) -> HybridRetrievalTrace:
    return HybridRetrievalTrace(
        project_id=project_id,
        excluded_voice_session_id=None,
        requested_embedding_model=None,
        resolved_embedding_model=None,
        embedding_dimensions=None,
        embedding_elapsed_ms=0,
        retrieval_elapsed_ms=0,
        queries=(),
        retrieved_entry_union_count=0,
        retrieved_source_union_count=0,
        candidates=(),
    )


async def test_query_units_use_flash_contract_without_post_quality_rules():
    fake = FakeAIClient(
        {
            "purpose": "query",
            "units": [
                "李桂芳在华西医院接受乳腺癌手术",
                " 李桂芳在华西医院接受乳腺癌手术 ",
                "之前有没有相关经历",
                "",
            ],
            "decision_reason": "当前讲述包含明确人物和治疗事件。",
        }
    )

    trace = await generate_retrieval_units(
        purpose=RetrievalUnitPurpose.QUERY,
        input_text="她后来是在华西医院做的手术。",
        ai_client=fake,
    )

    assert trace.prompt_version == RETRIEVAL_UNIT_PROMPT_VERSION
    assert trace.units == (
        "李桂芳在华西医院接受乳腺癌手术",
        "之前有没有相关经历",
    )
    assert trace.requested_model == DEEPSEEK_V4_FLASH_MODEL
    assert trace.resolved_model == "fake-flash"
    assert trace.provider == "fake-provider"
    assert trace.usage == {"total_tokens": 80}
    assert trace.attempt_count == 1
    assert trace.max_tokens == 512
    assert trace.temperature == 0.1
    assert trace.finish_reason == "stop"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert "purpose=query" in call["system_prompt"]
    assert "老余口述”只是产品名称" in call["system_prompt"]
    assert "禁止把任何人物写成“老余”" in call["system_prompt"]
    assert "只依据 current_narration" in call["system_prompt"]
    assert "decision_reason 必须解释是否生成 unit，并与 units 保持一致" in call[
        "system_prompt"
    ]
    assert "current_narration:\n她后来是在华西医院做的手术。" in call["user_prompt"]
    assert "previous_ai_question" not in call["user_prompt"]
    assert call["max_tokens"] == 512
    assert call["return_raw"] is True


async def test_shared_index_purpose_uses_source_contract():
    fake = FakeAIClient(
        {
            "purpose": "index",
            "units": ["讲述者退休后到印刷厂帮忙"],
            "decision_reason": "原文包含可独立检索的工作经历。",
        }
    )

    trace = await generate_retrieval_units(
        purpose=RetrievalUnitPurpose.INDEX,
        input_text="退休以后，我又去印刷厂帮了几年忙。",
        context_anchor="后来退休以后呢？",
        requested_model="deepseek-v4-pro",
        ai_client=fake,
    )

    assert trace.purpose is RetrievalUnitPurpose.INDEX
    assert trace.requested_model == "deepseek-v4-pro"
    assert trace.units == ("讲述者退休后到印刷厂帮忙",)
    assert "purpose=index" in fake.calls[0]["system_prompt"]
    assert "source_text:\n退休以后，我又去印刷厂帮了几年忙。" in fake.calls[0]["user_prompt"]
    assert "context_anchor:\n后来退休以后呢？" in fake.calls[0]["user_prompt"]


async def test_empty_query_decision_skips_hybrid_retrieval(test_db, monkeypatch):
    fake = FakeAIClient(
        {
            "purpose": "query",
            "units": [],
            "decision_reason": "这只是操作指令，没有历史召回方向。",
        }
    )

    async def unexpected_retrieval(*args, **kwargs):
        raise AssertionError("hybrid retrieval must not run")

    monkeypatch.setattr(
        retrieval_query_planning,
        "retrieve_hybrid_candidates",
        unexpected_retrieval,
    )
    trace = await plan_and_retrieve_memory_candidates(
        test_db,
        project_id="project-1",
        current_narration="先暂停一下。",
        ai_client=fake,
    )

    assert trace.should_retrieve is False
    assert trace.retrieval_queries == ()
    assert trace.hybrid_retrieval is None


async def test_query_plan_adds_raw_narration_and_calls_hybrid_retrieval(
    test_db,
    monkeypatch,
):
    narration = "她后来是在华西医院做的手术。"
    fake = FakeAIClient(
        {
            "purpose": "query",
            "units": [
                narration,
                "李桂芳在华西医院接受乳腺癌手术",
                "李桂芳在华西医院接受乳腺癌手术",
            ],
            "decision_reason": "需要召回同一人物的治疗经过。",
        }
    )
    calls: list[dict] = []
    embedding_client = object()

    async def fake_retrieval(db, **kwargs):
        calls.append({"db": db, **kwargs})
        return _empty_hybrid_trace(kwargs["project_id"])

    monkeypatch.setattr(
        retrieval_query_planning,
        "retrieve_hybrid_candidates",
        fake_retrieval,
    )
    trace = await plan_and_retrieve_memory_candidates(
        test_db,
        project_id="project-1",
        current_narration=narration,
        excluded_voice_session_id="current-session",
        ai_client=fake,
        embedding_client=embedding_client,
    )

    assert trace.should_retrieve is True
    assert trace.retrieval_queries == (
        narration,
        "李桂芳在华西医院接受乳腺癌手术",
    )
    assert trace.hybrid_retrieval is not None
    assert len(calls) == 1
    call = calls[0]
    assert call["db"] is test_db
    assert call["queries"] == list(trace.retrieval_queries)
    assert call["excluded_voice_session_id"] == "current-session"
    assert call["embedding_client"] is embedding_client
    assert (call["vector_limit"], call["bm25_limit"], call["candidate_limit"]) == (
        8,
        8,
        12,
    )


async def test_retrieval_unit_purpose_mismatch_is_rejected():
    fake = FakeAIClient(
        {
            "purpose": "index",
            "units": ["错误目的"],
            "decision_reason": "错误",
        }
    )

    with pytest.raises(AIInvalidStructuredOutputError):
        await generate_retrieval_units(
            purpose=RetrievalUnitPurpose.QUERY,
            input_text="我小时候住在外婆家。",
            ai_client=fake,
        )


async def test_query_units_reject_context_instead_of_silently_using_it():
    fake = FakeAIClient(
        {
            "purpose": "query",
            "units": [],
            "decision_reason": "不会调用",
        }
    )

    with pytest.raises(ValueError, match="do not accept context"):
        await generate_retrieval_units(
            purpose=RetrievalUnitPurpose.QUERY,
            input_text="后来还是他送我去的。",
            context_anchor="梁老师后来怎么样？",
            ai_client=fake,
        )
    assert fake.calls == []
