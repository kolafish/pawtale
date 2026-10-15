import pytest
from sqlalchemy import select

from app.models.memoir import MemoirChatMessage, MemoirCompletionIssue, MemoirSection, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.domain.memoir.types import MemoirCompletionIssueOutcome
from app.services.memoir import completion_interviews, completion_issues
from tests.memoir_helpers import post_mobile_memoir_voice_session
from tests.test_memoir_book_completion import _book
from tests.test_memoir_voice_assistant_turn_stream import _run_stream_turn


async def _opening(client, db, project_id):
    response = await post_mobile_memoir_voice_session(client, project_id=project_id, json={'guidance_mode': 'free_narration'})
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


async def test_opening_offer_is_once_only_and_does_not_bind_ordinary_narration(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr('app.services.memoir.audio_files.MEMOIR_AUDIO_DIR', tmp_path / 'audio')
    project_id, _, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id)
    user_id = (await test_db.get(Project, project_id)).user_id
    stream, ai, _, _ = await _run_stream_turn(test_db, voice_session_id=session.id, user_id=user_id,
        chunks=['欢迎回来，愿意补充可以点补充这段，也可以直接讲别的。'])
    started = next(event for event in stream.sent if event['type'] == 'turn.started')
    assert started['opening_completion_offer'] == {'issue_id': issue.id, 'question': issue.question}
    assert issue.question in ai.calls[0]['user_prompt']
    await test_db.refresh(issue)
    await test_db.refresh(session)
    assert issue.status == 'open' and session.current_completion_issue_id is None
    # Even after an offer, a direct answer/refusal/other story is ordinary narration.
    source = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw='今天不聊比赛，我想讲工厂里的师傅。')
    test_db.add(source)
    await test_db.flush()
    assert await completion_interviews.bind_answer_segment(test_db, voice_session=session, utterance=source) is None
    assert source.completion_issue_id is None and issue.status == 'open'
    later = await _opening(client, test_db, project_id)
    assert await completion_interviews.offer_for_opening(test_db, voice_session=later) is None


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


async def test_only_the_first_reply_to_an_offer_points_placement_at_the_offered_section(client, test_db):
    project_id, chapter, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id)
    await completion_interviews.offer_for_opening(test_db, voice_session=session)
    replies = [MemoirChatMessage(project_id=project_id, voice_session_id=session.id, role='user', content_text=text, turn_index=index)
               for index, text in ((1, '是我爸爸送我去的。'), (3, '后来我又去了工厂。'))]
    plain = await _opening(client, test_db, project_id)
    other = MemoirChatMessage(project_id=project_id, voice_session_id=plain.id, role='user', content_text='讲讲工厂。', turn_index=1)
    test_db.add_all([*replies, other])
    await test_db.commit()
    section_id = await test_db.scalar(select(MemoirSection.id).where(MemoirSection.chapter_id == chapter.id))
    assert await completion_interviews.offered_sections(test_db, [item.id for item in (*replies, other)]) == {replies[0].id: section_id}
    await test_db.refresh(issue)
    assert issue.status == 'open'
