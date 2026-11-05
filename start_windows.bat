@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PTT Discord Bot

echo ============================================================
echo   PTT Discord Bot - Windows 啟動器
echo ============================================================
echo.

REM 1. 檢查虛擬環境是否已建立
if exist ".venv\Scripts\python.exe" goto check_env

echo [PTT-DC-Bot] 未檢測到虛擬環境 (.venv)，正在自動初始化...
where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto create_venv
)
where python >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=python"
    goto create_venv
)
echo [錯誤] 找不到 Python 執行檔！
echo 請至 https://www.python.org/ 下載安裝 Python 3.10+，
echo 並於安裝時勾選 "Add python.exe to PATH"。
echo.
pause
exit /b 1

:create_venv
echo 正在建立虛擬環境 (.venv)...
%PY_CMD% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo [錯誤] 虛擬環境建立失敗！
    echo.
    pause
    exit /b 1
)

echo 正在安裝必要套件 (requirements.txt)...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [警告] 部分套件安裝失敗，請檢查網路連線。
)
echo.

:check_env
REM 2. 檢查 .env 設定檔與 Discord Token
set RUN_WIZARD=0
if not exist ".env" (
    set RUN_WIZARD=1
    goto run_wizard_check
)
findstr /r /c:"^DISCORD_TOKEN=..*" ".env" >nul 2>nul
if errorlevel 1 (
    set RUN_WIZARD=1
    goto run_wizard_check
)
findstr /c:"your_discord_bot_token_here" ".env" >nul 2>nul
if not errorlevel 1 (
    set RUN_WIZARD=1
    goto run_wizard_check
)
findstr /c:"your_discord_token_here" ".env" >nul 2>nul
if not errorlevel 1 (
    set RUN_WIZARD=1
    goto run_wizard_check
)

:run_wizard_check
if %RUN_WIZARD% EQU 1 (
    echo ============================================================
    echo [PTT-DC-Bot] 檢測到尚未完成憑證設定，正在啟動設定精靈...
    echo ============================================================
    echo.
    .venv\Scripts\python.exe setup_wizard.py
    if errorlevel 1 (
        echo.
        echo [嚴重錯誤] 設定精靈已取消或失敗，停止啟動。
        pause
        exit /b 42
    )
    echo.
)

REM 3. 機器人執行守護迴圈 (崩潰自動重啟)
:loop
echo ============================================================
echo [%DATE% %TIME%] 正在啟動 PTT Discord Bot...
echo ============================================================
.venv\Scripts\python.exe bot.py
set EXITCODE=%ERRORLEVEL%
echo.
echo [%DATE% %TIME%] 機器人已停止運行 (結束代碼: %EXITCODE%)

REM 正常退出 (Code 0)：不重啟，直接結束
if %EXITCODE% EQU 0 (
    echo 機器人已正常關閉。
    goto end
)

REM 致命錯誤 (Code 42)：憑證無效或設定精靈中止，已停止自動重啟
if %EXITCODE% EQU 42 (
    echo [嚴重錯誤] 憑證無效或設定精靈中止，已停止自動重啟。
    goto fatal
)

REM 異常崩潰 (其他非 0 結束代碼)：等待 10 秒後自動重啟
echo.
echo [提示] 程式異常退出，10 秒後將自動重新啟動...
echo (如欲徹底停止，請直接關閉此視窗或按 Ctrl+C)
timeout /t 10
goto loop

:fatal
echo.
echo 機器人因嚴重錯誤終止，請檢查設定與日誌。
pause
exit /b %EXITCODE%

:end
echo.
pause
