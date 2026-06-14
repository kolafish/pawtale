async def test_mobile_memoir_routes_require_login(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/project")

    assert response.status_code == 401


async def test_mobile_memoir_project_is_created_once(client):
    first = await client.get("/api/mobile/memoir/project")
    second = await client.get("/api/mobile/memoir/project")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["type"] == "memoir"
    assert first.json()["title"] == "我的回忆录"
    assert first.json()["groups"][0]["name"] == "回忆片段"
    assert first.json()["groups"][0]["documents"][0]["name"] == "第一次记录"


async def test_mobile_memoir_current_session_initializes_questions(client):
    response = await client.get("/api/mobile/memoir/sessions/current")

    assert response.status_code == 200
    data = response.json()
    assert data["session"]["status"] == "active"
    assert data["current_question"]["id"] == data["session"]["current_question_id"]
    assert data["completed_question_count"] == 0
    assert data["pending_question_count"] == 5
    assert len(data["questions"]) == 5


async def test_mobile_memoir_can_create_custom_session(client):
    response = await client.post(
        "/api/mobile/memoir/sessions",
        json={"title": "童年"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["session"]["title"] == "童年"
    assert data["current_question"] is not None


async def test_mobile_memoir_legacy_write_routes_are_removed(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    requests = [
        client.post("/api/mobile/memoir/answers", json={"question_id": question_id, "text": "旧回答"}),
        client.patch("/api/mobile/memoir/answers/legacy-answer", json={"text": "旧编辑"}),
        client.post("/api/mobile/memoir/answers/legacy-answer/draft", json={}),
        client.patch("/api/mobile/memoir/drafts/legacy-draft", json={"content_edited": "旧草稿"}),
        client.post("/api/mobile/memoir/drafts/legacy-draft/accept"),
        client.post("/api/mobile/memoir/audio-files", json={}),
        client.post(f"/api/mobile/memoir/questions/{question_id}/skip"),
        client.post("/api/mobile/memoir/question-candidates/legacy-candidate/select"),
    ]

    responses = [await request for request in requests]

    assert [response.status_code for response in responses] == [404] * len(responses)
