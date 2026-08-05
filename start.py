"""
启动脚本
"""
import subprocess
import sys
import os
import time
import signal


def check_docker():
    """检查Docker是否运行"""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def start_services():
    """启动Docker服务"""
    print("🚀 启动Qdrant和Redis服务...")

    if not check_docker():
        print("❌ Docker未运行，请先启动Docker")
        return False

    # 启动服务
    subprocess.run(["docker-compose", "up", "-d"], check=True)

    # 等待服务启动
    print("⏳ 等待服务启动...")
    time.sleep(5)

    print("✅ 服务启动完成")
    return True


def install_dependencies():
    """安装Python依赖"""
    print("📦 安装Python依赖...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    print("✅ 依赖安装完成")


def start_backend():
    """启动后端服务"""
    print("🚀 启动后端服务...")

    # 设置环境变量
    os.environ["DEEPSEEK_API_KEY"] = "sk-fd4df3956bb54948a969b7e7b0056997"
    os.environ["DEEPSEEK_API_BASE"] = "https://api.deepseek.com"

    # 启动FastAPI
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    print("✅ 后端服务已启动")
    print("📊 访问地址: http://localhost:8000")
    print("📚 API文档: http://localhost:8000/docs")

    return process


def main():
    """主函数"""
    print("=" * 60)
    print("  FinanceWiki Agent - 金融投研知识库问答系统")
    print("=" * 60)
    print()

    # 切换到项目根目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # 启动Docker服务
    if not start_services():
        return

    # 安装依赖
    install_dependencies()

    # 启动后端
    backend_process = start_backend()

    try:
        # 等待用户中断
        print()
        print("=" * 60)
        print("  服务已启动，按 Ctrl+C 停止")
        print("=" * 60)
        backend_process.wait()
    except KeyboardInterrupt:
        print("\n🛑 正在停止服务...")
        backend_process.terminate()
        subprocess.run(["docker-compose", "down"])
        print("✅ 服务已停止")


if __name__ == "__main__":
    main()
