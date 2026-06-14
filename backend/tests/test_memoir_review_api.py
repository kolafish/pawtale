import json

from sqlalchemy import func, select

from app.domain.projects.types import ProjectType
from app.models.memoir import MemoirCoverageSnapshot, MemoirPersonMemory, MemoirQuestionCandidate, MemoirSession
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.answers import answer_question_with_text


class FakeReviewAIClient:
    provider_name = "fake"
    model_name = "fake-review-model"

    async def _call_llm(self, *args, **kwargs):
        return json.dumps(
            {
                "people": [
                    {
                        "canonical_name": "老张",
                        "relationship": "同事",
                        "description": "带受访者去重庆厂里的人",
                        "confidence": 0.85,
                        "evidence_quote": "老张带我去的",
                    }
                ],
                "events": [
                    {
                        "title": "第一次去重庆工作",
                        "life_stage": "青年",
                        "time_text": "第一次去重庆厂里上班",
                        "process": "老张带受访者去重庆厂里",
                        "confidence": 0.9,
                        "evidence_quote": "第一次去重庆厂里上班",
                    }
                ],
                "places": [
                    {
                        "name": "重庆厂",
                        "elder_alias": "那个厂",
                        "place_type": "工作地点",
                        "confidence": 0.8,
                        "evidence_quote": "重庆厂里上班",
                    }
                ],
                "themes": [
                    {
                        "name": "骄傲",
                        "description": "回望第一次工作时仍有价值感",
                        "strength": 0.7,
                        "mention_count": 1,
                        "confidence": 0.75,
                        "evidence_quote": "还是有点骄傲",
                    }
                ],
                "open_questions": [
                    {
                        "question": "第一次去重庆厂那天，是谁送你出门的？",
                        "question_type": "scene",
                        "reason": "缺少出发场景",
                        "sensitivity": "low",
                        "priority": 0.82,
                        "target_memory_type": "event",
                        "target_title": "第一次去重庆工作",
                        "evidence_quote": "老张带我去的",
                    }
                ],
                "session_summary": "本次访谈沉淀了第一次去重庆工作的经历。",
                "story_fragment": "第一次去重庆厂里上班，是老张带我去的。",
            },
            ensure_ascii=False,
        )

    def _wrap_debug(self, *args, **kwargs):
        return {}


async def test_mobile_memoir_review_surfaces(client, test_db, monkeypatch):
    monkeypatch.setattr(
        "app.services.memoir.memory_extraction.get_ai_service",
        lambda ai_model=None: FakeReviewAIClient(),
    )
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    session = session_response.json()["session"]
    question = session_response.json()["current_question"]
    user = await test_db.scalar(select(User).where(User.email == "tester@example.com"))
    await answer_question_with_text(
        test_db,
        question["id"],
        user.id,
        "我第一次去重庆厂里上班，是老张带我去的。",
    )

    process_response = await client.post(
        f"/api/mobile/memoir/sessions/{session['id']}/process",
        json={},
    )
    assert process_response.status_code == 200

    summary_response = await client.get(f"/api/mobile/memoir/sessions/{session['id']}/summary")
    people_response = await client.get("/api/mobile/memoir/memories/people")
    events_response = await client.get("/api/mobile/memoir/memories/events")
    places_response = await client.get("/api/mobile/memoir/memories/places")
    themes_response = await client.get("/api/mobile/memoir/memories/themes")
    open_questions_response = await client.get("/api/mobile/memoir/open-questions")
    recalculate_response = await client.post("/api/mobile/memoir/coverage/recalculate")
    generate_candidates_response = await client.post("/api/mobile/memoir/question-candidates/generate")
    coverage_response = await client.get("/api/mobile/memoir/coverage")
    candidates_response = await client.get("/api/mobile/memoir/question-candidates")
    outputs_response = await client.get("/api/mobile/memoir/outputs")

    assert summary_response.status_code == 200
    assert recalculate_response.status_code == 200
    assert generate_candidates_response.status_code == 200
    assert summary_response.json()["summary"] == "本次访谈沉淀了第一次去重庆工作的经历。"
    assert people_response.json()[0]["canonical_name"] == "老张"
    assert events_response.json()[0]["title"] == "第一次去重庆工作"
    assert places_response.json()[0]["name"] == "重庆厂"
    assert themes_response.json()[0]["name"] == "骄傲"
    assert open_questions_response.json()[0]["question"].startswith("第一次去重庆厂")
    assert any(item["key"] == "first_job" for item in coverage_response.json())
    assert candidates_response.json()[0]["question"].startswith("第一次去重庆厂")
    assert outputs_response.json()[0]["output_type"] == "story_fragment"

    person_id = people_response.json()[0]["id"]
    update_memory_response = await client.patch(
        f"/api/mobile/memoir/memories/person/{person_id}",
        json={"status": "confirmed", "canonical_name": "张师傅"},
    )
    assert update_memory_response.status_code == 200
    assert update_memory_response.json()["status"] == "confirmed"
    assert update_memory_response.json()["canonical_name"] == "张师傅"

    candidate_id = candidates_response.json()[0]["id"]
    selected_response = await client.post(
        f"/api/mobile/memoir/question-candidates/{candidate_id}/select"
    )
    assert selected_response.status_code == 404

    open_question_id = open_questions_response.json()[0]["id"]
    update_open_question_response = await client.patch(
        f"/api/mobile/memoir/open-questions/{open_question_id}",
        json={"status": "dismissed", "reason": "家人已线下补充"},
    )
    assert update_open_question_response.status_code == 200
    assert update_open_question_response.json()["status"] == "dismissed"

    output_id = outputs_response.json()[0]["id"]
    output_detail_response = await client.get(f"/api/mobile/memoir/outputs/{output_id}")
    assert output_detail_response.status_code == 200
    assert output_detail_response.json()["versions"][0]["content"].startswith("第一次去重庆厂")


async def test_mobile_memoir_review_respects_explicit_project_id(client, test_db):
    user = await test_db.scalar(select(User).where(User.email == "tester@example.com"))
    first = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="第一本回忆录")
    second = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="第二本回忆录")
    test_db.add_all([first, second])
    await test_db.flush()
    test_db.add_all([
        MemoirPersonMemory(project_id=first.id, canonical_name="第一本的人"),
        MemoirPersonMemory(project_id=second.id, canonical_name="第二本的人"),
    ])
    await test_db.flush()

    first_response = await client.get(f"/api/mobile/memoir/memories/people?project_id={first.id}")
    second_response = await client.get(f"/api/mobile/memoir/memories/people?project_id={second.id}")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert [item["canonical_name"] for item in first_response.json()] == ["第一本的人"]
    assert [item["canonical_name"] for item in second_response.json()] == ["第二本的人"]


async def test_mobile_question_candidates_get_is_read_only_without_current_session(client, test_db):
    user = await test_db.scalar(select(User).where(User.email == "tester@example.com"))
    project = Project(user_id=user.id, type=ProjectType.MEMOIR.value, title="空回忆录")
    test_db.add(project)
    await test_db.flush()

    response = await client.get(f"/api/mobile/memoir/question-candidates?project_id={project.id}")

    assert response.status_code == 200
    assert response.json() == []
    assert await test_db.scalar(select(func.count()).select_from(MemoirSession)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirQuestionCandidate)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirCoverageSnapshot)) == 0
