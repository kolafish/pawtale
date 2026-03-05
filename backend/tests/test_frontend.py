import pytest
import subprocess
import os
from pathlib import Path


class TestFrontendCompilation:
    """前端编译测试"""

    @pytest.fixture(scope="class")
    def frontend_dir(self):
        """获取前端目录路径"""
        backend_dir = Path(__file__).parent.parent
        frontend_dir = backend_dir.parent / "frontend"
        return str(frontend_dir)

    def test_typescript_type_check(self, frontend_dir):
        """测试 TypeScript 类型检查"""
        result = subprocess.run(
            ["npx", "tsc", "-b"],
            cwd=frontend_dir,
            capture_output=True,
            text=True,
            timeout=60
        )

        assert result.returncode == 0, (
            f"TypeScript 类型检查失败:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_vite_build(self, frontend_dir):
        """测试 Vite 构建"""
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend_dir,
            capture_output=True,
            text=True,
            timeout=120
        )

        assert result.returncode == 0, (
            f"Vite 构建失败:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # 检查构建产物是否存在
        dist_dir = Path(frontend_dir) / "dist"
        assert dist_dir.exists(), "构建产物目录 dist 不存在"
        assert (dist_dir / "index.html").exists(), "构建产物 index.html 不存在"
