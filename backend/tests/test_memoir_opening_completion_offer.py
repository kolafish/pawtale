import pytest
from sqlalchemy import select

from app.models.memoir import MemoirChatMessage, MemoirCompletionIssue, MemoirVoiceSession, MemoirVoiceUtterance
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



def _line(project_id, session_id, role, text, turn_index, *, parent=None, intent=None):
    return MemoirChatMessage(project_id=project_id, voice_session_id=session_id, role=role, content_text=text,
                             turn_index=turn_index, parent_message_id=parent.id if parent else None, guidance_intent=intent)


async def test_replies_to_the_offer_line_point_placement_at_its_chapter_and_deletion_never_moves_it(client, test_db):
    project_id, chapter, issue = await _issue(client, test_db)
    session = await _opening(client, test_db, project_id)
    await completion_interviews.offer_for_opening(test_db, voice_session=session)
    opening = _line(project_id, session.id, 'assistant', '愿意补充可以点补充这段。', 0, intent='opening')
    test_db.add(opening)
    await test_db.flush()
    reply = _line(project_id, session.id, 'user', '是我爸爸送我去的。', 1, parent=opening)
    test_db.add(reply)
    await test_db.flush()
    follow_up = _line(project_id, session.id, 'assistant', '后来呢？', 2, parent=reply, intent='follow_up')
    test_db.add(follow_up)
    await test_db.flush()
    later = _line(project_id, session.id, 'user', '后来我又去了工厂。', 3, parent=follow_up)
    plain = await _opening(client, test_db, project_id)
    plain_opening = _line(project_id, plain.id, 'assistant', '想从哪儿讲起？', 0, intent='opening')
    test_db.add_all([later, plain_opening])
    await test_db.flush()
    other = _line(project_id, plain.id, 'user', '讲讲工厂。', 1, parent=plain_opening)
    test_db.add(other)
    await test_db.commit()
    ids = [reply.id, later.id, other.id]
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
