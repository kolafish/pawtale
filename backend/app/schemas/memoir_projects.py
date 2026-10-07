from pydantic import BaseModel, Field, field_validator

from app.domain.memoir.types import (
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirSessionGoal,
    MemoirTranscriptPolishLevel,
)
from app.schemas.auth import UserResponse
from app.schemas.projects import ProjectResponse
from app.services.memoir.share_video_styles import DEFAULT_SHARE_VIDEO_STYLE, ShareVideoStyle


class MemoirProjectCreate(BaseModel):
    title: str
    description: str | None = None
    elder_name: str | None = None
    narrator_gender: str | None = None
    narrator_origin: str | None = None
    narrator_age: int | None = None
    default_guidance_mode: MemoirGuidanceMode | None = None
    default_interview_type: MemoirInterviewType | None = None
    default_session_goal: MemoirSessionGoal | None = None
    transcript_polish_level: MemoirTranscriptPolishLevel = MemoirTranscriptPolishLevel.MEDIUM
    share_video_style: ShareVideoStyle = DEFAULT_SHARE_VIDEO_STYLE

    @field_validator("narrator_age")
    @classmethod
    def _valid_age(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value > 130):
            raise ValueError("年龄需要在 0 到 130 岁之间")
        return value

class MemoirProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    elder_name: str | None = None
    narrator_gender: str | None = None
    narrator_origin: str | None = None
    narrator_age: int | None = None
    default_guidance_mode: MemoirGuidanceMode | None = None
    default_interview_type: MemoirInterviewType | None = None
    default_session_goal: MemoirSessionGoal | None = None
    transcript_polish_level: MemoirTranscriptPolishLevel | None = None
    share_video_style: ShareVideoStyle | None = None

    @field_validator("narrator_age")
    @classmethod
    def _valid_age(cls, value: int | None) -> int | None:
        if value is not None and (value < 0 or value > 130):
            raise ValueError("年龄需要在 0 到 130 岁之间")
        return value

class MemoirProjectListResponse(BaseModel):
    active_project_id: str
    active_project: ProjectResponse | None = None
    projects: list[ProjectResponse] = Field(default_factory=list)

class MemoirIdentityResponse(UserResponse):
    active_project_id: str
    active_project: ProjectResponse | None = None
    projects: list[ProjectResponse] = Field(default_factory=list)
