import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from app.services.ai_base import AITextServiceBase


BOOK_TITLE = "从河湾到城里"
OLD_FACT = "黄纸灯笼，是舅舅送来的"
NEW_FACT = "黄纸灯笼，是姨妈送来的"
PRIVATE_SECTION = "旧钥匙的私下讲述"
FALSE_QUESTION = "您是先到武汉机械厂报到的吗？"


@dataclass(frozen=True)
class Turn:
    chapter: str
    section: str
    text: str

    @property
    def opening(self):
        return self.text[:10]

    @property
    def prose(self):
        return self.text.replace("我去城里不是去武汉，而是去南京。", "我去城里，去了南京。").replace(
            "黄纸灯笼是姨妈送来的，我第一回讲的时候说成了舅舅，已经把第一回那段讲述改正。",
            "黄纸灯笼是姨妈送来的。",
        )


def read_interviews():
    sessions: list[list[Turn]] = []
    chapter = section = ""
    for line in Path(__file__).with_name("riverbend_interviews.md").read_text().splitlines():
        if line.startswith("# "):
            sessions.append([])
        elif line.startswith("## "):
            chapter, section = line[3:].split("｜")
        elif line:
            sessions[-1].append(Turn(chapter, section, line))
    return sessions


def corrected(turn):
    return replace(turn, text=turn.text.replace(OLD_FACT, NEW_FACT))


def paragraphs(turns):
    texts = [turn.prose for turn in turns]
    if turns[0].section == "那场涨水" and len(texts) >= 2:
        # The first two turns describe one continuous move, so the opening
        # paragraph is long enough to exercise a continuation across pages.
        return [texts[0] + texts[1], *texts[2:]]
    return texts


def _prompt_json(prompt, label):
    return json.JSONDecoder().raw_decode(prompt.split(f"{label}：\n", 1)[1])[0]


class EditorialReplay(AITextServiceBase):
    provider_name = "test"
    model_name = "memoir-print-replay"

    def __init__(self, sessions):
        self.turns = {turn.opening: turn for session in sessions for turn in session}
        assert len(self.turns) == sum(map(len, sessions))
        self.saw_false_question = False
        self.saw_correction = False

    def _turn(self, text):
        return self.turns[text[:10]]

    def _growth(self, prompt):
        chapters = {row["chapter_title"]: row["chapter_id"] for row in _prompt_json(prompt, "已有章节")}
        sections = {row["section_title"]: row["section_id"] for row in _prompt_json(prompt, "已有小节")}
        new_chapters = {}
        placements = {}
        for row in _prompt_json(prompt, "本次新讲述"):
            turn = self._turn(row["text"])
            placement = {"kind": "story", "reason": "同一亲历事件"}
            if turn.section in sections:
                placement["section_id"] = sections[turn.section]
            else:
                chapter_ref = chapters.get(turn.chapter)
                if chapter_ref is None:
                    chapter_ref = new_chapters.setdefault(turn.chapter, f"N{len(new_chapters) + 1}")
                placement.update(chapter_ref=chapter_ref, new_section_title=turn.section)
            placements[str(row["source_index"])] = placement
        return {
            "new_chapters": [{"chapter_ref": ref, "title": title} for title, ref in new_chapters.items()],
            "placements": placements,
        }

    def _write(self, prompt):
        title = re.search(r"^小节标题: (.+)$", prompt, re.M)[1]
        refs = re.search(r"^全部已归属素材编号.+：(.+)$", prompt, re.M)[1].split("、")
        inputs = {}
        label = "现有正文的来源关联（只定位保留文字，不新增事实）"
        if label in prompt:
            for point in _prompt_json(prompt, label):
                for ref in point["material_ids"]:
                    inputs[ref] = self._turn(point["text"])
        for ref, question, text in re.findall(
            r"^material_id: (M\d+)\n(?:对应提问: ([^\n]+)\n)?素材版本[^:\n]*: (.+)$",
            prompt, re.M,
        ):
            turn = self._turn(text)
            if OLD_FACT in turn.text and NEW_FACT in text:
                turn = corrected(turn)
                self.saw_correction = True
            if turn.text.startswith("我去城里不是去武汉"):
                self.saw_false_question |= question == FALSE_QUESTION
            inputs[ref] = turn
        assert set(inputs) == set(refs), f"{title}: provider received {list(inputs)}, expected {refs}"
        turns = [inputs[ref] for ref in refs]
        content = "\n\n".join(paragraphs(turns))
        return {
            "content": content,
            "used_material_ids": refs,
            "body_digest": {"summary": turns[0].prose[:55], "focus": title, "points": [
                {"text": turn.prose[:48], "material_ids": [ref]} for turn, ref in zip(turns, refs, strict=True)
            ]},
        }

    async def _call_llm(self, _system, prompt, **_kwargs):
        if prompt.startswith("请把下面的口述内容整理成回忆录章节规划"):
            sources = re.findall(r"^source_index: (\d+)\n(?:interviewer_line: [^\n]+\n)?text: (.+)$", prompt, re.M)
            titles = {}
            for index, text in sources:
                titles.setdefault(self._turn(text).chapter, []).append(int(index))
            payload = {"title": BOOK_TITLE, "chapters": [
                {"title": title, "source_indices": indices} for title, indices in titles.items()
            ]}
        elif prompt.startswith("请将本次全部讲述安排到故事目录中"):
            payload = self._growth(prompt)
        elif "这里只设计候选小节" in prompt:
            titles = dict.fromkeys(self._turn(row["excerpt"]).section for row in _prompt_json(prompt, "讲述概览"))
            payload = {"sections": [{"title": title, "section_type": "event"} for title in titles]}
        elif "待归类讲述：\n" in prompt:
            candidates = {row["title"]: row["section_id"] for row in _prompt_json(prompt, "候选小节")}
            payload = {"assignments": [
                {"utterance_id": row["utterance_id"], "action": "assign_existing", "confidence": 1,
                 "section_id": candidates[self._turn(row["text"]).section], "reason": "同一亲历事件"}
                for row in _prompt_json(prompt, "待归类讲述")
            ]}
        elif "全部已归属素材编号" in prompt and "（本次生成）" in prompt:
            payload = self._write(prompt)
        elif prompt.startswith("新讲述已经归入《"):
            payload = {"sections": []}
        elif "审阅现有回忆录目录" in prompt:
            payload = {"findings": []}
        elif "请通读回忆录章节" in prompt:
            payload = {"summary": "按讲述顺序记录本章经历", "focus": "亲历事件", "candidate_issues": []}
        elif "请通读下面这本回忆录" in prompt:
            payload = {"summary": "河湾的童年、进城工作和回乡", "strengths": [], "structure_guidance": [],
                       "candidate_issues": [], "editorial_actions": []}
        else:
            raise AssertionError(f"Unexpected editorial model request: {prompt[:250]}")
        return json.dumps(payload, ensure_ascii=False)
