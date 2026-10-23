import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.memoir import MemoirChatMessage, MemoirCompletionIssue, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.domain.memoir.types import MemoirCompletionIssueOutcome
from app.services.memoir import completion_interviews, completion_issues
from tests.memoir_helpers import create_test_book_plan_with_chapters, post_mobile_memoir_voice_session
from tests.test_memoir_book_completion import _book
from app.services.memoir.voice_assistant_turn_stream import VoiceAssistantTurnStreamSession
from tests.test_memoir_voice_assistant_turn_stream import (
    FakeStreamingAI,
    FakeStreamingTTS,
    FakeTurnClient,
    _run_stream_turn,
    _wait_for_event,
)


async def _opening(client, db, project_id, *, entry=None):
    response = await post_mobile_memoir_voice_session(client, project_id=project_id, json={
        'guidance_mode': 'free_narration', 'metadata_json': {'entry': entry} if entry else {},
    })
    assert response.status_code == 201
    return await db.get(MemoirVoiceSession, response.json()['session']['id'])


async def _issue(client, db):
    project_id, chapters, _ = await _book(client, db)
    issue = MemoirCompletionIssue(project_id=project_id, scope='chapter', chapter_id=chapters[0].id,
        channel='interview', issue_type='context', question='那次比赛是谁送你去的？', reason='补充人物背景',
        priority='medium', fingerprint='a'*64, input_signature='current', related_material_ids_json=[])
    db.add(issue)
    await db.commit()
    return project_id, chapters[0], issue


async def test_home_opening_does_not_offer_supplement_until_narrator_has_no_direction(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr('app.services.memoir.audio_files.MEMOIR_AUDIO_DIR', tmp_path / 'audio')
    project_id, _, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    user_id = (await test_db.get(Project, project_id)).user_id
    stream, ai, _, _ = await _run_stream_turn(test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['欢迎回来，您接着讲就好。'])
    started = next(event for event in stream.sent if event['type'] == 'turn.started')
    assert 'opening_completion_offer' not in started
    assert issue.question not in ai.calls[0]['user_prompt']
    await test_db.refresh(issue)
    await test_db.refresh(session)
    assert issue.status == 'open' and session.current_completion_issue_id is None
    assert completion_interviews.OPENING_OFFER_KEY not in session.metadata_json

    no_direction = _line(project_id, session.id, 'user', '我不知道讲什么了。', 1)
    test_db.add(no_direction)
    await test_db.flush()
    await test_db.commit()
    stream, ai, _, _ = await _run_stream_turn(test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['您提到的工作经历，可以补充说说这件事对您后来工作的影响吗？'])
    started = next(event for event in stream.sent if event['type'] == 'turn.started')
    assert 'opening_completion_offer' not in started
    assert issue.question in ai.calls[0]['user_prompt']
    assert '按钮' not in ai.calls[0]['user_prompt']

    await test_db.refresh(session)
    assert session.metadata_json[completion_interviews.OPENING_OFFER_KEY] == issue.id
    assert session.metadata_json[completion_interviews.OPENING_OFFER_STATUS_KEY] == completion_interviews.OPENING_OFFER_CONSUMED

    decline = _line(project_id, session.id, 'user', '这个我不想补充，还是讲别的吧。', 3)
    test_db.add(decline)
    await test_db.flush()
    await test_db.commit()
    await _run_stream_turn(test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['好，您想讲什么就讲什么。'])
    await test_db.refresh(session)
    assert session.metadata_json[completion_interviews.OPENING_OFFER_DECLINED_KEY] is True

    repeat = _line(project_id, session.id, 'user', '我不知道讲什么了。', 5)
    test_db.add(repeat)
    await test_db.commit()
    _, ai, _, _ = await _run_stream_turn(test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['您接着讲就好。'])
    assert issue.question not in ai.calls[0]['user_prompt']


async def test_pending_opening_offer_can_be_cleared_and_retried(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    trigger = _line(project_id, session.id, 'user', '我不知道讲什么了。', 1)
    test_db.add(trigger)
    await test_db.commit()

    first = await completion_interviews.offer_for_opening(test_db, voice_session=session)
    assert first is not None and first.issue_id == issue.id
    assert session.metadata_json[completion_interviews.OPENING_OFFER_STATUS_KEY] == completion_interviews.OPENING_OFFER_PENDING
    assert completion_interviews.OPENING_OFFER_PENDING_AT_KEY in session.metadata_json
    await test_db.commit()

    assert await completion_interviews.clear_pending_opening_offer(test_db, voice_session=session) is True
    await test_db.commit()
    await test_db.refresh(session)
    assert completion_interviews.OPENING_OFFER_KEY not in session.metadata_json

    retried = await completion_interviews.offer_for_opening(test_db, voice_session=session)
    assert retried is not None and retried.issue_id == issue.id


async def test_stopping_opening_session_releases_pending_offer(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    offer = await completion_interviews.offer_for_opening(test_db, voice_session=session)
    assert offer is not None and offer.issue_id == issue.id
    await test_db.commit()

    stopped = await client.post(f'/api/mobile/memoir/voice-sessions/{session.id}/stop', json={})
    assert stopped.status_code == 200, stopped.text
    await test_db.refresh(session)
    assert completion_interviews.OPENING_OFFER_KEY not in session.metadata_json


async def test_expired_pending_opening_offer_can_be_claimed_by_another_session(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    first = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    offer = await completion_interviews.offer_for_opening(test_db, voice_session=first)
    assert offer is not None and offer.issue_id == issue.id
    first.metadata_json = {
        **first.metadata_json,
        completion_interviews.OPENING_OFFER_PENDING_AT_KEY: (
            datetime.now(UTC) - timedelta(minutes=16)
        ).isoformat(),
    }
    await test_db.commit()

    second = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    retry = await completion_interviews.offer_for_opening(test_db, voice_session=second)
    assert retry is not None and retry.issue_id == issue.id


async def test_pending_opening_offer_is_cleared_when_issue_is_no_longer_valid(client, test_db):
    project_id, chapter, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    session.metadata_json = {
        **(session.metadata_json or {}),
        completion_interviews.OPENING_OFFER_KEY: issue.id,
        completion_interviews.OPENING_OFFER_STATUS_KEY: completion_interviews.OPENING_OFFER_PENDING,
    }
    chapter.include_in_book = False
    await test_db.commit()

    assert await completion_interviews.offer_for_opening(test_db, voice_session=session) is None
    await test_db.refresh(session)
    assert completion_interviews.OPENING_OFFER_KEY not in session.metadata_json


async def test_pending_opening_offer_cannot_reference_another_project(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    owner_id = (await test_db.get(Project, project_id)).user_id
    foreign_project = Project(user_id=owner_id, type='memoir', title='另一本回忆录')
    test_db.add(foreign_project)
    await test_db.flush()
    foreign_chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=foreign_project.id,
        chapter_titles=['另一本回忆录的章节'],
    )
    foreign_issue = MemoirCompletionIssue(
        project_id=foreign_project.id,
        scope='chapter',
        chapter_id=foreign_chapters[1][0].id,
        channel='interview',
        issue_type='context',
        question='另一个项目的问题？',
        reason='补充人物背景',
        priority='medium',
        fingerprint='b'*64,
        input_signature='current',
        related_material_ids_json=[],
    )
    test_db.add(foreign_issue)
    await test_db.flush()
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    session.metadata_json = {
        **(session.metadata_json or {}),
        completion_interviews.OPENING_OFFER_KEY: foreign_issue.id,
        completion_interviews.OPENING_OFFER_STATUS_KEY: completion_interviews.OPENING_OFFER_PENDING,
    }
    await test_db.commit()

    offer = await completion_interviews.offer_for_opening(test_db, voice_session=session)
    assert offer is not None and offer.issue_id == issue.id


async def test_pending_opening_offer_is_reserved_across_sessions(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    first = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    second = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')

    offer = await completion_interviews.offer_for_opening(test_db, voice_session=first)
    assert offer is not None and offer.issue_id == issue.id
    await test_db.commit()

    assert await completion_interviews.offer_for_opening(test_db, voice_session=second) is None


async def test_failed_opening_offer_is_retryable(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry='miniprogram_home_resume')
    user_id = (await test_db.get(Project, project_id)).user_id
    test_db.add(_line(project_id, session.id, 'user', '我不知道讲什么了。', 1))
    await test_db.commit()

    class FailingAI(FakeStreamingAI):
        async def stream_llm(self, system_prompt, user_prompt, **kwargs):
            if False:
                yield None
            raise RuntimeError('stream failed')

    stream_client = FakeTurnClient()
    runner = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=session.id,
        user_id=user_id,
        ai_client_factory=lambda model=None: FailingAI([]),
        tts_client_factory=FakeStreamingTTS,
    )
    run_task = asyncio.create_task(runner.run())
    await _wait_for_event(stream_client, 'turn.ready')
    await stream_client.push({'type': 'turn.start'})
    await _wait_for_event(stream_client, 'turn.failed')
    await asyncio.wait_for(run_task, 1)

    await test_db.refresh(session)
    assert completion_interviews.OPENING_OFFER_KEY not in session.metadata_json
    retry = await completion_interviews.offer_for_opening(test_db, voice_session=session)
    assert retry is not None and retry.issue_id == issue.id


@pytest.mark.parametrize('entry', ['miniprogram_home_resume', 'miniprogram_home', 'miniprogram_home_launch'])
@pytest.mark.parametrize('previous_offer', [False, True])
async def test_all_home_openings_resume_the_book_without_offering_supplement(
    client, test_db, tmp_path, monkeypatch, entry, previous_offer,
):
    monkeypatch.setattr('app.services.memoir.audio_files.MEMOIR_AUDIO_DIR', tmp_path / 'audio')
    project_id, chapter, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id, entry=entry)
    if previous_offer:
        session.metadata_json = {**session.metadata_json, completion_interviews.OPENING_OFFER_KEY: issue.id}
        await test_db.commit()
    before = dict(session.metadata_json)

    async def unexpected_offer(*args, **kwargs):
        raise AssertionError('A home opening must not select an old completion question')

    monkeypatch.setattr(completion_interviews, 'offer_for_opening', unexpected_offer)
    user_id = (await test_db.get(Project, project_id)).user_id
    stream, ai, _, completed = await _run_stream_turn(
        test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['欢迎回来，您接着讲就好。'],
    )
    started = next(event for event in stream.sent if event['type'] == 'turn.started')
    assert 'opening_completion_offer' not in started
    assert issue.question not in ai.calls[0]['user_prompt']
    assert chapter.title in ai.calls[0]['user_prompt']
    assert '给两个具体方向让他选' in ai.calls[0]['user_prompt']
    assert '讲个新故事' not in ai.calls[0]['user_prompt']
    assert completed['primary_intent'] == 'opening'
    await test_db.refresh(session)
    assert session.metadata_json.get(completion_interviews.OPENING_OFFER_KEY) == before.get(completion_interviews.OPENING_OFFER_KEY)
    assert session.current_completion_issue_id is None
    text = '今天想讲工厂里带我的那位师傅。'
    reply = _line(project_id, session.id, 'user', text, 1)
    test_db.add(reply)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(voice_session_id=session.id, chat_message_id=reply.id, transcript_raw=text)
    test_db.add(utterance)
    await test_db.commit()
    _, ai, _, completed = await _run_stream_turn(
        test_db, voice_session_id=session.id, user_id=user_id, chunks=['师傅当时是怎么带您的？'],
    )
    assert completed['primary_intent'] == 'follow_up'
    assert text in ai.calls[0]['user_prompt']
    assert await completion_interviews.bind_answer_segment(test_db, voice_session=session, utterance=utterance) is None
    await test_db.refresh(issue)
    assert issue.status == 'open' and utterance.completion_issue_id is None



@pytest.mark.parametrize('ineligible', ['closed', 'in_progress', 'manual_review', 'excluded'])
async def test_opening_never_offers_unavailable_or_confirmation_only_questions(client, test_db, ineligible):
    project_id, chapter, issue = await _issue(client, test_db)
    if ineligible == 'closed':
        await completion_issues.close_issue(test_db, project_id=project_id, issue_id=issue.id, outcome=MemoirCompletionIssueOutcome.DECLINED, note="", answered_material_id=None)
    elif ineligible == 'in_progress':
        issue.status = ineligible
    elif ineligible == 'manual_review':
        issue.channel, issue.issue_type = 'manual_review', 'conflict'
    else:
        chapter.include_in_book = False
    await test_db.commit()
    session = await _opening(client, test_db, project_id)
    assert await completion_interviews.offer_for_opening(test_db, voice_session=session) is None


async def test_explicit_accept_creates_a_separate_existing_completion_session(client, test_db):
    project_id, _, issue = await _issue(client, test_db)
    original = await _opening(client, test_db, project_id)
    await completion_interviews.offer_for_opening(test_db, voice_session=original)
    await test_db.commit()
    response = await post_mobile_memoir_voice_session(client, project_id=project_id, json={
        'guidance_mode': 'guided_interview', 'session_goal': 'book_completion',
        'current_completion_issue_id': issue.id,
        'metadata_json': {'entry': 'miniprogram_home'},
    })
    assert response.status_code == 201, response.text
    bound = await test_db.get(MemoirVoiceSession, response.json()['session']['id'])
    assert bound.id != original.id and bound.current_completion_issue_id == issue.id
    assert original.session_goal == 'open_narration' and original.current_completion_issue_id is None
    await test_db.refresh(issue)
    assert issue.status == 'in_progress'
    answer = MemoirVoiceUtterance(voice_session_id=bound.id, transcript_raw='是张老师带我去的。')
    test_db.add(answer)
    await test_db.flush()
    await completion_interviews.bind_answer_segment(test_db, voice_session=bound, utterance=answer)
    assert issue.status == 'closed' and answer.completion_issue_id == issue.id
    assert len((await test_db.scalars(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == project_id))).all()) >= 2



def _line(project_id, session_id, role, text, turn_index, *, parent=None, intent=None):
    return MemoirChatMessage(project_id=project_id, voice_session_id=session_id, role=role, content_text=text,
                             turn_index=turn_index, parent_message_id=parent.id if parent else None, guidance_intent=intent)


@pytest.mark.parametrize('follow_up_intent', ['follow_up', None])
async def test_what_is_said_before_the_first_follow_up_answers_the_offer_and_deletion_never_moves_it(client, test_db, follow_up_intent):
    project_id, chapter, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id)
    await completion_interviews.offer_for_opening(test_db, voice_session=session)
    # The opening was interrupted (never saved); a no-answer reminder came before the reply.
    reminder = _line(project_id, session.id, 'assistant', '您可以接着刚才的问题慢慢说。', 0, intent='no_answer_prompt')
    reply = _line(project_id, session.id, 'user', '是我爸爸送我去的。', 1, parent=reminder)
    follow_up = _line(project_id, session.id, 'assistant', '后来呢？', 2, intent=follow_up_intent)
    later = _line(project_id, session.id, 'user', '后来我又去了工厂。', 3, parent=follow_up)
    recovered = _line(project_id, session.id, 'user', '补转写出来的另一段。', 4)
    plain = await _opening(client, test_db, project_id)
    other = _line(project_id, plain.id, 'user', '讲讲工厂。', 1)
    for message in (reminder, reply, follow_up, later, recovered, other):
        test_db.add(message)
        await test_db.flush()
    await test_db.commit()
    ids = [reply.id, later.id, recovered.id, other.id]
    assert await completion_interviews.offered_chapters(test_db, ids) == {reply.id: chapter.id}
    # Deleting a reply that already has a follow-up clears it in place (see delete_voice_transcript).
    reply.content_text = ''
    await test_db.commit()
    assert await completion_interviews.offered_chapters(test_db, ids) == {}
    await test_db.refresh(issue)
    assert issue.status == 'open'


async def test_a_book_question_spanning_chapters_gives_no_placement_mark(client, test_db):
    project_id, chapters, _ = await _book(client, test_db, chapter_count=2)
    issue = MemoirCompletionIssue(project_id=project_id, scope='book', channel='interview', issue_type='context',
        question='这两次比赛隔了几年？', reason='补充时间', priority='medium', fingerprint='b'*64, input_signature='current',
        related_material_ids_json=[], related_chapter_ids_json=[item.id for item in chapters])
    test_db.add(issue)
    await test_db.commit()
    session = await _opening(client, test_db, project_id)
    session.metadata_json = {**(session.metadata_json or {}), completion_interviews.OPENING_OFFER_KEY: issue.id}
    opening = _line(project_id, session.id, 'assistant', '愿意补充可以点补充这段。', 0, intent='opening')
    test_db.add(opening)
    await test_db.flush()
    reply = _line(project_id, session.id, 'user', '隔了两年。', 1, parent=opening)
    test_db.add(reply)
    await test_db.commit()
    assert await completion_interviews.offered_chapters(test_db, [reply.id]) == {}
