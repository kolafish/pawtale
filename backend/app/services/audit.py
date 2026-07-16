from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditDailyFeatureUsage, AuditDailyUserUsage, AuditEvent, AuditSession
from app.models.users import User
from app.schemas.audit import (
    AuditDashboardResponse,
    AuditDashboardSummary,
    AuditDailyFeatureUsageResponse,
    AuditDailyUserUsageResponse,
    AuditEventsIngestRequest,
    AuditEventsIngestResponse,
    AuditExitLocationResponse,
    AuditSessionResponse,
    AuditUserBrief,
    AuditUserDayResponse,
)


MAX_ACTIVE_EVENT_DURATION_MS = 60_000
EXIT_EVENT_TYPES = {"page_leave"}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _limit(value: str | None, max_length: int) -> str:
    return (value or "").strip()[:max_length]


def _active_ms(duration_ms: int) -> int:
    return min(MAX_ACTIVE_EVENT_DURATION_MS, max(0, int(duration_ms or 0)))


def _user_brief(user: User) -> AuditUserBrief:
    return AuditUserBrief(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


async def _load_or_create_session(
    db: AsyncSession,
    user_id: str,
    req: AuditEventsIngestRequest,
    first_seen_at: datetime,
) -> AuditSession:
    session = await db.scalar(
        select(AuditSession).where(
            AuditSession.user_id == user_id,
            AuditSession.client_session_id == req.client_session_id,
        )
    )
    if session:
        if first_seen_at < session.started_at:
            session.started_at = first_seen_at
        if req.app_version:
            session.app_version = _limit(req.app_version, 80)
        if req.device_summary:
            session.device_summary = _limit(req.device_summary, 255)
        if req.metadata_json:
            session.metadata_json = req.metadata_json
        return session

    session = AuditSession(
        user_id=user_id,
        client_session_id=req.client_session_id,
        platform=req.platform,
        started_at=first_seen_at,
        last_seen_at=first_seen_at,
        app_version=_limit(req.app_version, 80),
        device_summary=_limit(req.device_summary, 255),
        metadata_json=req.metadata_json,
    )
    db.add(session)
    await db.flush()
    return session


async def _session_has_event_on_date(db: AsyncSession, session_id: str, event_date: date) -> bool:
    existing = await db.scalar(
        select(AuditEvent.id)
        .where(AuditEvent.session_id == session_id, AuditEvent.event_date == event_date)
        .limit(1)
    )
    return existing is not None


async def _load_or_create_daily_user_usage(
    db: AsyncSession,
    user_id: str,
    event_date: date,
    platform: str,
) -> AuditDailyUserUsage:
    row = await db.scalar(
        select(AuditDailyUserUsage).where(
            AuditDailyUserUsage.user_id == user_id,
            AuditDailyUserUsage.date == event_date,
            AuditDailyUserUsage.platform == platform,
        )
    )
    if row:
        return row
    row = AuditDailyUserUsage(user_id=user_id, date=event_date, platform=platform)
    db.add(row)
    await db.flush()
    return row


async def _load_or_create_daily_feature_usage(
    db: AsyncSession,
    user_id: str,
    event_date: date,
    platform: str,
    feature_code: str,
) -> AuditDailyFeatureUsage:
    row = await db.scalar(
        select(AuditDailyFeatureUsage).where(
            AuditDailyFeatureUsage.user_id == user_id,
            AuditDailyFeatureUsage.date == event_date,
            AuditDailyFeatureUsage.platform == platform,
            AuditDailyFeatureUsage.feature_code == feature_code,
        )
    )
    if row:
        return row
    row = AuditDailyFeatureUsage(
        user_id=user_id,
        date=event_date,
        platform=platform,
        feature_code=feature_code,
    )
    db.add(row)
    await db.flush()
    return row


async def ingest_events(
    db: AsyncSession,
    user_id: str,
    req: AuditEventsIngestRequest,
    *,
    commit: bool = True,
) -> AuditEventsIngestResponse:
    ordered_events = sorted(req.events, key=lambda item: item.occurred_at)
    first_seen_at = _utc_naive(ordered_events[0].occurred_at)
    session = await _load_or_create_session(db, user_id, req, first_seen_at)
    accepted = 0
    duplicate = 0

    for item in ordered_events:
        duplicate_event = await db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.user_id == user_id,
                AuditEvent.client_event_id == item.client_event_id,
            )
        )
        if duplicate_event:
            duplicate += 1
            continue

        occurred_at = _utc_naive(item.occurred_at)
        event_date = item.event_date or occurred_at.date()
        page_path = _limit(item.page_path, 255)
        feature_code = _limit(item.feature_code, 120)
        exit_reason = _limit(item.exit_reason, 60)
        active_ms = _active_ms(item.duration_ms)
        session_had_date_event = await _session_has_event_on_date(db, session.id, event_date)

        event = AuditEvent(
            user_id=user_id,
            session_id=session.id,
            client_event_id=item.client_event_id,
            event_type=item.event_type,
            event_date=event_date,
            platform=req.platform,
            page_path=page_path,
            feature_code=feature_code,
            project_id=_limit(item.project_id, 36) or None,
            voice_session_id=_limit(item.voice_session_id, 36) or None,
            occurred_at=occurred_at,
            duration_ms=int(item.duration_ms or 0),
            active_ms=active_ms,
            exit_reason=exit_reason,
            metadata_json=item.metadata_json,
        )
        db.add(event)

        session.last_seen_at = max(session.last_seen_at, occurred_at)
        session.active_ms += active_ms
        if item.event_type in EXIT_EVENT_TYPES and (
            session.ended_at is None or occurred_at >= session.ended_at
        ):
            session.ended_at = occurred_at
            session.exit_page_path = page_path
            session.exit_feature_code = feature_code
            session.exit_reason = exit_reason

        daily_user = await _load_or_create_daily_user_usage(db, user_id, event_date, req.platform)
        daily_user.active_ms += active_ms
        daily_user.event_count += 1
        if not session_had_date_event:
            daily_user.session_count += 1
        if daily_user.last_seen_at is None or occurred_at >= daily_user.last_seen_at:
            daily_user.last_seen_at = occurred_at
            if item.event_type in EXIT_EVENT_TYPES:
                daily_user.last_exit_page_path = page_path
                daily_user.last_exit_feature_code = feature_code
                daily_user.last_exit_reason = exit_reason

        if feature_code:
            daily_feature = await _load_or_create_daily_feature_usage(
                db,
                user_id,
                event_date,
                req.platform,
                feature_code,
            )
            daily_feature.active_ms += active_ms
            daily_feature.event_count += 1
            if daily_feature.last_seen_at is None or occurred_at >= daily_feature.last_seen_at:
                daily_feature.last_seen_at = occurred_at

        accepted += 1

    if commit:
        await db.commit()
    else:
        await db.flush()
    return AuditEventsIngestResponse(accepted=accepted, duplicate=duplicate, session_id=session.id)


def _date_filters(model, start: date, end: date):
    return model.date >= start, model.date <= end


async def dashboard(
    db: AsyncSession,
    start: date,
    end: date,
    user_id: str | None = None,
    platform: str | None = None,
    feature_code: str | None = None,
) -> AuditDashboardResponse:
    daily_filters = [*_date_filters(AuditDailyUserUsage, start, end)]
    feature_filters = [*_date_filters(AuditDailyFeatureUsage, start, end)]
    if user_id:
        daily_filters.append(AuditDailyUserUsage.user_id == user_id)
        feature_filters.append(AuditDailyFeatureUsage.user_id == user_id)
    if platform:
        daily_filters.append(AuditDailyUserUsage.platform == platform)
        feature_filters.append(AuditDailyFeatureUsage.platform == platform)
    if feature_code:
        feature_filters.append(AuditDailyFeatureUsage.feature_code == feature_code)

    daily_result = await db.execute(
        select(AuditDailyUserUsage, User)
        .join(User, AuditDailyUserUsage.user_id == User.id)
        .where(and_(*daily_filters))
        .order_by(desc(AuditDailyUserUsage.date), desc(AuditDailyUserUsage.active_ms))
        .limit(500)
    )
    daily_rows = daily_result.all()

    feature_result = await db.execute(
        select(AuditDailyFeatureUsage, User)
        .join(User, AuditDailyFeatureUsage.user_id == User.id)
        .where(and_(*feature_filters))
        .order_by(desc(AuditDailyFeatureUsage.active_ms), desc(AuditDailyFeatureUsage.event_count))
        .limit(500)
    )
    feature_rows = feature_result.all()

    exit_counter: Counter[tuple[str, str, str]] = Counter()
    for row, _user in daily_rows:
        if row.last_exit_page_path or row.last_exit_feature_code or row.last_exit_reason:
            exit_counter[(row.last_exit_page_path, row.last_exit_feature_code, row.last_exit_reason)] += 1

    exit_locations = [
        AuditExitLocationResponse(
            page_path=page_path,
            feature_code=feature_code_value,
            reason=reason,
            count=count,
        )
        for (page_path, feature_code_value, reason), count in exit_counter.most_common(20)
    ]
    top_exit = exit_locations[0] if exit_locations else None

    summary = AuditDashboardSummary(
        active_ms=sum(row.active_ms for row, _user in daily_rows),
        session_count=sum(row.session_count for row, _user in daily_rows),
        event_count=sum(row.event_count for row, _user in daily_rows),
        user_count=len({row.user_id for row, _user in daily_rows}),
        top_exit_page_path=top_exit.page_path if top_exit else None,
        top_exit_feature_code=top_exit.feature_code if top_exit else None,
    )

    return AuditDashboardResponse(
        summary=summary,
        daily_users=[
            AuditDailyUserUsageResponse(
                user=_user_brief(user),
                date=row.date,
                platform=row.platform,
                active_ms=row.active_ms,
                session_count=row.session_count,
                event_count=row.event_count,
                last_exit_page_path=row.last_exit_page_path,
                last_exit_feature_code=row.last_exit_feature_code,
                last_exit_reason=row.last_exit_reason,
                last_seen_at=row.last_seen_at,
            )
            for row, user in daily_rows
        ],
        feature_usage=[
            AuditDailyFeatureUsageResponse(
                user=_user_brief(user),
                date=row.date,
                platform=row.platform,
                feature_code=row.feature_code,
                active_ms=row.active_ms,
                event_count=row.event_count,
                last_seen_at=row.last_seen_at,
            )
            for row, user in feature_rows
        ],
        exit_locations=exit_locations,
    )


async def user_day(db: AsyncSession, user_id: str, day: date) -> AuditUserDayResponse | None:
    user = await db.get(User, user_id)
    if user is None:
        return None

    result = await db.execute(
        select(AuditSession)
        .join(AuditEvent, AuditEvent.session_id == AuditSession.id)
        .where(AuditSession.user_id == user_id, AuditEvent.event_date == day)
        .options(selectinload(AuditSession.events))
        .order_by(AuditSession.started_at)
        .distinct()
    )
    sessions = result.scalars().all()

    return AuditUserDayResponse(
        user=_user_brief(user),
        date=day,
        sessions=[
            AuditSessionResponse(
                id=session.id,
                client_session_id=session.client_session_id,
                platform=session.platform,
                started_at=session.started_at,
                last_seen_at=session.last_seen_at,
                ended_at=session.ended_at,
                active_ms=session.active_ms,
                exit_page_path=session.exit_page_path,
                exit_feature_code=session.exit_feature_code,
                exit_reason=session.exit_reason,
                app_version=session.app_version,
                device_summary=session.device_summary,
                events=[
                    event
                    for event in session.events
                    if event.event_date == day
                ],
            )
            for session in sessions
        ],
    )
