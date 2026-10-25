import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.clock import utcnow_naive
from app.database import async_session_maker
from app.models.memoir import MemoirChatMessage, MemoirShareResponse, MemoirShareResponseVoice, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_common import AudioFile
from app.models.projects import Project
from app.models.users import User
from app.services.asr.streaming import StreamingASREvent
from app.services import account_deletion
from app.services.memoir import audio_files, narrator_profile, project_deletion, share_response_voice, share_responses
from tests.test_memoir_share_likes import _family_member
from tests.test_memoir_story_shares import saved_stories

BASE = '/api/mobile/memoir/share-responses'


async def test_voice_response_retains_original_audio_asr_and_confirmed_text(client, test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(audio_files, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(audio_files, 'MEMOIR_AUDIO_DIR', tmp_path / 'memoir' / 'audio')
    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    token = (await client.put(f'/api/mobile/memoir/projects/{pid}/book-output/share', params={'section_id': section.id})).json()['token']
    aunt = await _family_member(test_db, '三姑')
    cousin = await _family_member(test_db, '表弟')
    target = {'reading_token': token}
    async with aunt, cousin, AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as guest:
        assert (await guest.post(f'{BASE}/voice', json=target)).status_code == 401
        ticket = await aunt.post(f'{BASE}/voice', json=target)
        assert ticket.status_code == 200, ticket.text
        capture_id = ticket.json()['capture_id']
        cap = await test_db.get(MemoirShareResponseVoice, capture_id)
        assert cap.project_id == pid and cap.audio_file_id is None
        persistence = share_response_voice.ResponseCapturePersistence(capture_id=capture_id, user_id=cap.user_id)
        assert await persistence.authorized()
        await persistence.record_audio_frame(b'\x00\x00' * 16000)
        result = await persistence.persist_final(StreamingASREvent(
            type='final', text='我记得那棵树', start_ms=120, end_ms=850,
            raw={'provider_segment': 'private-test'},
        ))
        assert result == {'voice_capture_id': capture_id}
        await test_db.refresh(cap)
        original = await test_db.get(AudioFile, cap.audio_file_id)
        assert original.user_id == cap.user_id
        assert cap.asr_text == '我记得那棵树' and cap.provider_payload_json == {'provider_segment': 'private-test'}
        assert (cap.start_ms, cap.end_ms) == (120, 850)
        assert (await client.get(f'{BASE}/not-submitted/audio')).status_code == 404
        assert (await aunt.post(f'{BASE}/voice', json=target)).status_code == 200
        submitted = await aunt.post(BASE, json={**target, 'request_id': str(uuid4()),
            'content': '我记得那棵树是爷爷种的。', 'voice_capture_id': capture_id})
        assert submitted.status_code == 200, submitted.text
        response_id = submitted.json()['id']
        assert submitted.json()['voice_capture_id'] == capture_id
        assert (await cousin.get(f'{BASE}/{response_id}/audio')).status_code == 404
        raw_audio = await client.get(f'{BASE}/{response_id}/audio')
        assert raw_audio.status_code == 200 and raw_audio.content.startswith(b'RIFF')
        assert raw_audio.headers['cache-control'] == 'no-store'
        assert (await aunt.get(f'{BASE}/{response_id}/audio')).status_code == 200
        assert (await aunt.post(BASE, json={**target, 'request_id': str(uuid4()),
            'content': '重复使用原音', 'voice_capture_id': capture_id})).status_code == 409
        schedule_profile = AsyncMock()
        monkeypatch.setattr(narrator_profile, 'schedule_narrator_profile', schedule_profile)
        adopted = await client.post(f'{BASE}/{response_id}/adopt')
        assert adopted.status_code == 200, adopted.text
        utterance_id = adopted.json()['adopted_utterance_id']
        utterance = await test_db.get(MemoirVoiceUtterance, utterance_id)
        assert (utterance.transcript_raw, utterance.start_ms, utterance.end_ms) == ('我记得那棵树', 120, 850)
        assert '爷爷种的' in utterance.transcript_edited
        assert utterance.provider_payload_json['voice_capture_id'] == capture_id
        assert utterance.provider_payload_json['original_audio_file_id'] == original.id
        assert utterance.provider_payload_json['asr_provider_payload'] == {'provider_segment': 'private-test'}
        versions = list(await test_db.scalars(select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.source_artifact_id == utterance_id,
        ).order_by(MemoirTranscriptVersion.created_at)))
        assert {version.version_type for version in versions} == {'asr_raw', 'user_edited'}
        raw_version = next(version for version in versions if version.version_type == 'asr_raw')
        edited_version = next(version for version in versions if version.version_type == 'user_edited')
        assert utterance.provider_payload_json['asr_transcript_version_id'] == raw_version.id
        assert raw_version.content == cap.asr_text
        assert edited_version.content == utterance.transcript_edited
        message = await test_db.get(MemoirChatMessage, utterance.chat_message_id)
        assert message.transcript_version_id == edited_version.id
        assert message.metadata_json['asr_transcript_version_id'] == raw_version.id
        copied = await test_db.get(AudioFile, utterance.audio_file_id)
        assert copied.id != original.id and copied.user_id == (await test_db.get(Project, pid)).user_id
        assert audio_files.audio_file_path(copied).exists() and audio_files.audio_file_path(original).exists()
        assert (await client.post(f'{BASE}/{response_id}/adopt')).json()['adopted_utterance_id'] == utterance_id
        schedule_profile.assert_not_awaited()
        cap.expires_at = utcnow_naive() - timedelta(days=2)
        await test_db.commit()
        assert await share_response_voice.cleanup_expired_unsubmitted_captures(test_db) == 0
        assert audio_files.audio_file_path(original).exists()
        monkeypatch.setattr(account_deletion, 'DATA_DIR', tmp_path)
        await account_deletion.delete_account(test_db, await test_db.get(User, cap.user_id))
        assert await test_db.get(MemoirShareResponse, response_id) is None
        assert await test_db.get(MemoirShareResponseVoice, capture_id) is None
        assert await test_db.get(AudioFile, original.id) is None
        assert not audio_files.audio_file_path(original).exists()
        assert await test_db.get(Project, pid) is not None
        assert audio_files.audio_file_path(copied).exists()


async def test_voice_capture_rejects_revoked_share_and_discards_unsent_audio(client, test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(audio_files, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(audio_files, 'MEMOIR_AUDIO_DIR', tmp_path / 'memoir' / 'audio')
    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    params = {'section_id': section.id}
    path = f'/api/mobile/memoir/projects/{pid}/book-output/share'
    token = (await client.put(path, params=params)).json()['token']
    aunt = await _family_member(test_db, '表姑')
    async with aunt:
        ticket = (await aunt.post(f'{BASE}/voice', json={'reading_token': token})).json()['capture_id']
        cap = await test_db.get(MemoirShareResponseVoice, ticket)
        persistence = share_response_voice.ResponseCapturePersistence(capture_id=ticket, user_id=cap.user_id)
        await persistence.record_audio_frame(b'\x00\x00' * 800)
        await client.delete(path, params=params)
        assert not await persistence.authorized()
        await persistence.stop(None, 'terminal_error')
        assert not list((tmp_path / 'memoir' / 'audio' / '_pending' / cap.user_id).glob('*.part'))
        assert (await aunt.post(BASE, json={'reading_token': token,
            'request_id': str(uuid4()), 'content': '不能再提交', 'voice_capture_id': ticket})).status_code == 404


async def test_abandoned_voice_capture_and_audio_are_removed_after_retention(client, test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(audio_files, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(audio_files, 'MEMOIR_AUDIO_DIR', tmp_path / 'memoir' / 'audio')
    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    token = (await client.put(f'/api/mobile/memoir/projects/{pid}/book-output/share',
        params={'section_id': section.id})).json()['token']
    aunt = await _family_member(test_db, '舅妈')
    async with aunt:
        capture_id = (await aunt.post(f'{BASE}/voice', json={'reading_token': token})).json()['capture_id']
        capture = await test_db.get(MemoirShareResponseVoice, capture_id)
        persistence = share_response_voice.ResponseCapturePersistence(capture_id=capture_id, user_id=capture.user_id)
        await persistence.record_audio_frame(b'\x00\x00' * 800)
        await persistence.persist_final(StreamingASREvent(type='final', text='村口有棵树', raw={}))
        await test_db.refresh(capture)
        audio = await test_db.get(AudioFile, capture.audio_file_id)
        path = audio_files.audio_file_path(audio)
        assert path.exists()
        capture.expires_at = utcnow_naive() - timedelta(days=2)
        await test_db.commit()
        assert await share_response_voice.cleanup_expired_unsubmitted_captures(test_db) == 1
        assert await test_db.scalar(select(MemoirShareResponseVoice.id).where(
            MemoirShareResponseVoice.id == capture_id)) is None
        assert await test_db.scalar(select(AudioFile.id).where(AudioFile.id == audio.id)) is None
        assert not path.exists()
        expired_submit = await aunt.post(BASE, json={'reading_token': token,
            'request_id': str(uuid4()), 'content': '文字还在', 'voice_capture_id': capture_id})
        assert expired_submit.status_code == 410


async def test_project_delete_before_voice_adoption_does_not_leave_copied_audio(client, test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(audio_files, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(audio_files, 'MEMOIR_AUDIO_DIR', tmp_path / 'memoir' / 'audio')
    pid, _, _, _, section, _, _ = await saved_stories(client, test_db)
    owner_id = (await client.get('/api/auth/me')).json()['id']
    token = (await client.put(f'/api/mobile/memoir/projects/{pid}/book-output/share',
        params={'section_id': section.id})).json()['token']
    aunt = await _family_member(test_db, '姑姑')
    async with aunt:
        capture_id = (await aunt.post(f'{BASE}/voice', json={'reading_token': token})).json()['capture_id']
        capture = await test_db.get(MemoirShareResponseVoice, capture_id)
        persistence = share_response_voice.ResponseCapturePersistence(capture_id=capture_id, user_id=capture.user_id)
        await persistence.record_audio_frame(b'\x00\x00' * 800)
        await persistence.persist_final(StreamingASREvent(type='final', text='那棵树是祖父种的', raw={}))
        await test_db.refresh(capture)
        submitted = await aunt.post(BASE, json={'reading_token': token, 'request_id': str(uuid4()),
            'content': '那棵树是祖父种的。', 'voice_capture_id': capture_id})
        assert submitted.status_code == 200, submitted.text
        response_id = submitted.json()['id']
        await test_db.refresh(capture)
        original_audio = await test_db.get(AudioFile, capture.audio_file_id)
        original_path = audio_files.audio_file_path(original_audio)
    owner_audio_before = set((await test_db.scalars(select(AudioFile.id).where(
        AudioFile.user_id == owner_id))).all())

    enumerated = asyncio.Event()
    release_delete = asyncio.Event()
    original_candidates = project_deletion._project_audio_files

    async def paused_candidates(db, *, project_id):
        rows = await original_candidates(db, project_id=project_id)
        enumerated.set()
        await release_delete.wait()
        return rows

    monkeypatch.setattr(project_deletion, '_project_audio_files', paused_candidates)

    async def remove_project():
        async with async_session_maker() as db:
            project = await db.get(Project, pid)
            cleanups = []
            await project_deletion.delete_project(db, project, post_commit_cleanups=cleanups)
            await db.commit()
            for cleanup in cleanups:
                await cleanup()

    async def adopt():
        async with async_session_maker() as db:
            return await share_responses.adopt_response(db, response_id, owner_id)

    deleting = asyncio.create_task(remove_project())
    adopting = None
    try:
        await asyncio.wait_for(enumerated.wait(), 5)
        adopting = asyncio.create_task(adopt())
        await asyncio.sleep(0.05)
        assert not adopting.done()
        release_delete.set()
        await asyncio.wait_for(deleting, 5)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(adopting, 5)
        assert error.value.status_code == 404
        assert await test_db.scalar(select(MemoirShareResponse.id).where(
            MemoirShareResponse.id == response_id)) is None
        owner_audio_after = set((await test_db.scalars(select(AudioFile.id).where(
            AudioFile.user_id == owner_id))).all())
        assert not (owner_audio_after - owner_audio_before)
        assert not original_path.exists()
    finally:
        release_delete.set()
        for task in (deleting, adopting):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
