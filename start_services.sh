#!/bin/bash

echo "==================================="
echo " 金融投研知识库问答Agent - 服务启动"
echo "==================================="

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "❌ Docker未安装，请先安装Docker"
    echo "   Windows: https://docs.docker.com/desktop/install/windows-install/"
    echo "   macOS: https://docs.docker.com/desktop/install/mac-install/"
    echo "   Linux: https://docs.docker.com/engine/install/"
    exit 1
fi

# 检查Docker Compose是否安装
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose未安装"
    exit 1
fi

echo ""
echo "🚀 启动Qdrant和Redis服务..."
echo ""

# 启动服务
docker-compose up -d

echo ""
echo "✅ 服务启动完成！"
echo ""
echo "📊 服务状态:"
echo "   - Qdrant: http://localhost:6333"
echo "   - Redis:  localhost:6379"
echo ""
echo "📝 下一步:"
echo "   1. 安装Python依赖: pip install -r requirements.txt"
echo "   2. 配置API Key: 复制.env.example为.env并填写"
echo "   3. 启动应用: python backend/main.py"
echo ""
echo "🔧 常用命令:"
echo "   - 查看日志: docker-compose logs -f"
echo "   - 停止服务: docker-compose down"
echo "   - 重启服务: docker-compose restart"
echo "   - 查看状态: docker-compose ps"
