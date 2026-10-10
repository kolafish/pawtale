"""Upgrade topic maps to life topics v2 and drop the unused topic defer columns.

v2 adds "家族与出身", renames "晚年与现在" to "如今的生活" and widens a few
evidence hints. Existing items, matches and user resolutions are kept; the new
item starts with no material.

Revision ID: a3d9f6c2e817
Revises: e4b7c2a9d815
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision = "a3d9f6c2e817"
down_revision = "e4b7c2a9d815"
branch_labels = None
depends_on = None

TEMPLATE_KEY = "memoir_life_topics"
NEW_ITEM_KEY = "family_and_origins"

LIFE_TOPICS_V1 = {
    "template_key": "memoir_life_topics",
    "version": "1",
    "modules": [
        {
            "module_key": "life_stage",
            "title": "人生阶段",
            "sort_order": 10
        },
        {
            "module_key": "people",
            "title": "重要人物",
            "sort_order": 20
        },
        {
            "module_key": "experience",
            "title": "经历与故事",
            "sort_order": 30
        },
        {
            "module_key": "place",
            "title": "地方与生活",
            "sort_order": 40
        },
        {
            "module_key": "theme",
            "title": "感受与传承",
            "sort_order": 50
        }
    ],
    "items": [
        {
            "item_key": "childhood_and_growth",
            "module_key": "life_stage",
            "title": "童年与成长",
            "intent": "童年生活、家庭环境、成长经历和早年的重要记忆",
            "evidence_hints": ["童年", "小时候", "成长", "父母", "兄弟姐妹", "老家"],
            "sort_order": 10
        },
        {
            "item_key": "school_and_learning",
            "module_key": "life_stage",
            "title": "求学与学本事",
            "intent": "上学、求学、拜师、学手艺以及学习带来的改变",
            "evidence_hints": ["学校", "老师", "同学", "读书", "考试", "学徒", "手艺"],
            "sort_order": 20
        },
        {
            "item_key": "work_and_career",
            "module_key": "life_stage",
            "title": "工作与事业",
            "intent": "工作经历、职业变化、同事关系、成就和工作中的困难",
            "evidence_hints": ["工作", "单位", "同事", "领导", "职业", "生意", "退休"],
            "sort_order": 30
        },
        {
            "item_key": "marriage_and_family_life",
            "module_key": "life_stage",
            "title": "婚姻与家庭生活",
            "intent": "伴侣、婚姻、成家、养育子女和家庭共同生活",
            "evidence_hints": ["结婚", "伴侣", "丈夫", "妻子", "孩子", "成家", "家庭生活"],
            "sort_order": 40
        },
        {
            "item_key": "later_life_and_present",
            "module_key": "life_stage",
            "title": "晚年与现在",
            "intent": "晚年生活、退休以后、当下的日常以及回望人生的感受",
            "evidence_hints": ["晚年", "退休", "现在", "如今", "老年", "回望"],
            "sort_order": 50
        },
        {
            "item_key": "important_family_members",
            "module_key": "people",
            "title": "重要的家人",
            "intent": "对自己影响深、关系重要或值得记住的家人",
            "evidence_hints": ["父母", "祖父母", "外祖父母", "兄弟姐妹", "亲人", "家人"],
            "sort_order": 60
        },
        {
            "item_key": "mentors_friends_and_helpers",
            "module_key": "people",
            "title": "师友与帮助过自己的人",
            "intent": "老师、朋友、同学、同事、师傅以及帮助或影响过自己的人",
            "evidence_hints": ["老师", "朋友", "同学", "同事", "师傅", "恩人", "帮助"],
            "sort_order": 70
        },
        {
            "item_key": "turning_points_and_choices",
            "module_key": "experience",
            "title": "人生转折与选择",
            "intent": "改变人生方向的重要事件、决定、相遇、迁移或机会",
            "evidence_hints": ["转折", "决定", "选择", "搬家", "离开", "机会", "改变"],
            "sort_order": 80
        },
        {
            "item_key": "hardship_and_resilience",
            "module_key": "experience",
            "title": "困难与坚持",
            "intent": "疾病、失去、挫折、压力、困难时期以及如何度过",
            "evidence_hints": ["困难", "生病", "失去", "挫折", "辛苦", "坚持", "熬过"],
            "sort_order": 90
        },
        {
            "item_key": "memorable_everyday_life",
            "module_key": "experience",
            "title": "值得记住的日常",
            "intent": "看似平常但有个人意味的生活片段、习惯、节日、饭桌和相处时刻",
            "evidence_hints": ["日常", "吃饭", "过年", "习惯", "家务", "邻里", "小事"],
            "sort_order": 100
        },
        {
            "item_key": "hometown_and_meaningful_places",
            "module_key": "place",
            "title": "故乡与重要地点",
            "intent": "故乡、住过的家、学校、工作地点以及承载记忆的重要地方",
            "evidence_hints": ["故乡", "老家", "院子", "村子", "城市", "学校", "单位", "地方"],
            "sort_order": 110
        },
        {
            "item_key": "values_interests_and_legacy",
            "module_key": "theme",
            "title": "相信的事与想留下的话",
            "intent": "价值观、兴趣爱好、生活经验、家风以及想留给家人的话",
            "evidence_hints": ["相信", "道理", "爱好", "家风", "经验", "希望", "留给"],
            "sort_order": 120
        }
    ]
}


def _life_topics_v2() -> dict:
    template = copy.deepcopy(LIFE_TOPICS_V1)
    template["version"] = "2"
    items = {item["item_key"]: item for item in template["items"]}
    items["work_and_career"]["evidence_hints"] = ["工作", "单位", "同事", "领导", "职业", "生意"]
    items["later_life_and_present"].update(
        title="如今的生活",
        intent="现在的日常、身体和心境、和家人的相处，以及回望走过的路的感受",
        evidence_hints=["现在", "如今", "这几年", "退休", "晚年", "日常", "身体", "回望"],
    )
    items["turning_points_and_choices"]["evidence_hints"] += [
        "下乡", "参军", "高考", "进城打工", "外出闯荡",
    ]
    template["items"].insert(
        0,
        {
            "item_key": NEW_ITEM_KEY,
            "module_key": "life_stage",
            "title": "家族与出身",
            "intent": "祖辈和家族从哪里来、家里的境况、父母年轻时的事以及自己出生前后的家",
            "evidence_hints": ["祖籍", "祖上", "家族", "祖辈", "爷爷", "外公", "家境", "出生"],
            "sort_order": 5,
        },
    )
    return template


def _template_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _set_template(bind, *, from_version: str, template: dict) -> list[str]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    framework_ids = list(
        bind.execute(
            sa.text(
                "SELECT id FROM memoir_project_frameworks "
                "WHERE template_key = :key AND template_version = :version"
            ),
            {"key": TEMPLATE_KEY, "version": from_version},
        ).scalars()
    )
    if framework_ids:
        bind.execute(
            sa.text(
                "UPDATE memoir_project_frameworks SET template_version = :version, "
                "template_snapshot_json = CAST(:snapshot AS JSON), template_hash = :hash, "
                "updated_at = :now WHERE id = ANY(:ids)"
            ),
            {
                "version": template["version"],
                "snapshot": json.dumps(template, ensure_ascii=False),
                "hash": _template_hash(template),
                "now": now,
                "ids": framework_ids,
            },
        )
    return framework_ids


def upgrade() -> None:
    bind = op.get_bind()
    framework_ids = _set_template(bind, from_version="1", template=_life_topics_v2())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for framework_id in framework_ids:
        bind.execute(
            sa.text(
                "INSERT INTO memoir_framework_items (id, project_framework_id, item_key, "
                "module_key, sort_order, evidence_state, user_resolution, assessment_summary, "
                "assessment_version, user_resolution_note, deferred_until, defer_reason, updated_at) "
                "VALUES (:id, :framework_id, :item_key, 'life_stage', 5, 'none', 'none', '', '', '', "
                "NULL, '', :now)"
            ),
            {"id": str(uuid.uuid4()), "framework_id": framework_id, "item_key": NEW_ITEM_KEY, "now": now},
        )
    op.drop_constraint("ck_memoir_framework_items_defer_shape", "memoir_framework_items", type_="check")
    op.drop_column("memoir_framework_items", "deferred_until")
    op.drop_column("memoir_framework_items", "defer_reason")


def downgrade() -> None:
    op.add_column(
        "memoir_framework_items",
        sa.Column("defer_reason", sa.Text(), server_default="", nullable=False),
    )
    op.alter_column("memoir_framework_items", "defer_reason", server_default=None)
    op.add_column("memoir_framework_items", sa.Column("deferred_until", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_memoir_framework_items_defer_shape",
        "memoir_framework_items",
        "(deferred_until IS NULL AND length(trim(defer_reason)) = 0) OR "
        "(deferred_until IS NOT NULL AND length(trim(defer_reason)) > 0)",
    )
    bind = op.get_bind()
    framework_ids = _set_template(bind, from_version="2", template=LIFE_TOPICS_V1)
    if framework_ids:
        bind.execute(
            sa.text(
                "DELETE FROM memoir_framework_items "
                "WHERE project_framework_id = ANY(:ids) AND item_key = :item_key"
            ),
            {"ids": framework_ids, "item_key": NEW_ITEM_KEY},
        )
