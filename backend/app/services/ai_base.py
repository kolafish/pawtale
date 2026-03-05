from abc import ABC, abstractmethod


class AITextServiceBase(ABC):
    """AI文本生成服务基类"""

    @abstractmethod
    async def auto_complete_character(
        self,
        name: str | None = None,
        gender: str | None = None,
        inspiration: str | None = None,
    ) -> dict:
        """根据已有信息自动补全角色设定"""
        pass

    @abstractmethod
    async def generate_story(
        self,
        characters: list[dict],
        scene: dict,
        mood: str,
        writing_style: str = "细腻",
        chapter_length: str = "中",
        previous_content: str | None = None,
        custom_direction: str | None = None,
        chapter_number: int = 1,
        outline_context: dict | None = None,
        story_state: dict | None = None,
        debug: bool = False,
    ) -> dict:
        """生成故事内容和分支选项"""
        pass

    @abstractmethod
    async def generate_chapter_title(self, content: str) -> str:
        """为已有内容生成章节标题"""
        pass

    @abstractmethod
    async def revise_chapter(
        self,
        original_content: str,
        revision_instruction: str,
        characters: list[dict],
        writing_style: str = "细腻",
        chapter_length: str = "中",
        debug: bool = False,
    ) -> dict:
        """根据修改意见重新生成章节内容"""
        pass

    @abstractmethod
    async def enhance_outline(
        self,
        title: str,
        mood: str | None = None,
        characters: list[dict] | None = None,
        scenes: list[dict] | None = None,
    ) -> dict:
        """根据大纲标题和已有信息，AI生成核心冲突、剧情提示等"""
        pass

    @abstractmethod
    async def regenerate_choices(
        self,
        current_content: str,
        characters: list[dict],
        mood: str = "甜",
        hint: str | None = None,
        debug: bool = False,
    ) -> list[dict] | dict:
        """重新生成分支选项"""
        pass

    @abstractmethod
    async def polish_chapters(
        self,
        chapters: list[dict],
        characters: list[dict],
        writing_style: str = "细腻",
        instruction: str | None = None,
        debug: bool = False,
    ) -> str | dict:
        """整体润色多个章节，使其更通顺连贯"""
        pass

    @abstractmethod
    async def generate_outline_draft(
        self,
        story_goal: str,
        character_tension: str,
        central_conflict: str,
        characters: list[dict] | None = None,
        mood: str | None = None,
    ) -> dict:
        """基于引导流程生成大纲草稿"""
        pass

    @abstractmethod
    async def extract_story_context(
        self,
        content: str,
        debug: bool = False,
    ) -> dict:
        """从已写文本中提炼角色/场景/剧情要点"""
        pass
