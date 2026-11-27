"""Opt-in integration case. Run through scripts/test-memoir-print-e2e.sh."""
import asyncio
import base64
import json
import wave
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from sqlalchemy import select

from app.auth import SESSION_COOKIE_NAME
from app.domain.memoir.types import MemoirCreatedBy, MemoirEditingJobType
from app.main import app
from app.models.memoir import MemoirEditingJob, MemoirSection
from app.routers.memoir import voice_gateway
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir import (
    audio_files,
    book_outputs,
    editing_jobs,
    voice_transcript_correction,
)
from app.services.memoir.book_manuscript_ai import GeneratedChapterIntro, GeneratedManuscriptCopy
from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers
from app.services.memoir.voice_authorization import CURRENT_VOICE_AUTHORIZATION_VERSION
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.manual.memoir_print_fixture import (
    BOOK_TITLE,
    FALSE_QUESTION,
    NEW_FACT,
    OLD_FACT,
    PRIVATE_SECTION,
    EditorialReplay,
    corrected,
    paragraphs,
    read_interviews,
)
from tests.test_memoir_voice_gateway import FakeStreamingASRClient


MOBILE = "/api/mobile/memoir"
CASE_JOBS = (
    MemoirEditingJobType.CORRECT_TRANSCRIPT,
    MemoirEditingJobType.ORGANIZE_MATERIAL,
    MemoirEditingJobType.UPDATE_BOOK_DRAFT,
    MemoirEditingJobType.PREPARE_CHAPTER,
    MemoirEditingJobType.GENERATE_BOOK_EDITORIAL_COPY,
    MemoirEditingJobType.GENERATE_BOOK_REVIEW,
)
PCM = base64.b64encode(bytes(3200)).decode("ascii")


class ReplayASR(FakeStreamingASRClient):
    def __init__(self, text):
        super().__init__()
        self.text = text

    async def send_audio(self, audio):
        self.sent_audio.append(audio)

    async def finish(self):
        assert self.sent_audio, "ASR replay received no PCM"
        await self.events_queue.put(StreamingASREvent(type="final", text=self.text, start_ms=0, end_ms=100))
        await self.events_queue.put(None)


@asynccontextmanager
async def open_gateway(session_id, token):
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    path = f"{MOBILE}/voice-sessions/{session_id}/gateway"
    scope = {
        "type": "websocket", "scheme": "ws", "path": path, "root_path": "", "query_string": b"",
        "headers": [(b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode())],
        "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
        "client": ("127.0.0.1", 40000), "server": ("test", 80), "subprotocols": [],
    }
    await incoming.put({"type": "websocket.connect"})
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))

    async def send(kind, **values):
        await incoming.put({"type": "websocket.receive", "text": json.dumps({"type": kind, **values})})

    async def expect(kind):
        async with asyncio.timeout(10):
            while True:
                event = await outgoing.get()
                if event["type"] == "websocket.accept":
                    continue
                assert event["type"] == "websocket.send", f"{kind}: gateway closed: {event}"
                payload = json.loads(event["text"])
                assert payload["type"] != "session.error", payload
                if payload["type"] == kind:
                    return payload

    try:
        yield send, expect
        await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def json_response(client, method, url, *, status=200, **kwargs):
    response = await client.request(method, url, **kwargs)
    assert response.status_code == status, f"{method} {url}: {response.status_code} {response.text}"
    return response.json()


async def record_interview(client, project_id, turns, asr_queue, *, question=None):
    base = f"{MOBILE}/voice-sessions"
    created = await json_response(
        client, "POST", base, params={"project_id": project_id}, status=201,
        json={"interview_type": "human_interview", "guidance_mode": "free_narration"},
    )
    session_id = created["session"]["id"]
    events = []
    with_question = [("interviewer", question)] if question else []
    script = with_question + [("narrator", turn.text) for turn in turns]
    parent = None
    async with open_gateway(session_id, client.cookies.get(SESSION_COOKIE_NAME)) as (send, expect):
        await expect("session.ready")
        for role, text in script:
            asr_queue.append(text)
            await send("session.start", speaker_role=role, parent_message_id=parent)
            await expect("session.started")
            await send("audio.frame", audio=PCM)
            await send("audio.end")
            final = await expect("asr.final")
            assert final["text"] == text, f"{session_id}: transcript differs for {text[:20]}"
            assert final["utterance_id"] and final["transcript_version_id"] and final["audio_file_id"]
            await expect("audio.ended")
            parent = final["message_id"] if role == "interviewer" else None
            if role == "narrator":
                events.append(final)
        await send("session.stop")
        assert (await expect("session.stopped"))["status"] == "stopped"
    assert not asr_queue
    saved = await json_response(client, "GET", f"{base}/{session_id}")
    assert saved["session"]["status"] == "stopped"
    assert [item["transcript_raw"] for item in saved["utterances"]] == [text for _, text in script]
    assert [event["utterance_id"] for event in events] == [
        item["id"] for item, (role, _) in zip(saved["utterances"], script, strict=True) if role == "narrator"
    ]
    playback = await client.get(f"{MOBILE}/audio-files/{events[0]['audio_file_id']}/playback")
    assert playback.status_code == 200 and playback.headers["content-type"] == "audio/wav"
    with wave.open(BytesIO(playback.content)) as audio:
        assert audio.readframes(audio.getnframes()) == base64.b64decode(PCM)
    return session_id, events


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def settle(test_db, project_id, artifacts):
    # Run production leases/handlers for the narration-to-book queue. Search
    # embeddings, audio playback and social video have their own test paths.
    async with asyncio.timeout(75):
        while True:
            for kind in CASE_JOBS:
                await editing_jobs.run_due_editing_jobs(job_type=kind, project_id=project_id)
            jobs = (await test_db.scalars(select(MemoirEditingJob).where(
                MemoirEditingJob.project_id == project_id,
                MemoirEditingJob.job_type.in_([job.value for job in CASE_JOBS]),
            ).execution_options(populate_existing=True))).all()
            state = [{"type": job.job_type, "status": job.status, "error": job.error_message,
                      "details": job.result_json.get("failure")} for job in jobs]
            await test_db.commit()
            write_json(artifacts / "jobs.json", state)
            failures = [job for job in state if job["status"] == "failed"]
            assert not failures, failures
            if not any(job["status"] in editing_jobs.ACTIVE_EDITING_JOB_STATUSES for job in state):
                return
            await asyncio.sleep(0.2)


async def hide_section(client, test_db, project_id):
    section = await test_db.scalar(select(MemoirSection).where(
        MemoirSection.project_id == project_id, MemoirSection.title == PRIVATE_SECTION,
    ))
    assert section is not None
    await test_db.commit()
    await json_response(
        client, "PATCH", f"/api/memoir/projects/{project_id}/sections/{section.id}",
        json={"expected_version": section.version, "include_in_book": False},
    )


async def export_book(client, project_id, artifacts, name):
    url = f"{MOBILE}/projects/{project_id}/book-output"
    state = await json_response(client, "GET", url)
    latest = state["latest_version"]
    assert latest and not state["is_stale"], state["stale_chapter_titles"]
    params = {"layout": "large_print", "format": "pdf", "version_id": latest["id"], "show_source_appendix": "false"}
    info = await json_response(client, "GET", f"{url}/export/info", params=params)
    assert info["version_id"] == latest["id"]
    pdf = await client.get(f"{url}/export", params={**params, "cache_key": info["cache_key"]})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-") and "filename*=" in pdf.headers["content-disposition"]
    (artifacts / f"{name}.pdf").write_bytes(pdf.content)
    write_json(artifacts / f"{name}.json", latest)
    return latest


def check_manuscript(book, sources, *, revised):
    expected = defaultdict(lambda: defaultdict(list))
    for turn, session_id, event in sources:
        if turn.section != PRIVATE_SECTION:
            expected[turn.chapter][turn.section].append((turn, session_id, event["utterance_id"]))
    chapters = book["manuscript"]["chapters"]
    assert [chapter["title"] for chapter in chapters] == list(expected)
    char_count = 0
    for chapter in chapters:
        sections = expected[chapter["title"]]
        assert [section["title"] for section in chapter["sections"]] == list(sections), chapter["title"]
        expected_refs = {
            (utterance_id, session_id) for rows in sections.values() for _, session_id, utterance_id in rows
        }
        refs = chapter["source_refs"]
        actual_refs = {(ref["utterance_id"], ref["voice_session_id"]) for ref in refs}
        assert len(refs) == len(actual_refs) and actual_refs == expected_refs, chapter["title"]
        assert all(ref["transcript_version_id"] for ref in refs)
        for section in chapter["sections"]:
            title = section["title"]
            assert set(section["source_ref_ids"]) == {utterance_id for _, _, utterance_id in sections[title]}, title
            expected_paragraphs = paragraphs([turn for turn, _, _ in sections[title]])
            actual = [line.strip() for line in section["content"].splitlines() if line.strip()]
            assert len(actual) == len(expected_paragraphs), f"{chapter['title']} / {title}: paragraph count"
            for number, (text, expected_text) in enumerate(zip(actual, expected_paragraphs, strict=True), 1):
                assert text == expected_text, f"{chapter['title']} / {title} / paragraph {number}"
                char_count += sum(char.isalnum() for char in text)
    body = "\n".join(chapter["content"] for chapter in chapters)
    assert "南京" in body and "武汉" not in body
    assert "我仍记不清那是进厂后的第二年还是第三年" in body
    assert "紫铜匣钥匙" not in body
    assert body.count("母亲在台阶上画了一道粉笔线") == 1
    expected_fact, obsolete_fact = (NEW_FACT, OLD_FACT) if revised else (OLD_FACT, NEW_FACT)
    assert expected_fact in body and obsolete_fact not in body
    assert char_count >= 6000
    return char_count


async def test_memoir_print_journey(client, test_db, tmp_path, request, monkeypatch):
    assert request.config.option.basetemp, "Run this case through scripts/test-memoir-print-e2e.sh"
    artifacts = Path(request.config.option.basetemp).resolve() / "artifacts"
    artifacts.mkdir()
    sessions = read_interviews()
    raw_count = sum(sum(char.isalnum() for char in turn.text) for session in sessions for turn in session)
    assert len(sessions) == 8 and 36 <= sum(map(len, sessions)) <= 40 and raw_count >= 9000

    replay = EditorialReplay(sessions)
    for name in ("book_plan_ai", "story_tree_growth", "section_regeneration", "chapter_generation",
                 "chapter_digests", "section_digests", "chapter_structure", "book_reviews"):
        monkeypatch.setattr(f"app.services.memoir.{name}.get_ai_service", lambda *_: replay)

    async def clean(text, **_kwargs):
        return CleanedTranscriptText(content=text, provider="test", created_by=MemoirCreatedBy.AI)

    async def copy(*, snapshot, **_kwargs):
        return GeneratedManuscriptCopy(
            book_title=BOOK_TITLE, cover_subtitle="童年、进城与归来", preface="这些往事从河岸的老房子说起。",
            afterword="有些往事还可以在下一回见面时谈清楚。",
            chapter_intros=[
                GeneratedChapterIntro(
                    chapter_id=chapter["chapter_id"], intro=f"这一章记下{chapter['chapter_title']}的往事。",
                    valid_empty_quote=True,
                ) for chapter in snapshot
            ],
            back_cover_quote="", valid_empty_back_cover_quote=True, model=replay.model_name, prompt="",
        )

    asr_queue = deque()
    monkeypatch.setattr(voice_gateway, "create_realtime_volc_streaming_asr_client", lambda *_: ReplayASR(asr_queue.popleft()))
    monkeypatch.setattr(voice_transcript_correction, "generate_cleaned_transcript_text", clean)
    monkeypatch.setattr(book_outputs, "generate_manuscript_editorial_copy", copy)
    monkeypatch.setattr(audio_files, "MEMOIR_AUDIO_DIR", tmp_path / "audio")
    register_memoir_editing_job_handlers()

    await json_response(client, "POST", f"{MOBILE}/voice-authorization-consent", status=201,
                        json={"version": CURRENT_VOICE_AUTHORIZATION_VERSION, "agreed": True})
    project = await json_response(client, "POST", f"{MOBILE}/projects", status=201, json={"title": BOOK_TITLE})
    project_id = project["id"]
    sources = []
    baseline = None
    for index, turns in enumerate(sessions, 1):
        print(f"Interview {index}/8: {len(turns)} narration turns", flush=True)
        session_id, events = await record_interview(
            client, project_id, turns, asr_queue, question=FALSE_QUESTION if index == 4 else None,
        )
        sources.extend((turn, session_id, event) for turn, event in zip(turns, events, strict=True))
        if index == 8:
            original, voice_id, event = sources[0]
            edited = corrected(original)
            changed = await json_response(
                client, "PATCH", f"{MOBILE}/voice-transcripts/{event['utterance_id']}",
                json={"transcript_edited": edited.text},
            )
            assert changed["transcript_raw"] == original.text and changed["transcript_edited"] == edited.text
            revised_transcript_id = changed["transcript_version_id"]
            assert revised_transcript_id and revised_transcript_id != event["transcript_version_id"]
            sources[0] = (edited, voice_id, event)
        write_json(artifacts / "sources.json", [
            {"chapter": item.chapter, "section": item.section, "text": item.text, "raw_text": event["text"],
             "session_id": voice_id, "utterance_id": event["utterance_id"]}
            for item, voice_id, event in sources
        ])
        await settle(test_db, project_id, artifacts)
        if index == 7:
            await hide_section(client, test_db, project_id)
            await settle(test_db, project_id, artifacts)
            baseline = await export_book(client, project_id, artifacts, "before-correction")
            before_count = check_manuscript(baseline, sources, revised=False)
    final = await export_book(client, project_id, artifacts, "final")
    final_count = check_manuscript(final, sources, revised=True)
    assert final["id"] != baseline["id"] and final["version"] > baseline["version"]
    historical = await json_response(
        client, "GET", f"{MOBILE}/projects/{project_id}/book-output/versions/{baseline['id']}",
    )
    assert historical == baseline
    first_source = sources[0][2]["utterance_id"]
    first_refs = {
        ref["utterance_id"]: ref["transcript_version_id"] for ref in final["manuscript"]["chapters"][0]["source_refs"]
    }
    assert first_refs[first_source] == revised_transcript_id
    assert replay.saw_false_question and replay.saw_correction
    write_json(artifacts / "journey.json", {
        "interviews": len(sessions), "narration_turns": len(sources), "narration_characters": raw_count,
        "before_characters": before_count, "final_characters": final_count,
        "before_version": baseline["version"], "final_version": final["version"],
    })
