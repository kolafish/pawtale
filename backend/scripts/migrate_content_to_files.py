"""
一次性迁移脚本：将 documents.content 从数据库迁移到文件系统。

运行方式：
  PYTHONPATH=backend backend/venv/bin/python backend/scripts/migrate_content_to_files.py

迁移后文件布局：
  data/projects/{project_id}/{document_id}.md
"""
import re
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DATA_DIR / "pawtale.db"
PROJECTS_DIR = DATA_DIR / "projects"


def count_words(text: str) -> int:
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z]+', text))
    return chinese + english


def main():
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Check if content column exists
    cursor.execute("PRAGMA table_info(documents)")
    columns = {row[1] for row in cursor.fetchall()}
    if "content" not in columns:
        print("documents 表没有 content 列，无需迁移")
        conn.close()
        return

    # Ensure word_count column exists
    if "word_count" not in columns:
        cursor.execute("ALTER TABLE documents ADD COLUMN word_count INTEGER DEFAULT 0")
        conn.commit()
        print("已添加 word_count 列")

    # Query all documents with their project_id
    cursor.execute("""
        SELECT d.id, d.content, dg.project_id
        FROM documents d
        JOIN document_groups dg ON d.group_id = dg.id
    """)
    rows = cursor.fetchall()

    migrated = 0
    skipped = 0
    for doc_id, content, project_id in rows:
        content = content or ""
        project_dir = PROJECTS_DIR / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        file_path = project_dir / f"{doc_id}.md"
        if file_path.exists():
            skipped += 1
            continue

        file_path.write_text(content, encoding="utf-8")

        # Update word_count in DB
        wc = count_words(content)
        cursor.execute("UPDATE documents SET word_count = ? WHERE id = ?", (wc, doc_id))
        migrated += 1

    conn.commit()
    conn.close()

    print(f"迁移完成: {migrated} 个文档已导出为文件, {skipped} 个已存在跳过")
    print(f"文件位置: {PROJECTS_DIR}")


if __name__ == "__main__":
    main()
