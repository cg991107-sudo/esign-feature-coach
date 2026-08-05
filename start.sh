#!/bin/bash
# ============================================
#  e签宝 · 功能价值教练 - 一键启动
#  包含：Flask服务 + 公网隧道
# ============================================

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/Users/chengeng/.workbuddy/binaries/python/envs/esign-coach/bin/python"

echo "=========================================="
echo "  e签宝 · 功能价值教练 启动中..."
echo "=========================================="
echo ""

# 1. 启动 Flask 服务（后台）
echo "[1/2] 启动 Flask 服务..."
$PYTHON "$DIR/app.py" &
FLASK_PID=$!
sleep 3

if curl -s -o /dev/null http://127.0.0.1:5055/ 2>/dev/null; then
    echo "  ✓ 服务已启动 (PID: $FLASK_PID)"
    echo "  本机访问: http://127.0.0.1:5055"
else
    echo "  ✗ Flask 启动失败，请检查 Python 环境"
    exit 1
fi

echo ""
echo "[2/2] 启动公网隧道..."
echo "  连接 localhost.run 中，请稍候..."
echo ""
echo "=========================================="
echo "  ⚠️  此窗口不要关闭！关闭则服务停止！"
echo "  ⚠️  公网URL在下方显示，每次启动会变"
echo "=========================================="
echo ""

# 2. 启动 SSH 隧道（前台运行，显示公网URL）
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:localhost:5055 nokey@localhost.run

# SSH 断开后清理
echo ""
echo "隧道已断开，正在关闭 Flask 服务..."
kill $FLASK_PID 2>/dev/null
echo "已停止。重新运行此脚本即可恢复。"
