import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.clock import utcnow_naive
from app.domain.memoir.types import MemoirShareVideoJobStatus
from app.main import app
from app.models.memoir import MemoirShareResponse, MemoirVoiceUtterance, MemoirTranscriptVersion
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project
from tests.test_memoir_book_shares import _shareable_output
from tests.test_memoir_share_likes import _family_member
from tests.test_memoir_share_video_library import _add_video_job
from tests.test_memoir_story_shares import saved_stories

BASE = '/api/mobile/memoir/share-responses'


@pytest.mark.parametrize('kind', ['book', 'chapter', 'story', 'video'])
async def test_response_private_idempotent_and_revocable(client, test_db, kind, monkeypatch, tmp_path):
    if kind == 'book':
        pid = await ensure_mobile_memoir_project(client)
    else:
        pid, chapter, _, _, section, _, _ = await saved_stories(client, test_db)
    share = f'/api/mobile/memoir/projects/{pid}/book-output/share'
    params = {'section_id': section.id} if kind == 'story' else {'chapter_id': chapter.id} if kind == 'chapter' else {}
    video = None
    if kind == 'video':
        created = await create_test_voice_transcript(client, test_db, project_id=pid)
        video = await _add_video_job(test_db, created, job_id=str(uuid4()), status=MemoirShareVideoJobStatus.SUCCEEDED, created_at=utcnow_naive())
        await test_db.commit()
        from app.services.memoir import share_video_jobs
        monkeypatch.setattr(share_video_jobs, 'completed_share_video_file', lambda job: type('File', (), {'title': '视频回忆', 'path': Path(tmp_path / 'video.mp4')})())
        # response authorization shares the actual completed metadata helper.
        monkeypatch.setattr('app.services.memoir.share_responses.completed_share_video_file', share_video_jobs.completed_share_video_file)
        target = {'video_code': video.share_code}
    else:
        if kind == 'book':
            await _shareable_output(test_db, pid, '整书正文')
        enabled = await client.put(share, params=params)
        assert enabled.status_code == 200, enabled.text
        target = {'reading_token': enabled.json()['token']}
    aunt = await _family_member(test_db, '三姑')
    cousin = await _family_member(test_db, '表弟')
    async with aunt, cousin, AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as guest:
        payload = {**target, 'request_id': str(uuid4()), 'content': '我记得那棵树是爷爷种的。'}
        before = await test_db.scalar(select(func.count()).select_from(MemoirVoiceUtterance))
        assert (await guest.post(BASE, json=payload)).status_code == 401
        first = await aunt.post(BASE, json=payload)
        assert first.status_code == 200, first.text
        row = first.json()
        assert row['target_kind'] == kind and row['display_name'] == '三姑'
        assert first.headers['cache-control'] == 'no-store'
        assert await test_db.scalar(select(func.count()).select_from(MemoirVoiceUtterance)) == before
        assert (await aunt.post(BASE, json=payload)).json()['id'] == row['id']
        assert (await aunt.post(BASE, json={**payload, 'content': '不同内容'})).status_code == 409
        assert (await cousin.get(f"{BASE}/{row['id']}")).status_code == 404
        assert (await aunt.get(f"{BASE}/{row['id']}")).status_code == 200
        assert (await aunt.post(f"{BASE}/{row['id']}/adopt")).status_code == 404
        listing = await client.get(f'/api/mobile/memoir/projects/{pid}/share-responses')
        assert listing.json()['unread_count'] == 1
        assert (await cousin.get(f'/api/mobile/memoir/projects/{pid}/share-responses')).status_code == 404
        assert (await client.post(f"{BASE}/{row['id']}/read")).json()['read_at']
        # The author confirms once; concurrent retries return the same material.
        from app.database import async_session_maker
        from app.models.projects import Project
        from app.services.memoir.share_responses import adopt_response
        owner_id = (await test_db.get(Project, pid)).user_id
        await test_db.commit()
        async def adopt():
            async with async_session_maker() as session:
                return await adopt_response(session, row['id'], owner_id)
        adopted = await asyncio.gather(adopt(), adopt())
        utterance_id = adopted[0].adopted_utterance_id
        assert adopted[1].adopted_utterance_id == utterance_id
        utterance = await test_db.get(MemoirVoiceUtterance, utterance_id)
        assert '三姑对《' in utterance.transcript_edited and payload['content'] in utterance.transcript_edited
        assert utterance.audio_file_id is None
        versions = list(await test_db.scalars(select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.source_artifact_id == utterance_id)))
        assert [v.version_type for v in versions] == ['user_edited']
        assert await test_db.scalar(select(func.count()).select_from(MemoirVoiceUtterance)) == before + 1
        if video:
            video.share_enabled = False
            await test_db.commit()
        else:
            await client.delete(share, params=params)
        assert (await aunt.post(BASE, json=payload)).status_code == 404
        assert (await aunt.get(f"{BASE}/{row['id']}")).status_code == 200
        assert (await client.get(f"{BASE}/{row['id']}")).status_code == 200


async def test_response_pagination_and_invalid_input(client, test_db):
    pid, chapter, _, _, section, _, _ = await saved_stories(client, test_db)
    path = f'/api/mobile/memoir/projects/{pid}/book-output/share'
    token = (await client.put(path, params={'section_id': section.id})).json()['token']
    aunt = await _family_member(test_db, '阿姨')
    async with aunt:
        for content in ['', '   ', '字' * 2001]:
            assert (await aunt.post(BASE, json={'reading_token': token, 'content': content, 'request_id': 'invalid'})).status_code == 422
        first = (await aunt.post(BASE, json={'reading_token': token, 'content': '第一条', 'request_id': '1'})).json()
        row = await test_db.get(MemoirShareResponse, first['id'])
        for n in range(20):
            test_db.add(MemoirShareResponse(project_id=pid, user_id=row.user_id, request_id=f'page-{n}', target_kind='story', target_id=section.id, target_title='标题', display_name='阿姨', content=str(n)))
        await test_db.commit()
        listing = f'/api/mobile/memoir/projects/{pid}/share-responses'
        page1 = (await client.get(listing)).json()
        page2 = (await client.get(listing, params={'before_id': page1['next_before_id']})).json()
        assert len(page1['items']) == 20 and len(page2['items']) == 1
        assert len({r['id'] for r in page1['items'] + page2['items']}) == 21

async def test_response_submission_reports_deleted_object_after_lock_wait(monkeypatch):
    from fastapi import HTTPException
    from app.services.memoir import share_responses, book_shares, share_video_shares

    class Gone:
        async def scalar(self, _query):
            return None

    from app.models.memoir import MemoirSection, MemoirMaterialShareVideo
    async def text_read(_db, _token):
        return MemoirSection(id='gone'), object()
    async def video_read(_db, _code):
        return MemoirMaterialShareVideo(id='gone')
    monkeypatch.setattr(book_shares, 'readable_share', text_read)
    monkeypatch.setattr(share_video_shares, '_shared_job', video_read)
    for target in ({'reading_token': 'old'}, {'video_code': '0' * 20}):
        with pytest.raises(HTTPException) as exc:
            from app.schemas.memoir_share_likes import MemoirShareLikeTarget
            await share_responses.response_target(Gone(), MemoirShareLikeTarget(**target))
        assert exc.value.status_code == 404

async def test_response_waiting_for_story_archive_rechecks_fresh_status(client, test_db, monkeypatch):
    from app.database import async_session_maker
    from app.schemas.memoir_share_likes import MemoirShareLikeTarget
    from app.services.memoir import book_shares, share_responses

    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    token = (await client.put(f'/api/mobile/memoir/projects/{pid}/book-output/share', params={'section_id': section.id})).json()['token']
    first_read = asyncio.Event()
    original = book_shares.readable_share

    async def observed(db, value):
        result = await original(db, value)
        first_read.set()
        return result

    monkeypatch.setattr(book_shares, 'readable_share', observed)
    async with async_session_maker() as writer, async_session_maker() as submitter:
        pending = await writer.get(type(section), section.id, with_for_update=True)
        task = asyncio.create_task(share_responses.response_target(submitter, MemoirShareLikeTarget(reading_token=token)))
        try:
            await asyncio.wait_for(first_read.wait(), 3)
            pending.status = 'archived'
            await writer.commit()
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status_code in (404, 409)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
