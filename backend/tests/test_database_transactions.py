from unittest.mock import AsyncMock

import pytest

from app.database import commit_or_rollback


async def test_commit_or_rollback_commits_successful_unit_of_work():
    session = AsyncMock()

    async with commit_or_rollback(session):
        pass

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("failure_site", ["body", "commit"])
async def test_commit_or_rollback_rolls_back_failures(failure_site):
    session = AsyncMock()
    if failure_site == "commit":
        session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        async with commit_or_rollback(session):
            if failure_site == "body":
                raise RuntimeError("operation failed")

    session.rollback.assert_awaited_once()
