"""Offline test of the people-identity redesign: self-contained readings, ids kept only by the model naming them,
and a code-written question 小余 says as is. Uses the branch's narration_people and interviewer prompt.

No DB access. Run on ECS from /tmp/narration-metadata with the branch's backend (calls DeepSeek, a few jiao):
  PYTHONPATH=/opt/pawtale/backend/.pythondeps:/tmp/narration-metadata/backend python3 eval_identity.py export.json \
      --out identity.json
Prints only counts.
"""
import argparse
import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from app.domain.memoir.types import MemoirChatMessageRole, MemoirSessionGoal
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.services.ai_factory import get_ai_service
from app.services.memoir import narration_people as np
from app.services.memoir import voice_interviewer_prompt
from app.services.memoir.interview_book_context import InterviewBookContext, LifeTopic
from app.services.memoir.voice_followup_ai import (
    FOLLOW_UP_MAX_TOKENS,
    InterviewerPersona,
    ProgressPromptContext,
    system_prompt_for_persona,
)


def parse(content: str) -> list[dict]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    try:
        return np._ReadingOutput.model_validate(json.loads(match.group(0))).people if match else []
    except Exception:
        return []


class Book:
    """The same roster/candidate/id logic as narration_people.read_people, kept in memory."""

    def __init__(self, narrator: str = ""):
        self.narrator = narrator
        self.readings: dict[str, dict] = {}  # message id -> reading, insertion = narration order
        self.versions: dict[str, str] = {}

    def roster(self, keep: str | None = None) -> dict[str, np.Person]:
        return np.fold(
            (message_id, reading) for message_id, reading in self.readings.items()
            if message_id == keep or reading["transcript_version_id"] == self.versions[message_id]
        )

    async def read(self, client, model, message_id, text, previous_line="", version="v1"):
        self.versions[message_id] = version
        people = list(self.roster(keep=message_id).values())
        prompt = np.build_people_prompt(people=people, narrator=self.narrator, previous_line=previous_line, segment=text)
        refs = {f"P{i}": p.id for i, p in enumerate(people, start=1)}
        result = await client.generate_text(np.SYSTEM_PROMPT, prompt, requested_model=model, temperature=0.1,
                                            max_tokens=1200, timeout=90)

        def clean(value):
            value = (value or "").strip()
            return value[:80] if value and value.lower() != "null" else None

        self.readings[message_id] = {"transcript_version_id": version, "people": [
            {"id": refs.get(item.ref.strip()) or f"{message_id}:{version}:{i}",
             "same_as": refs.get((item.same_as or "").strip()),
             **{key: clean(getattr(item, key)) for key in np._FIELDS}}
            for i, item in enumerate(parse(result.content), start=1)
        ]}
        return result.content


def ordered(book: Book, message_ids: list[str]) -> None:
    book.readings = {mid: book.readings[mid] for mid in message_ids if mid in book.readings}


# A re-read must keep later segments' facts on the right person. check: (fact word, words the owner's card must
# contain in call or name, words it must not contain).
REREAD_CASES = [
    {"key": "reorder",
     "segments": [("M1", "", "我们车间有王师傅和李老师，王师傅教我开车床，李老师教我看图纸。"),
                  ("M2", "后来那两位怎么样了？", "教我开车床的那位后来当了厂长。")],
     "edit": ("M1", "我们车间有李老师和王师傅，李老师教我看图纸，王师傅教我开车床。"),
     "check": ("厂长", "王", "李")},
    {"key": "two_pronouns",
     "segments": [("M1", "", "我们车间有王师傅和李老师，王师傅教我开车床，李老师教我看图纸。"),
                  ("M2", "王师傅后来怎么样了？", "他后来当了厂长。"),
                  ("M3", "李老师呢？", "她后来去了南方。")],
     "edit": ("M1", "我们车间有李老师和王师傅，李老师教我看图纸，王师傅教我开车床。"),
     "check": ("厂长", "王", "李")},
    {"key": "delete",
     "segments": [("M1", "", "我们车间有王师傅和李老师，王师傅教我开车床，李老师教我看图纸。"),
                  ("M2", "王师傅后来怎么样了？", "他后来当了厂长。")],
     "edit": ("M1", "我们车间有个李老师，教我看图纸。"),
     "check": ("厂长", "", "李")},
    {"key": "rename",
     "segments": [("M1", "", "王师傅教我开车床，一教就是三年。"),
                  ("M2", "王师傅后来怎么样了？", "他后来当了厂长。")],
     "edit": ("M1", "汪师傅教我开车床，一教就是三年。"),
     "check": ("厂长", "汪", "")},
    {"key": "same_call",
     "segments": [("M1", "", "学校有两个王老师，一个叫王建国，教数学；一个叫王丽，教语文。"),
                  ("M2", "", "教语文的王丽老师后来嫁到了上海。")],
     "edit": ("M1", "学校有两个王老师，一个叫王丽，教语文；一个叫王建国，教数学。"),
     "check": ("上海", "丽", "建国")},
    {"key": "nothing_new",
     "segments": [("M1", "", "我二舅叫李建国，是我妈的弟弟，带我第一次进城。"),
                  ("M2", "", "二舅后来开了个小卖部。")],
     "edit": ("M1", "我二舅叫李建国，是我妈妈的弟弟，带我第一次进城。"),
     "check": ("小卖部", "二舅", "")},
]


async def reread_case(client, model, case, semaphore):
    async with semaphore:
        book = Book()
        order = [mid for mid, _, _ in case["segments"]]
        previous = {mid: line for mid, line, _ in case["segments"]}
        for mid, line, text in case["segments"]:
            await book.read(client, model, mid, text, line)
        before = {pid: p.line for pid, p in book.roster().items()}
        mid, text = case["edit"]
        raw = await book.read(client, model, mid, text, previous[mid], version="v2")
        ordered(book, order)
        roster = book.roster()
    fact, must, must_not = case["check"]
    owners = [p for p in roster.values() if "M2" in p.message_ids and fact in (p.note or "")]
    owner = owners[0] if owners else None
    label = f"{owner.call}{owner.name}" if owner else ""
    passed = (owner is None and not must) or bool(
        owner and (not must or must in label) and (not must_not or must_not not in label))
    return {"key": case["key"], "passed": passed, "before": before,
            "after": {pid: p.line for pid, p in roster.items()}, "owner": label, "raw": raw}


async def books_rerun(client, model, export, limit, semaphore):
    async def one(book_data):
        async with semaphore:
            book = Book(str((book_data.get("narrator") or {}).get("narrator_name") or ""))
            last_ai: dict[str, str] = {}
            count = 0
            for message in book_data["messages"]:
                if message["role"] != "user":
                    last_ai[message["session"]] = message["text"]
                    continue
                if count >= limit:
                    break
                await book.read(client, model, message["id"], message["text"], last_ai.pop(message["session"], ""))
                count += 1
            roster = book.roster()
            return {"title": book_data["title"], "segments": count,
                    "people": [{"line": p.line, "segments": len(p.message_ids), "unclear": p.unclear}
                               for p in roster.values()]}
    return await asyncio.gather(*(one(book) for book in export))


SCENES = [
    ("mid_story", "梁老师和他是什么关系", [
        ("ai", "您刚才说那年去了老掌沟，是跟谁一起去的？"),
        ("user", "跟梁老师一起去的。那天一早出发，走到半山腰突然下起雨来，我们俩就躲在一个山洞里，"),
    ], False),
    ("story_done", "梁老师和他是什么关系", [
        ("ai", "您刚才说那年去了老掌沟，是跟谁一起去的？"),
        ("user", "跟梁老师一起去的。走到半山腰下起雨，我们躲在山洞里等了两个钟头。下山的时候路过一个镇子，"
                 "一人买了一个卤羊头带回家，那天虽然淋得透湿，回想起来特别开心。"),
    ], True),
    ("already_said", "梁老师和他是什么关系", [
        ("ai", "您刚才说那年去了老掌沟，是跟谁一起去的？"),
        ("user", "跟梁老师，他是我研究生时候的导师。那天我们淋了一身雨，下山各买了一个卤羊头，就这么回来了。"),
    ], False),
    ("declined", "梁老师和他是什么关系", [
        ("user", "那年我跟梁老师去了老掌沟。他的事我不太想多说。"),
        ("ai", "好的，那就不说他。老掌沟那趟，路上有什么印象深的吗？"),
        ("user", "路上下了大雨，我们在山洞里躲了很久，下山以后才找到车回城，这一趟就是这样。"),
    ], False),
    ("already_asked", "梁老师和他是什么关系", [
        ("user", "那年我跟梁老师去了老掌沟，淋了一身雨。"),
        ("ai", "淋成那样也挺难忘。梁老师跟您是什么关系呀？"),
        ("user", "嗯，这个以后再说吧。下山以后我们各买了一个卤羊头，就回城了。"),
    ], False),
    ("name_story_done", "这位好朋友叫什么名字", [
        ("ai", "初中那会儿，有没有特别要好的朋友？"),
        ("user", "有一个好朋友，那年我生病住院，她每天放学都来给我送笔记，一直送了一个月。从那以后我也开始学着关心别人。"),
    ], True),
    ("no_questions", "梁老师和他是什么关系", [
        ("user", "我想自己讲，你别问我问题，听着就行。那年我跟梁老师去老掌沟，下了大雨。"),
        ("ai", "好，您慢慢讲，我听着。"),
        ("user", "我们在山洞里躲了两个钟头，下山各买了一个卤羊头，就回来了。"),
    ], False),
]


# The ask check alone: (line, topic, asked?).
CHECKS = [
    ("您觉得梁老师和校长关系怎么样？", "梁老师和他是什么关系", False),
    ("梁老师后来还跟您联系吗？", "梁老师和他是什么关系", False),
    ("您跟梁老师躲在山洞里，那场雨下了多久？", "梁老师和他是什么关系", False),
    ("这位梁老师，是您什么人呀？", "梁老师和他是什么关系", True),
    ("您和梁老师是怎么认识的？是同事还是老师？", "梁老师和他是什么关系", True),
    ("那位好朋友后来还常联系吗？", "这位好朋友叫什么名字", False),
    ("您还记得那位好朋友叫啥吗？", "这位好朋友叫什么名字", True),
    ("梁老师跟您是什么关系呀？", "梁建国和他是什么关系", True),
    ("他跟您是什么关系呀？", "梁建国和他是什么关系", True),
    ("那位老师后来还常来往吗？", "梁建国和他是什么关系", False),
]
WHO = {"梁建国和他是什么关系": "梁老师（梁建国）：和讲述者一起去老掌沟", "梁老师和他是什么关系": "梁老师：和讲述者一起去老掌沟",
       "这位好朋友叫什么名字": "她，好朋友：讲述者住院时天天送笔记"}
NARRATION = "那年我跟梁老师去了老掌沟，淋了一身雨，下山各买了一个卤羊头。"


async def check(model, line, topic, expect, semaphore):
    async with semaphore:
        got = await np.asked(np.Question(person_id="p", topic=topic, who=WHO[topic]), line, narration=NARRATION,
                             ai_model=model)
    return {"line": line, "expect": expect, "asked": got}


async def interviewer(client, model, scene, runs, semaphore):
    key, text, lines, expect = scene
    start = datetime(2026, 10, 1, 9)
    session = MemoirVoiceSession(session_goal=MemoirSessionGoal.OPEN_NARRATION.value, messages=[
        MemoirChatMessage(role=(MemoirChatMessageRole.USER if role == "user" else MemoirChatMessageRole.ASSISTANT).value,
                          content_text=line, turn_index=i, created_at=start + timedelta(minutes=i))
        for i, (role, line) in enumerate(lines)
    ])
    question = np.Question(person_id="p", topic=text, who=WHO[text])
    persona = InterviewerPersona(host_name="小余", voice_gender="female")
    prompt = voice_interviewer_prompt.build_stream_turn_prompt(
        session, has_previous_project_narration=True, activation_context="", regenerate=False,
        progress_context=ProgressPromptContext(prompt_text=""), persona=persona, completion_prompt_context=None,
        book_context=InterviewBookContext(
            chapters=(("童年", 6), ("求学", 9)), last_chapter_title="求学", anchor_topic=None,
            topics=(LifeTopic("工作经历", "第一份工作和后来的转变", 0), LifeTopic("成家", "怎么认识的另一半", 1)),
            narrator="余大姐，女，68 岁",
            people=("梁老师：和讲述者一起去老掌沟", "好朋友：初中时的好朋友，讲述者住院时天天送笔记"),
            person_question=question,
        ),
    )

    async def one():
        async with semaphore:
            result = await client.generate_text(system_prompt_for_persona(persona), prompt, requested_model=model,
                                                temperature=0.72, max_tokens=FOLLOW_UP_MAX_TOKENS, timeout=60)
        line = result.content.strip()
        async with semaphore:
            asked = "？" in line and await np.asked(question, line, narration=lines[-1][1], ai_model=model)
        return {"line": line, "asked": asked}

    outputs = await asyncio.gather(*(one() for _ in range(runs)))
    return {"key": key, "expect": expect, "outputs": outputs}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("identity.json"))
    parser.add_argument("--scenes-only", action="store_true")
    parser.add_argument("--no-books", action="store_true")
    args = parser.parse_args()
    client = get_ai_service(args.model)
    semaphore = asyncio.Semaphore(6)
    export = json.loads(args.export.read_text(encoding="utf-8"))
    async def none():
        return []

    books, rereads, scenes = await asyncio.gather(
        none() if args.scenes_only or args.no_books else books_rerun(client, args.model, export, args.limit, semaphore),
        none() if args.scenes_only else asyncio.gather(*(reread_case(client, args.model, case, semaphore)
                                                         for case in REREAD_CASES for _ in range(args.runs))),
        asyncio.gather(*(interviewer(client, args.model, scene, args.runs, semaphore) for scene in SCENES)),
    )
    checks = await asyncio.gather(*(check(args.model, *case, semaphore) for case in CHECKS))
    args.out.write_text(json.dumps({"books": books, "rereads": rereads, "scenes": scenes, "checks": checks},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "segments": sum(b["segments"] for b in books), "people": sum(len(b["people"]) for b in books),
        "rereads_passed": f"{sum(r['passed'] for r in rereads)}/{len(rereads)}",
        "asks_right": f"{sum(o['asked'] == s['expect'] for s in scenes for o in s['outputs'])}"
                      f"/{sum(len(s['outputs']) for s in scenes)}",
        "checks_right": f"{sum(c['asked'] == c['expect'] for c in checks)}/{len(checks)}",
    }))


if __name__ == "__main__":
    asyncio.run(main())
