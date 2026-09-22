from app.config import DEFAULT_AI_TEXT_MODEL
from app.services.ai_calls import AIStructuredCallResult, AITextCallResult
from project_helpers import create_project


async def _article_with_content(client) -> tuple[str, str]:
    project = await create_project(client, "口述文章", project_type="article")
    document_id = project["groups"][0]["documents"][0]["id"]
    saved = await client.put(
        f"/api/projects/{project['id']}/documents/{document_id}",
        json={"content": "今天想谈谈，为什么我们需要给生活留一点空白。"},
    )
    assert saved.status_code == 200
    return project["id"], document_id


async def test_article_ai_uses_template_free_article_workflows(client, monkeypatch):
    project_id, document_id = await _article_with_content(client)
    ai_client = FakeArticleAIClient()
    monkeypatch.setattr(
        "app.services.projects.ai.get_ai_service",
        lambda _model=None: ai_client,
    )

    continued = await client.post(
        f"/api/projects/{project_id}/documents/{document_id}/ai-continue",
        json={
            "instruction": "接着说明空白带来的好处",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
            "expected_content_revision": 1,
        },
    )
    revised = await client.post(
        f"/api/projects/{project_id}/documents/{document_id}/ai-revise",
        json={
            "instruction": "表达得更简洁",
            "selected_text": "给生活留一点空白",
            "full_content": "今天想谈谈，为什么我们需要给生活留一点空白。",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
            "expected_content_revision": 2,
        },
    )
    compared = await client.post(
        f"/api/projects/{project_id}/documents/{document_id}/ai-revise-multi",
        json={
            "instruction": "语气更自然",
            "models": [DEFAULT_AI_TEXT_MODEL],
            "expected_content_revision": 2,
        },
    )
    reviewed = await client.post(
        f"/api/projects/{project_id}/documents/{document_id}/ai-review",
        json={"ai_model": DEFAULT_AI_TEXT_MODEL, "expected_content_revision": 2},
    )

    assert continued.status_code == 200
    assert continued.json()["generated_chunk"] == "接下来，留白让我们重新听见自己的节奏。"
    assert continued.json()["content_revision"] == 2
    assert revised.status_code == 200
    assert revised.json()["revised_text"] == "给日常留一点空白"
    assert revised.json()["content_revision"] == 2
    assert compared.status_code == 200
    assert compared.json()["results"][0]["revised_text"] == "给日常留一点空白"
    assert compared.json()["content_revision"] == 2
    assert reviewed.status_code == 200
    assert reviewed.json()["summary"] == "主题清楚，可以再压缩一处口语重复。"
    assert reviewed.json()["suggestions"][0]["excerpt"] == "今天想谈谈"
    assert reviewed.json()["content_revision"] == 2

    continue_call, revise_call, multi_call, review_call = ai_client.calls
    assert "不要套用固定文章模板" in continue_call["system_prompt"]
    assert "言情小说" not in continue_call["system_prompt"]
    assert "全文上下文" in revise_call["user_prompt"]
    assert "不要套用固定文章模板" in multi_call["system_prompt"]
    assert "不是回忆录事实完整性检查" in review_call["system_prompt"]
    assert all(call["requested_model"] == DEFAULT_AI_TEXT_MODEL for call in ai_client.calls)

    stale = await client.post(
        f"/api/projects/{project_id}/documents/{document_id}/ai-review",
        json={"expected_content_revision": 1},
    )
    assert stale.status_code == 409
    assert len(ai_client.calls) == 4


async def test_article_review_rejects_non_article_project(client, monkeypatch):
    project = await create_project(client, "小说", project_type="novel")
    document_id = project["groups"][0]["documents"][0]["id"]
    monkeypatch.setattr(
        "app.services.projects.ai.get_ai_service",
        lambda _model=None: (_ for _ in ()).throw(AssertionError("AI must not be called")),
    )

    response = await client.post(
        f"/api/projects/{project['id']}/documents/{document_id}/ai-review",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅文章项目支持文章检查"


class FakeArticleAIClient:
    provider_name = "fake"
    model_name = "deepseek-v4-flash"

    def __init__(self):
        self.calls: list[dict] = []

    async def generate_text(
        self,
        system_prompt,
        user_prompt,
        *,
        requested_model,
        temperature,
        max_tokens,
        timeout,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "requested_model": requested_model,
            }
        )
        content = (
            "接下来，留白让我们重新听见自己的节奏。"
            if "写作搭档" in system_prompt
            else "给日常留一点空白"
        )
        return AITextCallResult(
            content=content,
            provider=self.provider_name,
            requested_model=requested_model,
            resolved_model=self.model_name,
            response_model=self.model_name,
            finish_reason="stop",
            response_json={"model": self.model_name},
        )

    async def generate_structured(
        self,
        system_prompt,
        user_prompt,
        *,
        output_model,
        requested_model,
        temperature,
        max_tokens,
        timeout,
        max_attempts,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "requested_model": requested_model,
            }
        )
        response = AITextCallResult(
            content='{"summary":"主题清楚，可以再压缩一处口语重复。"}',
            provider=self.provider_name,
            requested_model=requested_model,
            resolved_model=self.model_name,
            response_model=self.model_name,
            finish_reason="stop",
            response_json={"model": self.model_name},
        )
        return AIStructuredCallResult(
            value=output_model.model_validate(
                {
                    "summary": "主题清楚，可以再压缩一处口语重复。",
                    "suggestions": [
                        {
                            "excerpt": "今天想谈谈",
                            "issue": "开头略口语化",
                            "suggestion": "可直接从主题切入",
                        }
                    ],
                }
            ),
            response=response,
        )

    def _wrap_debug(self, *args, **kwargs):
        return {"request": {}, "response": {}, "meta": {}}
