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


async def test_mobile_memoir_identity_includes_active_project(client):
    response = await client.get("/api/mobile/memoir/me")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "tester@example.com"
    assert data["active_project_id"] == data["active_project"]["id"]
    assert data["active_project"]["type"] == "memoir"
    assert len(data["projects"]) == 1
    assert data["projects"][0]["id"] == data["active_project_id"]


async def test_mobile_memoir_projects_can_be_created_updated_and_activated(client):
    initial_response = await client.get("/api/mobile/memoir/projects")
    default_project_id = initial_response.json()["active_project_id"]

    created_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "母亲的回忆", "description": "先从童年聊起"},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["title"] == "母亲的回忆"
    assert created["type"] == "memoir"

    renamed_response = await client.patch(
        f"/api/mobile/memoir/projects/{created['id']}",
        json={"title": "外婆的回忆"},
    )
    assert renamed_response.status_code == 200
    assert renamed_response.json()["title"] == "外婆的回忆"

    project_list_response = await client.get("/api/mobile/memoir/projects")
    project_list = project_list_response.json()
    assert project_list["active_project_id"] == created["id"]
    assert [project["title"] for project in project_list["projects"]] == [
        "外婆的回忆",
        "我的回忆录",
    ]

    activated_response = await client.post(
        f"/api/mobile/memoir/projects/{default_project_id}/activate",
    )
    assert activated_response.status_code == 200
    activated = activated_response.json()
    assert activated["active_project_id"] == default_project_id
    assert activated["active_project"]["title"] == "我的回忆录"


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


async def test_mobile_memoir_dashboard_returns_ecs_backend_snapshot(client):
    response = await client.get("/api/mobile/memoir/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["project"]["type"] == "memoir"
    assert data["interview"]["session"]["status"] == "active"
    assert data["summary"]["total_question_count"] == 5
    assert len(data["coverage"]) > 0
    assert data["memory_stats"] == {
        "total_memory_count": 0,
        "draft_memory_count": 0,
        "confirmed_memory_count": 0,
        "rejected_memory_count": 0,
        "open_question_count": 0,
        "asked_open_question_count": 0,
        "answered_open_question_count": 0,
        "dismissed_open_question_count": 0,
        "question_candidate_count": 0,
        "output_count": 0,
        "coverage_average": 0,
        "low_coverage_count": len(data["coverage"]),
    }


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
