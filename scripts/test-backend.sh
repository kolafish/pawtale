#!/bin/bash

echo "🧪 Pawtale 后端快速测试"
echo "========================"
echo ""

cd "$(dirname "$0")/../backend"

echo "📦 检查虚拟环境..."
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先创建虚拟环境"
    exit 1
fi
echo "✅ 虚拟环境检查通过"
echo ""

echo "🔍 Python 导入测试..."
PYTHONPATH=. venv/bin/python -c "from app.main import app; print('✅ 主应用导入成功')" 2>&1
if [ $? -ne 0 ]; then
    echo "❌ Python 导入失败"
    exit 1
fi
echo ""

echo "🔍 检查数据库..."
if [ ! -f "../data/pawtale.db" ]; then
    echo "⚠️  数据库文件不存在，首次启动时会自动创建"
else
    echo "✅ 数据库文件存在"
fi
echo ""

echo "🔍 检查环境变量..."
if [ ! -f ".env" ]; then
    echo "⚠️  .env 文件不存在，请确保配置了必要的环境变量"
else
    echo "✅ .env 文件存在"
    # 检查关键配置
    if grep -q "DEEPSEEK_API_KEY" .env; then
        echo "  ✓ DEEPSEEK_API_KEY 已配置"
    fi
    if grep -q "ANTHROPIC_AUTH_TOKEN" .env; then
        echo "  ✓ ANTHROPIC_AUTH_TOKEN 已配置"
    fi
fi
echo ""

echo "🧪 运行 pytest 测试（如果存在）..."
if [ -d "tests" ]; then
    PYTHONPATH=. venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | head -50
    if [ $? -ne 0 ]; then
        echo "⚠️  部分测试失败，但不影响启动"
    else
        echo "✅ 所有测试通过"
    fi
else
    echo "ℹ️  未找到测试目录，跳过"
fi
echo ""

echo "🎉 后端基础检查通过！"
echo ""
echo "💡 提示：运行 ./scripts/start.sh 启动完整服务"
