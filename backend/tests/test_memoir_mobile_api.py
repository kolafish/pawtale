async def test_mobile_memoir_routes_require_login(anonymous_client):
    response = await anonymous_client.get("/api/mobile/memoir/me")

    assert response.status_code == 401


async def test_mobile_memoir_default_project_is_created_once(client):
    first = await client.get("/api/mobile/memoir/projects")
    second = await client.get("/api/mobile/memoir/projects")

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data["active_project_id"] == second_data["active_project_id"]
    assert len(first_data["projects"]) == 1
    assert first_data["projects"][0]["type"] == "memoir"
    assert first_data["projects"][0]["title"] == "我的回忆录"
    assert first_data["projects"][0]["groups"][0]["name"] == "回忆片段"
    assert first_data["projects"][0]["groups"][0]["documents"][0]["name"] == "第一次记录"


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


async def test_mobile_memoir_projects_can_be_deleted_but_keeps_one_project(client):
    initial_response = await client.get("/api/mobile/memoir/projects")
    default_project_id = initial_response.json()["active_project_id"]

    created_response = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "父亲的回忆"},
    )
    created_project_id = created_response.json()["id"]

    delete_response = await client.delete(f"/api/mobile/memoir/projects/{created_project_id}")
    assert delete_response.status_code == 204

    project_list_response = await client.get("/api/mobile/memoir/projects")
    project_list = project_list_response.json()
    assert project_list["active_project_id"] == default_project_id
    assert [project["id"] for project in project_list["projects"]] == [default_project_id]

    last_delete_response = await client.delete(f"/api/mobile/memoir/projects/{default_project_id}")
    assert last_delete_response.status_code == 400
    assert last_delete_response.json()["detail"] == "至少保留一本回忆录"


async def test_mobile_memoir_legacy_routes_are_removed(client):
    legacy_requests = [
        ("GET", "/api/mobile/memoir/project", None),
        ("GET", "/api/mobile/memoir/dashboard", None),
        ("POST", "/api/mobile/memoir/sessions", {"title": "童年"}),
        ("GET", "/api/mobile/memoir/sessions/current", None),
        ("POST", "/api/mobile/memoir/sessions/legacy-session/process", {}),
        ("GET", "/api/mobile/memoir/sessions/legacy-session/summary", None),
        ("GET", "/api/mobile/memoir/questions/next", None),
        ("POST", "/api/mobile/memoir/answers", {"question_id": "legacy-question", "text": "旧回答"}),
        ("PATCH", "/api/mobile/memoir/answers/legacy-answer", {"text": "旧编辑"}),
        ("POST", "/api/mobile/memoir/answers/legacy-answer/draft", {}),
        ("PATCH", "/api/mobile/memoir/drafts/legacy-draft", {"content_edited": "旧草稿"}),
        ("POST", "/api/mobile/memoir/drafts/legacy-draft/accept", None),
        ("POST", "/api/mobile/memoir/audio-files", {}),
        ("POST", "/api/mobile/memoir/questions/legacy-question/skip", None),
        ("GET", "/api/mobile/memoir/memories/people", None),
        ("GET", "/api/mobile/memoir/memories/events", None),
        ("GET", "/api/mobile/memoir/memories/places", None),
        ("GET", "/api/mobile/memoir/memories/themes", None),
        ("PATCH", "/api/mobile/memoir/memories/person/legacy-memory", {"status": "confirmed"}),
        ("GET", "/api/mobile/memoir/open-questions", None),
        ("PATCH", "/api/mobile/memoir/open-questions/legacy-question", {"status": "dismissed"}),
        ("GET", "/api/mobile/memoir/coverage", None),
        ("POST", "/api/mobile/memoir/coverage/recalculate", None),
        ("GET", "/api/mobile/memoir/question-candidates", None),
        ("POST", "/api/mobile/memoir/question-candidates/generate", None),
        ("POST", "/api/mobile/memoir/question-candidates/legacy-candidate/select", None),
        ("GET", "/api/mobile/memoir/outputs", None),
        ("GET", "/api/mobile/memoir/outputs/legacy-output", None),
        ("GET", "/api/mobile/memoir/summary", None),
        ("GET", "/api/mobile/memoir/stories", None),
        ("GET", "/api/mobile/memoir/stories/legacy-answer", None),
    ]

    for method, path, body in legacy_requests:
        request = getattr(client, method.lower())
        response = await request(path, json=body) if body is not None else await request(path)
        assert response.status_code == 404, f"{method} {path}"
