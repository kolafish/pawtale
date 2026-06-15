#!/bin/bash
set -euo pipefail

echo "🧪 Pawtale 前端快速测试"
echo "========================"
echo ""

cd "$(dirname "$0")/../frontend"

echo "📦 检查依赖..."
if [ ! -d "node_modules" ]; then
    echo "❌ node_modules 不存在，请先运行 npm install"
    exit 1
fi
echo "✅ 依赖检查通过"
echo ""

echo "🔍 TypeScript 类型检查..."
if ! npx tsc --noEmit; then
    echo "❌ TypeScript 类型检查失败"
    exit 1
fi
echo "✅ TypeScript 类型检查通过"
echo ""

echo "🧪 前端单元测试..."
if ! npm test; then
    echo "❌ 前端单元测试失败"
    exit 1
fi
echo "✅ 前端单元测试通过"
echo ""

echo "🏗️  Vite 构建测试（不输出文件）..."
if ! npx vite build --mode development --logLevel error; then
    echo "❌ Vite 构建失败"
    exit 1
fi
echo "✅ Vite 构建测试通过"
echo ""

echo "🎉 所有测试通过！前端代码可以正常编译运行"
echo ""
echo "💡 提示：运行 ./scripts/start.sh 启动完整服务"
