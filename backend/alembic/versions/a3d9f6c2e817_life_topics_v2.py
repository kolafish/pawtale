"""Move topic maps to the seven life-story topics and drop the unused topic defer columns.

Topics that continue in v2 keep their matches and user resolutions. The six
cross-cutting v1 topics (family, mentors, turning points, hardship, everyday
life, places) are removed with their matches, and "家世与出身" starts empty.
Old narrations are not matched again.

Revision ID: a3d9f6c2e817
Revises: e4b7c2a9d815
"""
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

LIFE_TOPICS_V2 = {
    "template_key": "memoir_life_topics",
    "version": "2",
    "modules": [
        {
            "module_key": "life_story",
            "title": "人生经历",
            "sort_order": 10
        }
    ],
    "items": [
        {
            "item_key": "family_and_origins",
            "module_key": "life_story",
            "title": "家世与出身",
            "intent": "祖辈和父母是怎样的人，家从哪里来，自己出生时家里的光景",
            "sort_order": 10
        },
        {
            "item_key": "childhood_and_growth",
            "module_key": "life_story",
            "title": "童年",
            "intent": "小时候住在哪、跟谁一起过，记得最清楚的人和事",
            "sort_order": 20
        },
        {
            "item_key": "school_and_learning",
            "module_key": "life_story",
            "title": "求学与少年",
            "intent": "上学、学本事的那些年，遇到的老师和伙伴",
            "sort_order": 30
        },
        {
            "item_key": "work_and_career",
            "module_key": "life_story",
            "title": "工作与闯荡",
            "intent": "离开家以后怎么谋生，做过哪些事，去过哪些地方",
            "sort_order": 40
        },
        {
            "item_key": "marriage_and_family_life",
            "module_key": "life_story",
            "title": "成家与儿女",
            "intent": "怎么遇到另一半，成家过日子，把孩子拉扯大",
            "sort_order": 50
        },
        {
            "item_key": "later_life_and_present",
            "module_key": "life_story",
            "title": "如今的生活",
            "intent": "现在的日子、身体和心境，回头看走过的路",
            "sort_order": 60
        },
        {
            "item_key": "values_interests_and_legacy",
            "module_key": "life_story",
            "title": "想留下的话",
            "intent": "信的道理、攒下的经验，想对家人和后辈说的话",
            "sort_order": 70
        }
    ]
}


def _template_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _move_frameworks(*, source: dict, target: dict) -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    framework_ids = list(
        bind.execute(
            sa.text(
                "SELECT id FROM memoir_project_frameworks "
                "WHERE template_key = :key AND template_version = :version"
            ),
            {"key": TEMPLATE_KEY, "version": source["version"]},
        ).scalars()
    )
    if not framework_ids:
        return
    bind.execute(
        sa.text(
            "UPDATE memoir_project_frameworks SET template_version = :version, "
            "template_snapshot_json = CAST(:snapshot AS JSON), template_hash = :hash, "
            "updated_at = :now WHERE id = ANY(:ids)"
        ),
        {
            "version": target["version"],
            "snapshot": json.dumps(target, ensure_ascii=False),
            "hash": _template_hash(target),
            "now": now,
            "ids": framework_ids,
        },
    )
    target_items = {item["item_key"]: item for item in target["items"]}
    removed = [item["item_key"] for item in source["items"] if item["item_key"] not in target_items]
    # Item materials cascade; voice sessions and utterances anchored to a removed item are set to null.
    bind.execute(
        sa.text(
            "DELETE FROM memoir_framework_items "
            "WHERE project_framework_id = ANY(:ids) AND item_key = ANY(:keys)"
        ),
        {"ids": framework_ids, "keys": removed},
    )
    source_keys = {item["item_key"] for item in source["items"]}
    for item in target["items"]:
        if item["item_key"] in source_keys:
            bind.execute(
                sa.text(
                    "UPDATE memoir_framework_items SET module_key = :module_key, sort_order = :sort_order "
                    "WHERE project_framework_id = ANY(:ids) AND item_key = :item_key"
                ),
                {"module_key": item["module_key"], "sort_order": item["sort_order"], "ids": framework_ids,
                  "item_key": item["item_key"]},
            )
            continue
        for framework_id in framework_ids:
            bind.execute(
                sa.text(
                    "INSERT INTO memoir_framework_items (id, project_framework_id, item_key, module_key, "
                    "sort_order, evidence_state, user_resolution, assessment_summary, assessment_version, "
                    "user_resolution_note, updated_at) VALUES (:id, :framework_id, :item_key, :module_key, "
                    ":sort_order, 'none', 'none', '', '', '', :now)"
                ),
                {"id": str(uuid.uuid4()), "framework_id": framework_id, "item_key": item["item_key"],
                  "module_key": item["module_key"], "sort_order": item["sort_order"], "now": now},
            )


def upgrade() -> None:
    op.drop_constraint("ck_memoir_framework_items_defer_shape", "memoir_framework_items", type_="check")
    op.drop_column("memoir_framework_items", "deferred_until")
    op.drop_column("memoir_framework_items", "defer_reason")
    _move_frameworks(source=LIFE_TOPICS_V1, target=LIFE_TOPICS_V2)


def downgrade() -> None:
    _move_frameworks(source=LIFE_TOPICS_V2, target=LIFE_TOPICS_V1)
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
