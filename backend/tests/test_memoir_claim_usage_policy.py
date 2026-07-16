import pytest
from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
)
from app.models.memoir_narrative import MemoirClaim
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.narrative.claim_usage_policy import (
    evaluate_claim_usage,
    known_for_conversation_filters,
    publication_safe_context_summary,
    usable_for_publication_filters,
)


def _claim(
    *,
    statement: str = "讲述者曾经在清流二村居住。",
    verification_status: MemoirClaimVerificationStatus = (
        MemoirClaimVerificationStatus.UNREVIEWED
    ),
    publication_status: MemoirClaimPublicationStatus = (
        MemoirClaimPublicationStatus.ELIGIBLE
    ),
    superseded_by_claim_id: str | None = None,
) -> MemoirClaim:
    return MemoirClaim(
        project_id="claim-policy-project",
        claim_type=MemoirClaimType.EVENT.value,
        statement=statement,
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=verification_status.value,
        publication_status=publication_status.value,
        superseded_by_claim_id=superseded_by_claim_id,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
        updated_by=MemoirNarrativeActorType.SYSTEM.value,
    )


@pytest.mark.parametrize(
    (
        "verification_status",
        "publication_status",
        "superseded_by_claim_id",
        "known_for_conversation",
        "usable_for_publication",
        "retain_as_conflict_context",
    ),
    [
        (
            MemoirClaimVerificationStatus.UNREVIEWED,
            MemoirClaimPublicationStatus.ELIGIBLE,
            None,
            True,
            True,
            False,
        ),
        (
            MemoirClaimVerificationStatus.CONFIRMED,
            MemoirClaimPublicationStatus.PRIVATE,
            None,
            True,
            False,
            False,
        ),
        (
            MemoirClaimVerificationStatus.CONFIRMED,
            MemoirClaimPublicationStatus.EXCLUDED,
            None,
            True,
            False,
            False,
        ),
        (
            MemoirClaimVerificationStatus.REJECTED,
            MemoirClaimPublicationStatus.ELIGIBLE,
            None,
            False,
            False,
            True,
        ),
        (
            MemoirClaimVerificationStatus.DISPUTED,
            MemoirClaimPublicationStatus.ELIGIBLE,
            None,
            True,
            False,
            True,
        ),
        (
            MemoirClaimVerificationStatus.CONFIRMED,
            MemoirClaimPublicationStatus.ELIGIBLE,
            "replacement-claim",
            False,
            False,
            False,
        ),
    ],
)
def test_claim_usage_policy_separates_conversation_and_publication(
    verification_status,
    publication_status,
    superseded_by_claim_id,
    known_for_conversation,
    usable_for_publication,
    retain_as_conflict_context,
):
    usage = evaluate_claim_usage(
        _claim(
            verification_status=verification_status,
            publication_status=publication_status,
            superseded_by_claim_id=superseded_by_claim_id,
        )
    )

    assert usage.known_for_conversation is known_for_conversation
    assert usage.usable_for_publication is usable_for_publication
    assert usage.retain_as_conflict_context is retain_as_conflict_context
    assert usage.successor_claim_id == superseded_by_claim_id


def test_publication_safe_context_summary_hides_restricted_and_ineffective_claims():
    visible = _claim(statement="这是一条可以用于书稿的事实。")
    private = _claim(
        statement="这是一条私密事实。",
        publication_status=MemoirClaimPublicationStatus.PRIVATE,
    )
    rejected = _claim(
        statement="这是一条已被否定的事实。",
        verification_status=MemoirClaimVerificationStatus.REJECTED,
    )
    superseded = _claim(
        statement="这是一条旧版本事实。",
        superseded_by_claim_id="replacement-claim",
    )

    summary = publication_safe_context_summary(
        [visible, private, rejected, superseded],
        story_summary="章节背景。",
    )

    assert summary == "章节背景。；这是一条可以用于书稿的事实。"


async def test_claim_usage_sql_filters_match_object_policy(test_db):
    user = User(
        email="claim-policy@example.com",
        password_hash="hash",
        display_name="Claim Policy User",
    )
    test_db.add(user)
    await test_db.flush()
    project = Project(
        user_id=user.id,
        type="memoir",
        title="Claim policy SQL parity",
    )
    test_db.add(project)
    await test_db.flush()
    claims = [
        _claim(statement="普通有效 Claim"),
        _claim(
            statement="私密 Claim",
            publication_status=MemoirClaimPublicationStatus.PRIVATE,
        ),
        _claim(
            statement="排除 Claim",
            publication_status=MemoirClaimPublicationStatus.EXCLUDED,
        ),
        _claim(
            statement="争议 Claim",
            verification_status=MemoirClaimVerificationStatus.DISPUTED,
        ),
        _claim(
            statement="否定 Claim",
            verification_status=MemoirClaimVerificationStatus.REJECTED,
        ),
    ]
    for claim in claims:
        claim.project_id = project.id
        if (
            claim.publication_status != MemoirClaimPublicationStatus.ELIGIBLE.value
            or claim.verification_status == MemoirClaimVerificationStatus.REJECTED.value
        ):
            claim.updated_by = MemoirNarrativeActorType.USER.value
            claim.updated_by_user_id = user.id
    test_db.add_all(claims)
    await test_db.flush()
    superseded = _claim(
        statement="旧版本 Claim",
        superseded_by_claim_id=claims[0].id,
    )
    superseded.project_id = project.id
    test_db.add(superseded)
    await test_db.flush()
    claims.append(superseded)

    known_ids = set(
        (
            await test_db.scalars(
                select(MemoirClaim.id).where(
                    MemoirClaim.project_id == project.id,
                    *known_for_conversation_filters(),
                )
            )
        ).all()
    )
    publication_ids = set(
        (
            await test_db.scalars(
                select(MemoirClaim.id).where(
                    MemoirClaim.project_id == project.id,
                    *usable_for_publication_filters(),
                )
            )
        ).all()
    )

    assert known_ids == {
        claim.id
        for claim in claims
        if evaluate_claim_usage(claim).known_for_conversation
    }
    assert publication_ids == {
        claim.id
        for claim in claims
        if evaluate_claim_usage(claim).usable_for_publication
    }
