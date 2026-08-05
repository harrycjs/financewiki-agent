@echo off
REM ==========================================
REM   FinanceWiki Agent - 启动所有服务
REM   启动顺序：Redis -> Qdrant -> 后端
REM   每个依赖都有 readiness probe，失败立刻报错退出
REM ==========================================
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM 切到脚本所在目录，确保 backend.main 可被 import
cd /d "%~dp0"

echo 脚本所在目录: %cd%
echo.

REM ---- 可配置路径 ----
set "REDIS_DIR=D:\tools\redis"
set "REDIS_EXE=%REDIS_DIR%\redis-server.exe"
set "REDIS_CONF=%REDIS_DIR%\redis.conf"
set "REDIS_LOG=%REDIS_DIR%\logs\redis.log"
set "REDIS_PORT=6379"

set "QDRANT_DIR=D:\tools\qdrant"
set "QDRANT_EXE=%QDRANT_DIR%\qdrant.exe"
set "QDRANT_CONF=%QDRANT_DIR%\config\config.yaml"
set "QDRANT_HTTP_PORT=6333"

set "BACKEND_HOST=0.0.0.0"
set "BACKEND_PORT=8000"

echo ==========================================
echo   FinanceWiki Agent - 启动所有服务
echo ==========================================
echo.

REM ---- 0. 前置检查 ----
echo [0/4] 检查依赖...
if not exist "%REDIS_EXE%" (
    echo [ERROR] 未找到 redis-server.exe: %REDIS_EXE%
    pause
    exit /b 1
)
if not exist "%QDRANT_EXE%" (
    echo [ERROR] 未找到 qdrant.exe: %QDRANT_EXE%
    pause
    exit /b 1
)
if not exist "%REDIS_CONF%" (
    echo [ERROR] 未找到 redis 配置: %REDIS_CONF%
    pause
    exit /b 1
)
if not exist "%QDRANT_CONF%" (
    echo [ERROR] 未找到 qdrant 配置: %QDRANT_CONF%
    pause
    exit /b 1
)
echo [完成] 依赖检查通过
echo.

REM ---- 工具函数：等待端口就绪 ----
REM  usage: call :wait_port <port> <timeout_sec> <label>
REM  实现：用 Python socket 检测（避免 PowerShell 在 Git Bash 下被补全展开）
:wait_port
set /a waited=0
:wait_loop
python -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',%~1))==0 else 1)" >nul 2>&1
if %errorlevel% equ 0 goto :wait_ok
set /a waited+=1
if %waited% geq %~2 (
    echo [ERROR] %~3 端口 %~1 在 %~2 秒内未就绪
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop
:wait_ok
echo   - 端口 %~1 已就绪（耗时 %waited% 秒）
goto :eof

REM ---- 工具函数：检测端口是否已被占用 ----
REM  usage: call :port_in_use <port> -> errorlevel 0=占用, 1=空闲
:port_in_use
netstat -ano | findstr /R /C:":%1 " >nul 2>&1
goto :eof

REM ---- 1. 启动 Redis ----
echo [1/4] 启动 Redis (端口 %REDIS_PORT%)...
call :port_in_use %REDIS_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %REDIS_PORT% 已被占用，假设 Redis 已在运行
) else (
    if not exist "%REDIS_LOG%" (
        if not exist "%REDIS_DIR%\logs" mkdir "%REDIS_DIR%\logs"
        type nul > "%REDIS_LOG%"
    )
    REM 用 start 打开独立窗口；崩溃时窗口会停留便于排查
    start "FinanceWiki-Redis" /D "%REDIS_DIR%" "%REDIS_EXE%" "%REDIS_CONF%"
    if errorlevel 1 (
        echo [ERROR] Redis 启动失败（退出码 %errorlevel%）
        pause
        exit /b 1
    )
    echo   - Redis 进程已派发，等待端口就绪...
    call :wait_port %REDIS_PORT% 15 Redis
    if errorlevel 1 (
        pause
        exit /b 1
    )

    REM 业务级连通性探针
    "%REDIS_DIR%\redis-cli.exe" -p %REDIS_PORT% PING >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Redis 进程在跑但 PING 不通，请查看日志: %REDIS_LOG%
        pause
        exit /b 1
    )
    echo   - Redis PING 通过
)
echo [完成] Redis
echo.

REM ---- 2. 启动 Qdrant ----
echo [2/4] 启动 Qdrant (HTTP %QDRANT_HTTP_PORT%, gRPC 6334)...
call :port_in_use %QDRANT_HTTP_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %QDRANT_HTTP_PORT% 已被占用，假设 Qdrant 已在运行
) else (
    if not exist "%QDRANT_DIR%\tmp" mkdir "%QDRANT_DIR%\tmp"
    start "FinanceWiki-Qdrant" /D "%QDRANT_DIR%" "%QDRANT_EXE%" --config-path "%QDRANT_CONF%"
    if errorlevel 1 (
        echo [ERROR] Qdrant 启动失败（退出码 %errorlevel%）
        pause
        exit /b 1
    )
    echo   - Qdrant 进程已派发，等待端口就绪...
    call :wait_port %QDRANT_HTTP_PORT% 30 Qdrant
    if errorlevel 1 (
        pause
        exit /b 1
    )

    REM 业务级健康探针
    curl -sf http://127.0.0.1:%QDRANT_HTTP_PORT%/healthz >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Qdrant 端口在听但 /healthz 不通
        pause
        exit /b 1
    )
    echo   - Qdrant /healthz 通过
)
echo [完成] Qdrant
echo.

REM ---- 3. 启动后端 ----
echo [3/4] 启动后端 (端口 %BACKEND_PORT%)...
call :port_in_use %BACKEND_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %BACKEND_PORT% 已被占用，后端已在运行
    goto :backend_done
)

REM 数据目录兜底
if not exist ".\data" mkdir ".\data"
if not exist ".\data\documents" mkdir ".\data\documents"
if not exist ".\logs" mkdir ".\logs"

start "FinanceWiki-Backend" cmd /k "python -m uvicorn backend.main:app --host %BACKEND_HOST% --port %BACKEND_PORT%"
echo   - 后端进程已派发，等待端口就绪...
call :wait_port %BACKEND_PORT% 30 Backend
if errorlevel 1 (
    echo [ERROR] 后端未能在 30 秒内就绪
    pause
    exit /b 1
)

REM 业务级探针
curl -sf http://127.0.0.1:%BACKEND_PORT%/api/health >nul 2>&1
if errorlevel 1 (
    echo [WARN] 后端端口在听但 /api/health 不通，请查看启动窗口
) else (
    echo   - 后端 /api/health 通过
)
:backend_done
echo [完成] 后端
echo.

REM ---- 4. 汇总 ----
echo [4/4] 全部就绪
echo ==========================================
echo   Qdrant:   http://localhost:%QDRANT_HTTP_PORT%
echo   Redis:    localhost:%REDIS_PORT%
echo   Backend:  http://localhost:%BACKEND_PORT%
echo   API 文档: http://localhost:%BACKEND_PORT%/docs
echo.
echo   关闭对应窗口即可停止服务（Redis/Qdrant/Backend 各一个窗口）。
echo   日志位置：
echo     Redis:    %REDIS_LOG%
echo     Qdrant:  启动窗口输出
echo     Backend: 启动窗口输出
echo ==========================================
echo.
echo [完成] 所有服务已启动。按任意键关闭此汇总窗口...
echo       (其他三个服务窗口保持运行)
pause >nul
endlocal
exit /b 0