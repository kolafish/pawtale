import json


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


async def test_mobile_memoir_review_surfaces(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.memoir.memory_extraction.get_ai_service",
        lambda ai_model=None: FakeReviewAIClient(),
    )
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    session = session_response.json()["session"]
    question = session_response.json()["current_question"]
    answer_response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question["id"], "text": "我第一次去重庆厂里上班，是老张带我去的。"},
    )
    assert answer_response.status_code == 201

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
    coverage_response = await client.get("/api/mobile/memoir/coverage")
    candidates_response = await client.get("/api/mobile/memoir/question-candidates")
    outputs_response = await client.get("/api/mobile/memoir/outputs")

    assert summary_response.status_code == 200
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
    assert selected_response.status_code == 200
    assert selected_response.json()["source"] == "ai_generated"

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
