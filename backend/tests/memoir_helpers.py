async def ensure_mobile_memoir_project(client, title: str = "测试回忆录") -> str:
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    active_project_id = response.json()["active_project_id"]
    if active_project_id:
        return active_project_id

    created = await client.post("/api/mobile/memoir/projects", json={"title": title})
    assert created.status_code == 201
    return created.json()["id"]


async def post_mobile_memoir_voice_session(
    client,
    json: dict,
    *,
    project_id: str | None = None,
):
    if project_id is None:
        project_id = await ensure_mobile_memoir_project(client)
    return await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": project_id},
        json=json,
    )
