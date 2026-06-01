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

        create_project = await anonymous_client.post(
            "/api/projects",
            json={"type": "novel", "title": "第一位作者的项目"},
        )
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
