#!/bin/bash

echo "停止 Pawtale 服务..."

# 从 PID 文件停止
if [ -f /tmp/pawtale_backend.pid ]; then
    kill $(cat /tmp/pawtale_backend.pid) 2>/dev/null && echo "后端已停止"
    rm /tmp/pawtale_backend.pid
fi

if [ -f /tmp/pawtale_frontend.pid ]; then
    kill $(cat /tmp/pawtale_frontend.pid) 2>/dev/null && echo "前端已停止"
    rm /tmp/pawtale_frontend.pid
fi

# 确保清理所有相关进程
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "vite.*frontend" 2>/dev/null

echo "所有服务已停止"
