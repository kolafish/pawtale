def project_payload(title: str = "测试", project_type: str = "novel", **overrides):
    return {"type": project_type, "title": title, **overrides}


async def create_project(client, title: str = "测试", project_type: str = "novel", **overrides):
    response = await client.post("/api/projects", json=project_payload(title, project_type, **overrides))
    assert response.status_code == 201
    return response.json()


async def create_project_id(client, title: str = "测试") -> str:
    project = await create_project(client, title)
    return project["id"]


async def create_project_with_group(client, title: str = "测试") -> tuple[str, str]:
    project = await create_project(client, title)
    return project["id"], project["groups"][0]["id"]


async def create_project_with_document(client, title: str = "测试") -> tuple[str, str]:
    project = await create_project(client, title)
    return project["id"], project["groups"][0]["documents"][0]["id"]
