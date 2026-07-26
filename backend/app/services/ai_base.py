from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.services.ai_debug import build_debug_info
from app.services.ai_project_analysis import (
    analyze_project_content as analyze_project_content_use_case,
)
from app.services.ai_revision import revise_chapter as revise_chapter_use_case
from app.services.ai_story import generate_story as generate_story_use_case
from app.services.json_utils import extract_json_payload, strip_json_trailing_commas, try_parse_json
from app.services.ai_streaming import AITextStreamChunk


class AITextServiceBase(ABC):
    """Provider base class for AI text generation.

    Subclasses only implement transport concerns. Product-specific prompts live in
    dedicated use-case modules and are exposed here as thin public methods.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @abstractmethod
    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        ...

    def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        raise NotImplementedError(f"{self.provider_name} does not support streaming text generation")

    def _wrap_debug(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        content: str,
        response_json: dict | None,
    ) -> dict:
        return build_debug_info(
            request_info={
                "provider": self.provider_name,
                "model": self.model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            response_text=content,
            response_json=response_json,
        )

    @staticmethod
    def _extract_json(content: str) -> str:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return content.strip()

    @staticmethod
    def _strip_json_trailing_commas(text: str) -> str:
        return strip_json_trailing_commas(text)

    @classmethod
    def _try_parse_json(cls, text: str) -> dict | None:
        parsed = try_parse_json(text)
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _extract_json_payload(cls, content: str) -> dict:
        parsed = extract_json_payload(content)
        return parsed if isinstance(parsed, dict) else {}

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
        return await generate_story_use_case(
            self,
            characters=characters,
            scene=scene,
            mood=mood,
            writing_style=writing_style,
            chapter_length=chapter_length,
            previous_content=previous_content,
            custom_direction=custom_direction,
            chapter_number=chapter_number,
            outline_context=outline_context,
            story_state=story_state,
            debug=debug,
        )

    async def revise_chapter(
        self,
        original_content: str,
        revision_instruction: str,
        characters: list[dict],
        writing_style: str = "细腻",
        chapter_length: str = "中",
        debug: bool = False,
    ) -> dict:
        return await revise_chapter_use_case(
            self,
            original_content=original_content,
            revision_instruction=revision_instruction,
            characters=characters,
            writing_style=writing_style,
            chapter_length=chapter_length,
            debug=debug,
        )

    async def analyze_project_content(
        self,
        content: str,
        debug: bool = False,
    ) -> dict:
        return await analyze_project_content_use_case(
            self,
            content=content,
            debug=debug,
        )
