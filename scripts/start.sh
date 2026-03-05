#!/bin/bash
cd "$(dirname "$0")/.."

echo "启动后端服务..."
cd backend
source venv/bin/activate
PYTHONPATH=. uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "后端 PID: $BACKEND_PID"

echo "启动前端服务..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!
echo "前端 PID: $FRONTEND_PID"

echo "$BACKEND_PID" > /tmp/pawtale_backend.pid
echo "$FRONTEND_PID" > /tmp/pawtale_frontend.pid

echo ""
echo "服务已启动:"
echo "  前端: http://localhost:5173"
echo "  后端: http://localhost:8000"
echo "  API 文档: http://localhost:8000/docs"
echo ""
echo "按 Ctrl+C 停止所有服务，或运行 ./scripts/stop.sh"

trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit' INT TERM
wait
