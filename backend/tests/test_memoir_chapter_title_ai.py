import json

import pytest
from fastapi import HTTPException

from app.services.ai_base import AITextServiceBase
from app.services.memoir.book_plan_ai import generate_chapter_title_from_sources


class TitleAI(AITextServiceBase):
    provider_name = "test"
    model_name = "title-model"

    def __init__(self, title):
        self.title = title

    async def _call_llm(self, *args, **kwargs):
        return json.dumps({"title": self.title}, ensure_ascii=False)


@pytest.mark.parametrize("title", ["", "   ", "\n\t", "“”", "新章节"])
async def test_unusable_structured_title_is_rejected(title):
    with pytest.raises(HTTPException):
        await generate_chapter_title_from_sources(sources=[], ai_model="title-model", ai_client=TitleAI(title))


@pytest.mark.parametrize("title", ["外婆家的院子", "  “外婆家的院子”  ", "标题：外婆家的院子。"])
async def test_structured_title_is_normalized(title):
    result = await generate_chapter_title_from_sources(
        sources=[], ai_model="title-model", ai_client=TitleAI(title),
    )
    assert result.title == "外婆家的院子"
    assert result.model == "title-model"


async def test_title_uses_validated_value_instead_of_reparsing_response():
    class ValidatedTitleAI(TitleAI):
        async def generate_structured(self, *args, **kwargs):
            result = await super().generate_structured(*args, **kwargs)
            return type(result)(value=kwargs["output_model"](title="外婆家的院子"), response=result.response)

    result = await generate_chapter_title_from_sources(
        sources=[], ai_model="title-model", ai_client=ValidatedTitleAI("未规范化标题"),
    )
    assert result.title == "外婆家的院子"
