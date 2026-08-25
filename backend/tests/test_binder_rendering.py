"""Test that Binder component can render without crashing."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import get_db
from app.models.users import User
from app.models.projects import Project, DocumentGroup, Document


@pytest_asyncio.fixture
async def client(test_db, tmp_path):
    """Create a test client backed by the shared PostgreSQL fixture."""
    from unittest.mock import patch

    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.content_storage.PROJECTS_DIR", tmp_path / "projects"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/register",
                json={
                    "email": "binder@example.com",
                    "password": "password123",
                    "display_name": "Binder 用户",
                },
            )
            assert response.status_code == 201
            yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sample_project(test_db, client):
    """Create a sample project with groups and documents."""
    from sqlalchemy import select

    result = await test_db.execute(select(User).where(User.email == "binder@example.com"))
    user = result.scalar_one()

    project = Project(
        user_id=user.id,
        type="novel",
        title="测试项目",
        description="测试描述",
        word_count_target=50000,
    )
    test_db.add(project)
    await test_db.flush()

    group1 = DocumentGroup(
        project_id=project.id,
        name="第一卷",
        sort_order=0,
    )
    group2 = DocumentGroup(
        project_id=project.id,
        name="第二卷",
        sort_order=1,
    )
    test_db.add(group1)
    test_db.add(group2)
    await test_db.flush()

    doc1 = Document(
        group_id=group1.id,
        name="开端",
        word_count=7,
        sort_order=0,
    )
    doc2 = Document(
        group_id=group1.id,
        name="发展",
        word_count=7,
        sort_order=1,
    )
    doc3 = Document(
        group_id=group2.id,
        name="高潮",
        word_count=7,
        sort_order=0,
    )
    test_db.add_all([doc1, doc2, doc3])
    await test_db.commit()

    return project.id


@pytest.mark.asyncio
async def test_get_project_structure(client: AsyncClient, sample_project):
    """Test that project API returns correct structure for Binder rendering."""
    project_id = sample_project

    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200

    data = response.json()

    assert "id" in data
    assert "title" in data
    assert "groups" in data
    assert len(data["groups"]) == 2

    for group in data["groups"]:
        assert "id" in group
        assert "name" in group
        assert "sort_order" in group
        assert "documents" in group
        assert isinstance(group["documents"], list)

    group1 = data["groups"][0]
    assert len(group1["documents"]) == 2

    for doc in group1["documents"]:
        assert "id" in doc
        assert "group_id" in doc
        assert "name" in doc
        assert "status" in doc
        assert "word_count" in doc
        assert "sort_order" in doc
        assert "content" not in doc


@pytest.mark.asyncio
async def test_reorder_groups_maintains_structure(client: AsyncClient, sample_project):
    """Test that reordering groups doesn't break the data structure."""
    project_id = sample_project

    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    initial_data = response.json()

    group1_id = initial_data["groups"][0]["id"]
    group2_id = initial_data["groups"][1]["id"]

    reorder_data = [
        {"id": group2_id, "sort_order": 0},
        {"id": group1_id, "sort_order": 1},
    ]
    response = await client.put(f"/api/projects/{project_id}/groups/reorder", json=reorder_data)
    assert response.status_code == 200

    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    updated_data = response.json()

    assert len(updated_data["groups"]) == 2
    assert updated_data["groups"][0]["id"] == group2_id
    assert updated_data["groups"][1]["id"] == group1_id


@pytest.mark.asyncio
async def test_reorder_documents_cross_group(client: AsyncClient, sample_project):
    """Test that moving documents across groups maintains structure."""
    project_id = sample_project

    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    initial_data = response.json()

    group1_id = initial_data["groups"][0]["id"]
    group2_id = initial_data["groups"][1]["id"]
    doc1_id = initial_data["groups"][0]["documents"][0]["id"]
    doc2_id = initial_data["groups"][0]["documents"][1]["id"]
    doc3_id = initial_data["groups"][1]["documents"][0]["id"]

    reorder_data = [
        {"id": doc1_id, "group_id": group1_id, "sort_order": 0},
        {"id": doc3_id, "group_id": group2_id, "sort_order": 0},
        {"id": doc2_id, "group_id": group2_id, "sort_order": 1},
    ]
    response = await client.put(f"/api/projects/{project_id}/documents/reorder", json=reorder_data)
    assert response.status_code == 200

    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    updated_data = response.json()

    assert len(updated_data["groups"][0]["documents"]) == 1
    assert len(updated_data["groups"][1]["documents"]) == 2
    assert updated_data["groups"][0]["documents"][0]["id"] == doc1_id
    assert updated_data["groups"][1]["documents"][0]["id"] == doc3_id
    assert updated_data["groups"][1]["documents"][1]["id"] == doc2_id
