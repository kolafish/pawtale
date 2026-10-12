"""Read-only offline eval: per-segment people clues and clarifying questions on real AI interviews.

Replays a project's AI interview segments in order. Each narrator segment is read once with the
interviewer line before it, the people noted so far and the questions still open, exactly as the
proposed background job would. Calls the server's default AI model; never writes to the database.

  --find-owner NAME                     accounts whose display name contains NAME (id, name, role only)
  --owner U --list-projects             U's memoir projects with AI interview counts (no narration text)
  --owner U --project-id ID --output F  replay one of U's projects, one JSON line per segment to F
U is the account's user id or email.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select, text

from app.database import async_session_maker
from app.domain.memoir.types import MemoirChatMessageRole, MemoirInterviewType
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.models.projects import Project
from app.models.users import User
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy

SYSTEM = "你帮回忆录访谈记人物线索。只记讲述者说出来的，宁可少记，不猜。"
_PRONOUNS = re.compile(r"^(我|你|您|他|她|它)(们|俩|们俩)?$|^(他们|她们|他们俩|她们俩|这个人|那个人)$")


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str = ""
    content: str = ""
    quote: str = ""


class _Person(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ref: str = "new"
    name: str = ""
    aliases: list[str] = []
    relation: str | None = None
    new_facts: list[str] = []
    quote: str = ""


class _Unclear(BaseModel):
    model_config = ConfigDict(extra="ignore")
    about: str = ""
    question: str = ""
    why: str = ""
    quote: str = ""


class _Output(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reply_kind: Literal["story", "answer", "answer_and_story", "declined", "other"] = "story"
    answer: _Answer | None = None
    people: list[_Person] = []
    resolved: list[str] = []
    unclear: list[_Unclear] = []


def build_prompt(state: dict, ai_line: str, segment: str) -> str:
    people = "\n".join(
        f"{p['ref']} {p['name']}"
        + (f"（也叫：{'、'.join(p['aliases'])}）" if p["aliases"] else "")
        + f"｜{p['relation'] or '关系未说'}｜{'；'.join(p['notes'][-3:])}"
        for p in state["people"]
    ) or "（还没有）"
    asked = "\n".join(f"{q['ref']} {q['about']}：{q['question']}" for q in state["open"]) or "（没有）"
    return f"""{SYSTEM}

已知人物（P 编号是本次请求内的引用）：
{people}

之前记下、还没问清的事（Q 编号）：
{asked}

刚才这一轮：
AI：{ai_line or "（无）"}
讲述者：{segment}

请只根据讲述者这一段，返回 JSON：
{{
  "reply_kind": "story | answer | answer_and_story | declined | other",
  "answer": {{"question": "AI 问的是什么", "content": "讲述者的回答要点", "quote": "回答部分的原话"}} 或 null,
  "people": [{{"ref": "已知人物的 P 编号，新人物填 new", "name": "书里该怎么称呼他", "aliases": ["这段里对他的其他叫法，不含代词"], "relation": "和讲述者的关系，照讲述者的说法写，没说就填 null", "new_facts": ["这段新说的关于他的事，短句"], "quote": "原话"}}],
  "resolved": ["这段已经回答，或讲述者不愿说、不记得的 Q 编号"],
  "unclear": [{{"about": "P 编号或人物/事情", "question": "下次可以问讲述者的一句口语问题", "why": "不问清楚，写书时哪里会看不懂", "quote": "原话"}}]
}}

判断规则：
- reply_kind：AI 问了具体问题、讲述者只回答了它是 answer；回答之后又讲了新的往事是 answer_and_story；没回答 AI 的问题、在讲自己的事是 story；不愿说、不记得、反问是 declined；其余 other。AI 只是说“后来呢”“接着讲”时，讲述者讲的都是 story。
- people：只记这段提到的人（不含讲述者本人和 AI）。和已知人物是同一个人才填他的 P 编号；拿不准是不是同一个人就填 new，并在 unclear 里问。relation 不要推测，比如“老表”没说是哪边的就写“表亲”。
- unclear：只记不问清楚会让这本书的读者看不懂的事：主要人物是谁、和讲述者什么关系；两个人容易搞混；一句话里“他”指谁说不清。路过的人（老师、邻居、同事等）不问名字；已经改口说清的不问；讲述者不愿说、不记得的不再问；已知人物里写清的不问；“还没问清的事”里已有的不要重复记。没有就返回空数组。
- 所有 quote 必须是讲述者原话里连续的一段。
只返回 JSON。"""


def merge(state: dict, output: _Output, segment: str) -> list[str]:
    """Apply one reading; returns what the server-side checks dropped."""
    dropped = []
    by_ref = {p["ref"]: p for p in state["people"]}
    for item in output.people:
        if item.quote and item.quote not in segment:
            dropped.append(f"quote not in segment: {item.quote}")
            continue
        aliases = [a for a in item.aliases if a and not _PRONOUNS.match(a)]
        person = by_ref.get(item.ref)
        if person is None:
            if item.ref != "new":
                dropped.append(f"unknown ref {item.ref}")
            person = {"ref": f"P{len(state['people']) + 1}", "name": item.name, "aliases": [], "relation": None, "notes": []}
            state["people"].append(person)
            by_ref[person["ref"]] = person
        for alias in [item.name, *aliases]:
            if alias and alias != person["name"] and alias not in person["aliases"]:
                person["aliases"].append(alias)
        if item.relation and not person["relation"]:
            person["relation"] = item.relation
        person["notes"] += item.new_facts
    resolved = set(output.resolved)
    state["closed"] += [q for q in state["open"] if q["ref"] in resolved]
    state["open"] = [q for q in state["open"] if q["ref"] not in resolved]
    for q in output.unclear:
        if q.quote and q.quote not in segment:
            dropped.append(f"unclear quote not in segment: {q.quote}")
            continue
        state["next_q"] += 1
        state["open"].append({"ref": f"Q{state['next_q']}", "about": q.about, "question": q.question})
    return dropped


async def find_owner(name: str) -> None:
    async with async_session_maker() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await db.execute(
            select(User.id, User.display_name, User.role, func.count(Project.id))
            .outerjoin(Project, Project.user_id == User.id)
            .where(User.display_name.ilike(f"%{name}%"))
            .group_by(User.id, User.display_name, User.role)
        )).all()
        await db.rollback()
    for row in rows:
        print(json.dumps({"user_id": row[0], "display_name": row[1], "role": row[2], "projects": row[3]}, ensure_ascii=False))


def _is_owner(owner: str):
    return or_(User.id == owner, User.email == owner)


async def list_projects(owner: str) -> None:
    async with async_session_maker() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        user_messages = (
            select(MemoirChatMessage.project_id, func.count().label("segments"),
                   func.count(func.distinct(MemoirChatMessage.voice_session_id)).label("sessions"))
            .join(MemoirVoiceSession, MemoirVoiceSession.id == MemoirChatMessage.voice_session_id)
            .where(
                MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
                MemoirChatMessage.content_text != "",
                MemoirVoiceSession.interview_type == MemoirInterviewType.AI_INTERVIEW.value,
            )
            .group_by(MemoirChatMessage.project_id)
            .subquery()
        )
        rows = (await db.execute(
            select(Project.id, Project.title, User.display_name, user_messages.c.sessions, user_messages.c.segments)
            .join(user_messages, user_messages.c.project_id == Project.id)
            .join(User, User.id == Project.user_id)
            .where(_is_owner(owner))
            .order_by(user_messages.c.segments.desc())
        )).all()
        await db.rollback()
    for row in rows:
        print(json.dumps({"project_id": row[0], "title": row[1], "owner": row[2], "ai_sessions": row[3], "segments": row[4]}, ensure_ascii=False))


async def replay(project_id: str, owner: str, output_path: Path, max_segments: int) -> None:
    async with async_session_maker() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        owned = await db.scalar(
            select(Project.id).join(User, User.id == Project.user_id)
            .where(Project.id == project_id, _is_owner(owner))
        )
        if owned is None:
            await db.rollback()
            raise SystemExit(f"project {project_id} is not owned by {owner}")
        rows = (await db.execute(
            select(MemoirChatMessage.voice_session_id, MemoirChatMessage.turn_index, MemoirChatMessage.role,
                   MemoirChatMessage.content_text)
            .join(MemoirVoiceSession, MemoirVoiceSession.id == MemoirChatMessage.voice_session_id)
            .where(
                MemoirChatMessage.project_id == project_id,
                MemoirVoiceSession.interview_type == MemoirInterviewType.AI_INTERVIEW.value,
                MemoirChatMessage.content_text != "",
            )
            .order_by(MemoirVoiceSession.created_at, MemoirChatMessage.turn_index)
        )).all()
        await db.rollback()
    client = get_ai_service(None)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    state = {"people": [], "open": [], "closed": [], "next_q": 0}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last_ai: dict[str, str] = {}
    count = 0
    with output_path.open("w") as out:
        for session_id, turn_index, role, content in rows:
            if role == MemoirChatMessageRole.ASSISTANT.value:
                last_ai[session_id] = content.strip()
                continue
            if role != MemoirChatMessageRole.USER.value or count >= max_segments:
                continue
            count += 1
            segment = content.strip()
            prompt = build_prompt(state, last_ai.get(session_id, ""), segment)
            started = time.monotonic()
            row = {"session_id": session_id, "turn_index": turn_index, "ai": last_ai.get(session_id, ""),
                   "user": segment, "prompt_chars": len(prompt)}
            try:
                result = await generate_structured_with_client(
                    client, SYSTEM, prompt, output_model=_Output, requested_model=None, temperature=0.1,
                    max_tokens=policy.max_tokens, timeout=ai_text_timeout_seconds(policy.timeout_profile),
                    max_attempts=policy.max_attempts,
                )
                row["output"] = result.value.model_dump()
                row["dropped"] = merge(state, result.value, segment)
                usage = result.response.normalized_usage
                row["usage"] = usage.__dict__ if usage is not None and hasattr(usage, "__dict__") else str(usage)
                row["model"] = result.response.response_model or result.response.resolved_model
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            row["elapsed_seconds"] = round(time.monotonic() - started, 2)
            row["roster"] = [f"{p['ref']} {p['name']}{p['aliases'] or ''}={p['relation']}" for p in state["people"]]
            row["open"] = [f"{q['ref']} {q['about']}：{q['question']}" for q in state["open"]]
            out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            out.flush()
            print(f"segment {count}: {row.get('output', {}).get('reply_kind', row.get('error'))} "
                  f"{row['elapsed_seconds']}s open={len(state['open'])} people={len(state['people'])}", flush=True)
    (output_path.with_suffix(".state.json")).write_text(json.dumps(state, ensure_ascii=False, indent=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", help="user id or email; only this account's projects are read")
    parser.add_argument("--find-owner")
    parser.add_argument("--list-projects", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-segments", type=int, default=80)
    args = parser.parse_args()
    if args.find_owner:
        asyncio.run(find_owner(args.find_owner))
        return 0
    if not args.owner:
        parser.error("--owner is required")
    if args.list_projects:
        asyncio.run(list_projects(args.owner))
        return 0
    if not args.project_id or not args.output:
        parser.error("--project-id and --output are required unless --list-projects")
    asyncio.run(replay(args.project_id, args.owner, args.output, args.max_segments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
