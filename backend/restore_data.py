import asyncio
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.database import get_db, engine
from app.models import Base, Character, Scene, Story, StoryCharacter
from sqlalchemy.ext.asyncio import AsyncSession


async def restore_data():
    """从备份文件恢复数据"""

    # 读取备份文件
    backup_file = Path(__file__).parent.parent / "data" / "backup_20260210_211708.json"
    with open(backup_file, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)

    # 创建数据库表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建会话
    async with AsyncSession(engine) as db:
        # 恢复角色
        print("恢复角色...")
        for char_data in backup_data['characters']:
            # 解析personality字段（从JSON字符串转为列表）
            personality = json.loads(char_data['personality']) if isinstance(char_data['personality'], str) else char_data['personality']

            character = Character(
                id=char_data['id'],
                name=char_data['name'],
                gender=char_data['gender'],
                age=char_data['age'],
                identity=char_data['identity'],
                appearance=char_data['appearance'],
                personality=personality,
                speaking_style=char_data['speaking_style'],
                background=char_data['background'],
                inspiration=char_data['inspiration'],
                avatar_url=char_data['avatar_url'],
                base_image_url=char_data['base_image_url'],
                is_preset=bool(char_data['is_preset']),
            )
            db.add(character)
            print(f"  - {character.name}")

        # 恢复场景
        print("\n恢复场景...")
        for scene_data in backup_data['scenes']:
            # 解析mood_keywords字段
            mood_keywords = json.loads(scene_data['mood_keywords']) if isinstance(scene_data['mood_keywords'], str) else scene_data['mood_keywords']

            scene = Scene(
                id=scene_data['id'],
                name=scene_data['name'],
                category=scene_data['category'],
                description=scene_data['description'],
                location=scene_data['location'],
                time_weather=scene_data['time_weather'],
                mood_keywords=mood_keywords,
                is_preset=bool(scene_data['is_preset']),
            )
            db.add(scene)
            print(f"  - {scene.name}")

        # 恢复故事
        print("\n恢复故事...")
        for story_data in backup_data['stories']:
            # 解析nodes和current_path字段
            nodes = json.loads(story_data['nodes']) if isinstance(story_data['nodes'], str) else story_data['nodes']
            current_path = json.loads(story_data['current_path']) if isinstance(story_data['current_path'], str) else story_data['current_path']

            story = Story(
                id=story_data['id'],
                title=story_data['title'],
                scene_id=story_data['scene_id'],
                mood=story_data['mood'],
                writing_style=story_data['writing_style'],
                chapter_length=story_data['chapter_length'],
                ai_model='claude-opus-4',  # 默认使用 Claude Opus 4.6
                nodes=nodes,
                current_node_id=story_data['current_node_id'],
                current_path=current_path,
                is_favorite=bool(story_data['is_favorite']),
            )
            db.add(story)
            print(f"  - {story.title}")

        # 恢复故事-角色关联
        print("\n恢复故事-角色关联...")
        for sc_data in backup_data['story_characters']:
            story_char = StoryCharacter(
                story_id=sc_data['story_id'],
                character_id=sc_data['character_id'],
            )
            db.add(story_char)

        # 提交所有更改
        await db.commit()
        print("\n✅ 数据恢复完成！")
        print(f"  - 角色: {len(backup_data['characters'])} 个")
        print(f"  - 场景: {len(backup_data['scenes'])} 个")
        print(f"  - 故事: {len(backup_data['stories'])} 个")


if __name__ == "__main__":
    asyncio.run(restore_data())
