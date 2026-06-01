class TestAuthAPI:
    async def test_register_me_and_logout(self, anonymous_client):
        register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "author@example.com",
                "password": "password123",
                "display_name": "作者",
            },
        )
        assert register.status_code == 201
        assert register.json()["user"]["email"] == "author@example.com"

        me = await anonymous_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["display_name"] == "作者"

        logout = await anonymous_client.post("/api/auth/logout")
        assert logout.status_code == 204

        after_logout = await anonymous_client.get("/api/auth/me")
        assert after_logout.status_code == 401

    async def test_project_routes_require_login(self, anonymous_client):
        response = await anonymous_client.get("/api/projects")
        assert response.status_code == 401

    async def test_projects_are_isolated_between_users(self, anonymous_client):
        first_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "first@example.com",
                "password": "password123",
                "display_name": "第一位作者",
            },
        )
        assert first_register.status_code == 201

        create_project = await anonymous_client.post("/api/projects", json={"title": "第一位作者的项目"})
        assert create_project.status_code == 201
        project_id = create_project.json()["id"]

        await anonymous_client.post("/api/auth/logout")

        second_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "second@example.com",
                "password": "password123",
                "display_name": "第二位作者",
            },
        )
        assert second_register.status_code == 201

        listing = await anonymous_client.get("/api/projects")
        assert listing.status_code == 200
        assert listing.json() == []

        direct_access = await anonymous_client.get(f"/api/projects/{project_id}")
        assert direct_access.status_code == 404

    async def test_stories_and_outlines_are_isolated_between_users(self, anonymous_client):
        first_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "story-owner@example.com",
                "password": "password123",
                "display_name": "故事作者",
            },
        )
        assert first_register.status_code == 201

        story = await anonymous_client.post(
            "/api/stories/workspace",
            json={"title": "第一位作者的故事", "content": "开篇内容"},
        )
        assert story.status_code == 200
        story_id = story.json()["story_id"]

        outline = await anonymous_client.post("/api/outlines", json={"title": "第一位作者的大纲"})
        assert outline.status_code == 200
        outline_id = outline.json()["id"]

        await anonymous_client.post("/api/auth/logout")

        second_register = await anonymous_client.post(
            "/api/auth/register",
            json={
                "email": "story-reader@example.com",
                "password": "password123",
                "display_name": "另一位作者",
            },
        )
        assert second_register.status_code == 201

        stories = await anonymous_client.get("/api/stories")
        assert stories.status_code == 200
        assert stories.json() == []

        direct_story = await anonymous_client.get(f"/api/stories/{story_id}")
        assert direct_story.status_code == 404

        direct_workspace = await anonymous_client.get(f"/api/stories/{story_id}/workspace")
        assert direct_workspace.status_code == 404

        outlines = await anonymous_client.get("/api/outlines")
        assert outlines.status_code == 200
        assert outlines.json() == []

        direct_outline = await anonymous_client.get(f"/api/outlines/{outline_id}")
        assert direct_outline.status_code == 404
