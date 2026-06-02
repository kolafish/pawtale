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


async def test_mobile_memoir_text_answer_flow(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    assert session_response.status_code == 200
    question_id = session_response.json()["current_question"]["id"]

    answer_response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": " 我记得外婆家的院子。 "},
    )
    assert answer_response.status_code == 201
    answer = answer_response.json()
    assert answer["status"] == "answered"
    assert answer["transcript_raw"] == "我记得外婆家的院子。"
    assert answer["transcript_edited"] == "我记得外婆家的院子。"

    updated_response = await client.patch(
        f"/api/mobile/memoir/answers/{answer['id']}",
        json={"text": "外婆家的院子里有一棵树。"},
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["transcript_raw"] == "我记得外婆家的院子。"
    assert updated["transcript_edited"] == "外婆家的院子里有一棵树。"

    next_response = await client.get("/api/mobile/memoir/questions/next")
    assert next_response.status_code == 200
    assert next_response.json()["id"] != question_id


async def test_mobile_memoir_rejects_blank_answer(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    response = await client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "  "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "回答不能为空"


async def test_mobile_memoir_skip_question(client):
    session_response = await client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    response = await client.post(f"/api/mobile/memoir/questions/{question_id}/skip")

    assert response.status_code == 200
    data = response.json()
    assert data["questions"][0]["status"] == "skipped"
    assert data["completed_question_count"] == 1
    assert data["pending_question_count"] == 4


async def test_mobile_memoir_hides_other_users_questions(anonymous_client):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "memoir-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    session_response = await anonymous_client.get("/api/mobile/memoir/sessions/current")
    question_id = session_response.json()["current_question"]["id"]

    await anonymous_client.post("/api/auth/logout")

    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "memoir-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    response = await anonymous_client.post(
        "/api/mobile/memoir/answers",
        json={"question_id": question_id, "text": "越权回答"},
    )

    assert response.status_code == 404
