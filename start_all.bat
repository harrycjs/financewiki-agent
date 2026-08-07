@echo off
REM ==========================================
REM   FinanceWiki Agent - start all services
REM   Order: Redis -> Qdrant -> Backend -> Frontend -> browser
REM   Each dep has readiness probe; fail-fast on error
REM ==========================================
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM cd to script dir so backend.main can be imported
cd /d "%~dp0"

echo 脚本所在目录: %cd%
echo.

REM ---- configurable paths ----
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

for %%I in ("%~dp0frontend") do set "FRONTEND_DIR=%%~fI"
set "FRONTEND_PORT=3000"

echo ==========================================
echo   FinanceWiki Agent - 启动所有服务
echo ==========================================
echo.

REM ---- 0. preflight checks ----
echo [0/5] 检查依赖...
if not exist "%REDIS_EXE%" (
    echo [ERROR] redis-server.exe not found: %REDIS_EXE%
    pause
    exit /b 1
)
if not exist "%QDRANT_EXE%" (
    echo [ERROR] qdrant.exe not found: %QDRANT_EXE%
    pause
    exit /b 1
)
if not exist "%REDIS_CONF%" (
    echo [ERROR] redis config not found: %REDIS_CONF%
    pause
    exit /b 1
)
if not exist "%QDRANT_CONF%" (
    echo [ERROR] qdrant config not found: %QDRANT_CONF%
    pause
    exit /b 1
)
if not exist "%FRONTEND_DIR%\package.json" (
    echo [WARN] frontend dir not found: %FRONTEND_DIR%, will skip frontend step
    set "FRONTEND_DIR="
)
echo [完成] 依赖检查通过
echo.
goto :main

REM ---- utility: wait for port to be ready ----
REM  usage: call :wait_port <port> <timeout_sec> <label>
REM  impl: Python socket probe (avoids PowerShell path-expansion in Git Bash)
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
REM Absolute path: avoid Git Bash GNU timeout.exe being picked first
"%SystemRoot%\System32\timeout.exe" /t 1 /nobreak >nul 2>&1
goto :wait_loop
:wait_ok
echo   - 端口 %~1 已就绪（耗时 %waited% 秒）
goto :eof

REM ---- utility: check if port is in use ----
REM  usage: call :port_in_use <port> -> errorlevel 0=in-use, 1=free
:port_in_use
REM LISTENING.*:port pattern avoids false-positive on outbound ESTABLISHED
netstat -ano | findstr /R /C:"LISTENING.*:%1 " >nul 2>&1
goto :eof

REM ---- 1. 启动 Redis ----
:main
echo [1/5] 启动 Redis (端口 %REDIS_PORT%)...
call :port_in_use %REDIS_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %REDIS_PORT% 已被占用，假设 Redis 已在运行
) else (
    if not exist "%REDIS_LOG%" (
        if not exist "%REDIS_DIR%\logs" mkdir "%REDIS_DIR%\logs"
        type nul > "%REDIS_LOG%"
    )
    REM 用 start 打开独立窗口；崩溃时窗口会停留便于排查
    REM Do NOT check errorlevel after start here: :port_in_use used a pipe,
    REM which makes the next start wrongly report errorlevel=1 (cmd.exe quirk).
    REM Real readiness is verified by :wait_port below.
    start "FinanceWiki-Redis" /D "%REDIS_DIR%" "%REDIS_EXE%" "%REDIS_CONF%"
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
echo [2/5] 启动 Qdrant (HTTP %QDRANT_HTTP_PORT%, gRPC 6334)...
call :port_in_use %QDRANT_HTTP_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %QDRANT_HTTP_PORT% 已被占用，假设 Qdrant 已在运行
) else (
    if not exist "%QDRANT_DIR%\tmp" mkdir "%QDRANT_DIR%\tmp"
    REM Same pipe-after-port-check quirk as Redis, rely on :wait_port
    start "FinanceWiki-Qdrant" /D "%QDRANT_DIR%" "%QDRANT_EXE%" --config-path "%QDRANT_CONF%"
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
echo [3/5] 启动后端 (端口 %BACKEND_PORT%)...
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

REM ---- 4. 启动前端 ----
echo [4/5] 启动前端 (端口 %FRONTEND_PORT%)...
if not defined FRONTEND_DIR (
    echo   - 前端目录未配置，跳过
    goto :frontend_done
)
call :port_in_use %FRONTEND_PORT%
if %errorlevel% equ 0 (
    echo   - 端口 %FRONTEND_PORT% 已被占用，前端已在运行
    goto :frontend_done
)
if not exist "%FRONTEND_DIR%\node_modules" (
    echo   - 首次启动：安装前端依赖（可能需要几分钟）...
    start "FinanceWiki-Frontend-Install" /WAIT /D "%FRONTEND_DIR%" cmd /S /c "npm install --no-audit --no-fund || (echo [ERROR] npm install 失败 ^& pause ^& exit /b 1)"
    if errorlevel 1 (
        echo [ERROR] 前端依赖安装失败
        pause
        exit /b 1
    )
    echo   - 依赖安装完成
)

start "FinanceWiki-Frontend" /D "%FRONTEND_DIR%" cmd /k "npm run dev"
echo   - Vite 进程已派发，等待端口就绪...
call :wait_port %FRONTEND_PORT% 45 Frontend
if errorlevel 1 (
    echo [ERROR] 前端未能在 45 秒内就绪，请查看启动窗口
    pause
    exit /b 1
)
echo   - 前端端口已就绪
:frontend_done
echo [完成] 前端
echo.

REM ---- 5. 汇总 + 自动开浏览器 ----
echo [5/5] 全部就绪，自动打开浏览器
start "" "http://localhost:%FRONTEND_PORT%"

echo ==========================================
echo   Qdrant:   http://localhost:%QDRANT_HTTP_PORT%
echo   Redis:    localhost:%REDIS_PORT%
echo   Backend:  http://localhost:%BACKEND_PORT%
echo   Frontend: http://localhost:%FRONTEND_PORT%
echo   API 文档: http://localhost:%BACKEND_PORT%/docs
echo.
echo   关闭对应窗口即可停止服务（Redis/Qdrant/Backend/Frontend 各一个窗口）。
echo   Log locations:
echo     Redis:    %REDIS_LOG%
echo     Qdrant:  启动窗口输出
echo     Backend: 启动窗口输出
echo     Frontend: 启动窗口输出
echo ==========================================
echo.
echo [完成] 所有服务已启动。按任意键关闭此汇总窗口...
echo       (其他四个服务窗口保持运行)
pause >nul
endlocal
exit /b 0