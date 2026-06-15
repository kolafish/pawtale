from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.domain.memoir import MemoirMemoryType
from app.models.users import User
from app.schemas.memoir import (
    MemoirCoverageItemResponse,
    MemoirDashboardResponse,
    MemoirEventMemoryResponse,
    MemoirIdentityResponse,
    MemoirInterviewState,
    MemoirMemoryUpdateRequest,
    MemoirMemoryStatsResponse,
    MemoirOpenQuestionResponse,
    MemoirOpenQuestionUpdateRequest,
    MemoirOutputDetailResponse,
    MemoirOutputResponse,
    MemoirPersonMemoryResponse,
    MemoirPlaceMemoryResponse,
    MemoirProjectCreate,
    MemoirProjectListResponse,
    MemoirProjectUpdate,
    MemoirQuestionCandidateResponse,
    MemoirQuestionResponse,
    MemoirSessionCreate,
    MemoirSessionProcessRequest,
    MemoirSessionProcessResponse,
    MemoirSessionSummaryResponse,
    MemoirStoryDetailResponse,
    MemoirStoryListResponse,
    MemoirSummaryResponse,
    MemoirThemeMemoryResponse,
)
from app.schemas.projects import ProjectResponse
from app.services.memoir import coverage as memoir_coverage
from app.services.memoir import interview as memoir_interview
from app.services.memoir import memory_repository as memoir_memory_repository
from app.services.memoir import memory_extraction as memoir_memory_extraction
from app.services.memoir import outputs as memoir_outputs
from app.services.memoir import projects as memoir_projects
from app.services.memoir import question_planner as memoir_question_planner
from app.services.memoir import session_summary as memoir_session_summary
from app.services.memoir import story_cards as memoir_story_cards
from app.services.memoir.access import load_memoir_project


router = APIRouter()


def _enum_value(value: object) -> str:
    return getattr(value, "value", value) or ""


def _memory_stats(
    *,
    people: list[MemoirPersonMemoryResponse],
    events: list[MemoirEventMemoryResponse],
    places: list[MemoirPlaceMemoryResponse],
    themes: list[MemoirThemeMemoryResponse],
    open_questions: list[MemoirOpenQuestionResponse],
    coverage: list[MemoirCoverageItemResponse],
    candidates: list[MemoirQuestionCandidateResponse],
    outputs: list[MemoirOutputResponse],
) -> MemoirMemoryStatsResponse:
    memory_statuses = [
        _enum_value(item.status)
        for item in [*people, *events, *places, *themes]
    ]
    open_question_statuses = [_enum_value(item.status) for item in open_questions]
    coverage_scores = [item.coverage_score for item in coverage]
    return MemoirMemoryStatsResponse(
        total_memory_count=len(memory_statuses),
        draft_memory_count=memory_statuses.count("draft"),
        confirmed_memory_count=memory_statuses.count("confirmed"),
        rejected_memory_count=memory_statuses.count("rejected"),
        open_question_count=open_question_statuses.count("open"),
        asked_open_question_count=open_question_statuses.count("asked"),
        answered_open_question_count=open_question_statuses.count("answered"),
        dismissed_open_question_count=open_question_statuses.count("dismissed"),
        question_candidate_count=len(candidates),
        output_count=len(outputs),
        coverage_average=round(sum(coverage_scores) / len(coverage_scores)) if coverage_scores else 0,
        low_coverage_count=sum(score < 30 for score in coverage_scores),
    )


async def _default_memoir_project_id(db: AsyncSession, user_id: str) -> str:
    project = await memoir_projects.get_or_create_mobile_memoir_project(db, user_id)
    return project.id


async def _memoir_project_id(
    db: AsyncSession,
    user_id: str,
    project_id: str | None = None,
) -> str:
    if project_id:
        project = await load_memoir_project(db, project_id, user_id)
        return project.id
    return await _default_memoir_project_id(db, user_id)


@router.get("/project", response_model=ProjectResponse)
async def get_mobile_memoir_project(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.get_or_create_mobile_memoir_project(db, current_user.id)


@router.get("/me", response_model=MemoirIdentityResponse)
async def get_mobile_memoir_identity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = await memoir_projects.mobile_project_list(db, current_user.id)
    return MemoirIdentityResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
        created_at=current_user.created_at,
        active_project_id=projects.active_project_id,
        active_project=projects.active_project,
        projects=projects.projects,
    )


@router.get("/projects", response_model=MemoirProjectListResponse)
async def list_mobile_memoir_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.mobile_project_list(db, current_user.id)


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_mobile_memoir_project(
    req: MemoirProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.create_mobile_memoir_project(db, current_user.id, req)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_mobile_memoir_project(
    project_id: str,
    req: MemoirProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.update_mobile_memoir_project(
        db,
        current_user.id,
        project_id,
        req,
    )


@router.post("/projects/{project_id}/activate", response_model=MemoirProjectListResponse)
async def activate_mobile_memoir_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_projects.activate_mobile_memoir_project(
        db,
        current_user.id,
        project_id,
    )


@router.get("/dashboard", response_model=MemoirDashboardResponse)
async def get_mobile_memoir_dashboard(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if project_id:
        project_response = await memoir_projects.get_mobile_memoir_project(
            db,
            current_user.id,
            project_id,
        )
    else:
        project_response = await memoir_projects.active_mobile_memoir_project(db, current_user.id)
    project_id = project_response.id

    interview = await memoir_interview.get_or_create_current_session(db, project_id, current_user.id)
    summary = await memoir_story_cards.get_summary(db, project_id, current_user.id)
    session_summary = await memoir_session_summary.get_session_summary(
        db,
        current_user.id,
        interview.session.id,
    )
    people = await memoir_memory_repository.list_person_memories(db, current_user.id, project_id)
    events = await memoir_memory_repository.list_event_memories(db, current_user.id, project_id)
    places = await memoir_memory_repository.list_place_memories(db, current_user.id, project_id)
    themes = await memoir_memory_repository.list_theme_memories(db, current_user.id, project_id)
    open_questions = await memoir_memory_repository.list_open_questions(db, current_user.id, project_id)
    coverage = await memoir_coverage.get_coverage_map(db, current_user.id, project_id)
    if not coverage:
        await memoir_coverage.calculate_coverage_snapshots(db, current_user.id, project_id)
        await db.commit()
        coverage = await memoir_coverage.get_coverage_map(db, current_user.id, project_id)
    candidates = await memoir_question_planner.list_question_candidates(
        db,
        current_user.id,
        project_id,
        interview.session.id,
    )
    outputs = await memoir_outputs.list_outputs(db, current_user.id, project_id)

    return MemoirDashboardResponse(
        project=project_response,
        interview=interview,
        summary=summary,
        session_summary=session_summary,
        people=people,
        events=events,
        places=places,
        themes=themes,
        open_questions=open_questions,
        coverage=coverage,
        candidates=candidates,
        outputs=outputs,
        memory_stats=_memory_stats(
            people=people,
            events=events,
            places=places,
            themes=themes,
            open_questions=open_questions,
            coverage=coverage,
            candidates=candidates,
            outputs=outputs,
        ),
    )


@router.post("/sessions", response_model=MemoirInterviewState, status_code=201)
async def create_mobile_memoir_session(
    req: MemoirSessionCreate,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_interview.create_session(db, project_id, current_user.id, req)


@router.get("/sessions/current", response_model=MemoirInterviewState)
async def get_current_mobile_memoir_session(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_interview.get_or_create_current_session(db, project_id, current_user.id)


@router.post("/sessions/{session_id}/process", response_model=MemoirSessionProcessResponse)
async def process_mobile_memoir_session(
    session_id: str,
    req: MemoirSessionProcessRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_memory_extraction.process_session_memory(
        db,
        session_id,
        current_user.id,
        req,
    )


@router.get("/sessions/{session_id}/summary", response_model=MemoirSessionSummaryResponse | None)
async def get_mobile_memoir_session_summary(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_session_summary.get_session_summary(db, current_user.id, session_id)


@router.get("/questions/next", response_model=MemoirQuestionResponse | None)
async def get_next_mobile_memoir_question(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_interview.get_next_question(db, project_id, current_user.id)


@router.get("/memories/people", response_model=list[MemoirPersonMemoryResponse])
async def list_mobile_memoir_people(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_memory_repository.list_person_memories(db, current_user.id, project_id)


@router.get("/memories/events", response_model=list[MemoirEventMemoryResponse])
async def list_mobile_memoir_events(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_memory_repository.list_event_memories(db, current_user.id, project_id)


@router.get("/memories/places", response_model=list[MemoirPlaceMemoryResponse])
async def list_mobile_memoir_places(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_memory_repository.list_place_memories(db, current_user.id, project_id)


@router.get("/memories/themes", response_model=list[MemoirThemeMemoryResponse])
async def list_mobile_memoir_themes(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_memory_repository.list_theme_memories(db, current_user.id, project_id)


@router.patch("/memories/{memory_type}/{memory_id}")
async def update_mobile_memoir_memory(
    memory_type: MemoirMemoryType,
    memory_id: str,
    req: MemoirMemoryUpdateRequest,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    result = await memoir_memory_repository.update_memory(
        db,
        current_user.id,
        project_id,
        memory_type,
        memory_id,
        req,
    )
    await db.commit()
    return result


@router.get("/open-questions", response_model=list[MemoirOpenQuestionResponse])
async def list_mobile_memoir_open_questions(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_memory_repository.list_open_questions(db, current_user.id, project_id)


@router.patch("/open-questions/{question_id}", response_model=MemoirOpenQuestionResponse)
async def update_mobile_memoir_open_question(
    question_id: str,
    req: MemoirOpenQuestionUpdateRequest,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    result = await memoir_memory_repository.update_open_question(
        db,
        current_user.id,
        project_id,
        question_id,
        req,
    )
    await db.commit()
    return result


@router.get("/coverage", response_model=list[MemoirCoverageItemResponse])
async def get_mobile_memoir_coverage(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_coverage.get_coverage_map(db, current_user.id, project_id)


@router.post("/coverage/recalculate", response_model=list[MemoirCoverageItemResponse])
async def recalculate_mobile_memoir_coverage(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    await memoir_coverage.calculate_coverage_snapshots(db, current_user.id, project_id)
    await db.commit()
    return await memoir_coverage.get_coverage_map(db, current_user.id, project_id)


@router.get("/question-candidates", response_model=list[MemoirQuestionCandidateResponse])
async def list_mobile_memoir_question_candidates(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    session_id = await memoir_interview.current_session_id(db, project_id, current_user.id)
    return await memoir_question_planner.list_question_candidates(
        db,
        current_user.id,
        project_id,
        session_id,
    )


@router.post("/question-candidates/generate", response_model=list[MemoirQuestionCandidateResponse])
async def generate_mobile_memoir_question_candidates(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    state = await memoir_interview.get_or_create_current_session(db, project_id, current_user.id)
    await memoir_question_planner.generate_question_candidates(
        db,
        current_user.id,
        project_id,
        state.session.id,
    )
    await db.commit()
    return await memoir_question_planner.list_question_candidates(
        db,
        current_user.id,
        project_id,
        state.session.id,
    )


@router.get("/outputs", response_model=list[MemoirOutputResponse])
async def list_mobile_memoir_outputs(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_outputs.list_outputs(db, current_user.id, project_id)


@router.get("/outputs/{output_id}", response_model=MemoirOutputDetailResponse)
async def get_mobile_memoir_output(
    output_id: str,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_outputs.get_output(db, current_user.id, project_id, output_id)


@router.get("/summary", response_model=MemoirSummaryResponse)
async def get_mobile_memoir_summary(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_story_cards.get_summary(db, project_id, current_user.id)


@router.get("/stories", response_model=MemoirStoryListResponse)
async def list_mobile_memoir_stories(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_story_cards.list_story_cards(db, project_id, current_user.id)


@router.get("/stories/{answer_id}", response_model=MemoirStoryDetailResponse)
async def get_mobile_memoir_story(
    answer_id: str,
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project_id = await _memoir_project_id(db, current_user.id, project_id)
    return await memoir_story_cards.get_story_detail(db, project_id, current_user.id, answer_id)
