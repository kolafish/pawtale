from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.framework_templates import FrameworkTemplateItemDefinition
from app.domain.memoir.narrative_types import (
    MemoirFrameworkEvidenceState,
    MemoirFrameworkUserResolution,
)
from app.models.memoir_narrative import MemoirFrameworkItem
from app.schemas.memoir_topic_map import (
    MemoirTopicMapItemResponse,
    MemoirTopicMapModuleResponse,
    MemoirTopicMapResponse,
)
from app.services.memoir.narrative import frameworks


def _item_response(
    item: MemoirFrameworkItem,
    definition: FrameworkTemplateItemDefinition,
    material_count: int,
) -> MemoirTopicMapItemResponse:
    resolution = MemoirFrameworkUserResolution(item.user_resolution)
    evidence_state = MemoirFrameworkEvidenceState(item.evidence_state)
    level = frameworks.topic_level(material_count)
    display = {
        MemoirFrameworkUserResolution.ENOUGH_FOR_NOW: (
            "enough_for_now",
            "目前讲这些就够了",
            "这是您的选择，以后可以随时撤销。",
        ),
        MemoirFrameworkUserResolution.NOT_APPLICABLE: (
            "not_applicable",
            "不适合我的经历",
            "这个方向不会被当成您需要补讲的内容。",
        ),
        MemoirFrameworkUserResolution.DO_NOT_DISCUSS: (
            "do_not_discuss",
            "不想聊这个",
            "这个方向会被收起，撤销前不会作为新的话题方向。",
        ),
    }.get(resolution)
    if display is None:
        display = {
            frameworks.FrameworkTopicLevel.COVERED: (
                level.value,
                "聊了不少",
                "这里已经有不少讲述，想起更多也可以接着讲。",
            ),
            frameworks.FrameworkTopicLevel.TOUCHED: (
                level.value,
                "聊过一点",
                "已经讲过一些，想起更多可以接着讲。",
            ),
            frameworks.FrameworkTopicLevel.NOT_STARTED: (
                level.value,
                "还没聊",
                "想讲时再讲，不要求每个方向都讲到。",
            ),
        }[level]
    display_state, state_text, state_detail = display
    return MemoirTopicMapItemResponse(
        id=item.id,
        item_key=item.item_key,
        title=definition.title,
        intent=definition.intent,
        evidence_state=evidence_state,
        user_resolution=resolution,
        display_state=display_state,
        state_text=state_text,
        state_detail=state_detail,
        resolved_at=item.resolved_at,
    )


async def get_project_topic_map(
    db: AsyncSession,
    *,
    project_id: str,
) -> MemoirTopicMapResponse:
    project_framework = await frameworks.framework_for_project(
        db,
        project_id=project_id,
    )
    if project_framework is None:
        raise ValueError("topic map is not initialized for the project")
    template = frameworks.template_for_framework(project_framework)
    items_by_key = {item.item_key: item for item in project_framework.items}
    material_counts = await frameworks.primary_material_counts(
        db,
        project_framework_id=project_framework.id,
    )
    definitions_by_module: dict[str, list[FrameworkTemplateItemDefinition]] = {}
    for definition in template.items:
        definitions_by_module.setdefault(definition.module_key, []).append(definition)
    return MemoirTopicMapResponse(
        project_id=project_id,
        template_key=project_framework.template_key,
        template_version=project_framework.template_version,
        headline="人生话题",
        detail="它不是章节目录，也没有必须全部讲完。想起什么就讲什么，您的选择随时可以改。",
        modules=[
            MemoirTopicMapModuleResponse(
                module_key=module.module_key,
                title=module.title,
                items=[
                    _item_response(
                        items_by_key[definition.item_key],
                        definition,
                        material_counts.get(items_by_key[definition.item_key].id, 0),
                    )
                    for definition in sorted(
                        definitions_by_module.get(module.module_key, []),
                        key=lambda definition: definition.sort_order,
                    )
                ],
            )
            for module in sorted(template.modules, key=lambda module: module.sort_order)
        ],
    )


async def update_user_resolution(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
    user_resolution: MemoirFrameworkUserResolution,
    actor_user_id: str,
) -> MemoirTopicMapItemResponse:
    item = await frameworks.set_framework_user_resolution(
        db,
        project_id=project_id,
        item_id=item_id,
        user_resolution=user_resolution,
        actor_user_id=actor_user_id,
    )
    project_framework = await frameworks.framework_for_project(
        db,
        project_id=project_id,
    )
    if project_framework is None:
        raise ValueError("topic map is not initialized for the project")
    template = frameworks.template_for_framework(project_framework)
    definition = next(
        definition
        for definition in template.items
        if definition.item_key == item.item_key
    )
    material_counts = await frameworks.primary_material_counts(
        db,
        project_framework_id=project_framework.id,
    )
    return _item_response(item, definition, material_counts.get(item.id, 0))
