# =============================================================================
# PTT-DC-Bot Windows PowerShell One-Liner Installer
#
# Target Execution Command:
#   irm https://raw.githubusercontent.com/jasonxp516/ptt-dc-bot/main/install.ps1 | iex
#
# Features:
#   1. Automatic detection of repo directory vs fresh install target ($HOME\ptt-dc-bot)
#   2. GitHub zip download & Expand-Archive fallback if git is unavailable
#   3. Python 3.10+ detection with winget auto-installation fallback
#   4. Direct invocation of virtualenv python.exe to bypass ExecutionPolicy restrictions
#      (Bypasses environment activation scripts completely)
#   5. Dependency installation (requirements.txt) & interactive setup (setup_wizard.py)
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "        PTT Discord Bot - Windows 一鍵安裝精靈" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# -----------------------------------------------------------------------------
# 1. 判斷安裝路徑 (若在專案原始碼目錄中則原地安裝，否則下載至 $HOME\ptt-dc-bot)
# -----------------------------------------------------------------------------
$CurrentDir = (Get-Item .).FullName
if ((Test-Path (Join-Path $CurrentDir "bot.py")) -and (Test-Path (Join-Path $CurrentDir "requirements.txt"))) {
    $InstallDir = $CurrentDir
    Write-Host "[PTT-DC-Bot] 檢測到已在專案原始碼目錄中: $InstallDir" -ForegroundColor Green
} else {
    $InstallDir = Join-Path $HOME "ptt-dc-bot"
    Write-Host "[PTT-DC-Bot] 目標安裝路徑: $InstallDir" -ForegroundColor Cyan
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    # 下載或 Clone 專案原始碼
    $RepoUrl = "https://github.com/jasonxp516/ptt-dc-bot.git"
    $ZipUrl = "https://github.com/jasonxp516/ptt-dc-bot/archive/refs/heads/main.zip"

    if ((Get-Command git -ErrorAction SilentlyContinue) -and (-not (Test-Path (Join-Path $InstallDir "bot.py")))) {
        Write-Host "[PTT-DC-Bot] 使用 Git 複製專案原始碼..." -ForegroundColor Cyan
        git clone $RepoUrl $InstallDir
    } elseif (-not (Test-Path (Join-Path $InstallDir "bot.py"))) {
        Write-Host "[PTT-DC-Bot] 正在從 GitHub 下載最新版本壓縮檔..." -ForegroundColor Cyan
        $ZipPath = Join-Path $env:TEMP "ptt-dc-bot-main.zip"
        $ExtractPath = Join-Path $env:TEMP "ptt-dc-bot-extract"

        if (Test-Path $ExtractPath) {
            Remove-Item -Path $ExtractPath -Recurse -Force -ErrorAction SilentlyContinue
        }

        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath

        Write-Host "[PTT-DC-Bot] 解壓縮檔案中..." -ForegroundColor Cyan
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractPath -Force

        $ExtractedRoot = Join-Path $ExtractPath "ptt-dc-bot-main"
        if (Test-Path $ExtractedRoot) {
            Copy-Item -Path (Join-Path $ExtractedRoot "*") -Destination $InstallDir -Recurse -Force
        } else {
            Copy-Item -Path (Join-Path $ExtractPath "*") -Destination $InstallDir -Recurse -Force
        }

        Remove-Item -Path $ZipPath -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $ExtractPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Set-Location $InstallDir

# -----------------------------------------------------------------------------
# 2. 檢測 Python 3.10+ 環境 (支援 winget 自動安裝 fallback)
# -----------------------------------------------------------------------------
function Get-CompatiblePython {
    $commands = @("py", "python", "python3")
    foreach ($cmd in $commands) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            $cmdArgs = if ($cmd -eq "py") { @("-3") } else { @() }
            try {
                & $cmd @cmdArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    return @{
                        Command = $cmd
                        Args = $cmdArgs
                    }
                }
            } catch {
                # 忽略執行例外，繼續嘗試下一個候選指令
            }
        }
    }
    return $null
}

$PyCandidate = Get-CompatiblePython

if (-not $PyCandidate) {
    Write-Host "[PTT-DC-Bot] 未檢測到符合版本需求的 Python (>= 3.10)！" -ForegroundColor Yellow

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "[PTT-DC-Bot] 檢測到 Windows 套件管理工具 (winget)，準備自動安裝 Python 3.11..." -ForegroundColor Cyan
        try {
            winget install --id Python.Python.3.11 --exact --source winget --accept-package-agreements --accept-source-agreements
            # 從登錄檔重新整理當前 Session 的 PATH 環境變數
            $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
            $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
            $env:Path = "$machinePath;$userPath"

            $PyCandidate = Get-CompatiblePython
        } catch {
            Write-Host "[PTT-DC-Bot] winget 安裝過程發生例外。" -ForegroundColor Yellow
        }
    }

    if (-not $PyCandidate) {
        Write-Host ""
        Write-Host "[錯誤] 找不到 Python 3.10 或更新版本。" -ForegroundColor Red
        Write-Host "請手動前往官方網站下載並安裝：" -ForegroundColor Yellow
        Write-Host "  https://www.python.org/downloads/" -ForegroundColor Cyan
        Write-Host "【重要】安裝程式首頁務必勾選「Add python.exe to PATH」！" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

Write-Host "[PTT-DC-Bot] 檢測到相容的 Python 環境 ($($PyCandidate.Command))。" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 3. 建立虛擬環境 (.venv) - 嚴格避免啟用腳本以防 ExecutionPolicy 阻擋
# -----------------------------------------------------------------------------
$VenvDir = Join-Path $InstallDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[PTT-DC-Bot] 正在建立虛擬環境 (.venv)..." -ForegroundColor Cyan
    $venvArgs = @()
    if ($PyCandidate.Args) {
        $venvArgs += $PyCandidate.Args
    }
    $venvArgs += @("-m", "venv", $VenvDir)
    & $PyCandidate.Command @venvArgs

    if (-not (Test-Path $VenvPython)) {
        Write-Host "[PTT-DC-Bot] 錯誤：虛擬環境建立失敗！" -ForegroundColor Red
        exit 1
    }
}

# -----------------------------------------------------------------------------
# 4. 安裝依賴套件 (直接以虛擬環境的 python.exe 呼叫 pip)
# -----------------------------------------------------------------------------
$ReqFile = Join-Path $InstallDir "requirements.txt"
if (Test-Path $ReqFile) {
    Write-Host "[PTT-DC-Bot] 正在升級 pip 並安裝相依套件 (requirements.txt)..." -ForegroundColor Cyan
    & "$VenvPython" -m pip install --upgrade pip
    & "$VenvPython" -m pip install -r "$ReqFile"
} else {
    Write-Host "[PTT-DC-Bot] 警告：未找到 requirements.txt！" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 5. 首次設定精靈 (setup_wizard.py)
# -----------------------------------------------------------------------------
$EnvFile = Join-Path $InstallDir ".env"
$WizardScript = Join-Path $InstallDir "setup_wizard.py"

$needsWizard = $true
if (Test-Path $EnvFile) {
    $envContent = Get-Content $EnvFile -Raw -ErrorAction SilentlyContinue
    if ($envContent -and ($envContent -match 'DISCORD_TOKEN\s*=\s*([^\s#]+)') -and ($matches[1] -notmatch 'your_discord_.*token')) {
        $needsWizard = $false
    }
}

if ($needsWizard -and (Test-Path $WizardScript)) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "[PTT-DC-Bot] 正在啟動首次設定精靈..." -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    & "$VenvPython" "$WizardScript"
}

# -----------------------------------------------------------------------------
# 6. 安裝完成與啟動引導
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  PTT Discord Bot 安裝完成！" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "安裝目錄: $InstallDir" -ForegroundColor Cyan
Write-Host "日後隨時可雙擊資料夾內的 start_windows.bat 啟動機器人。" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

$BotScript = Join-Path $InstallDir "bot.py"
if (Test-Path $BotScript) {
    $launchChoice = Read-Host "是否立即啟動機器人？(Y/N) [預設: Y]"
    if ([string]::IsNullOrWhiteSpace($launchChoice) -or ($launchChoice -match '^[Yy]')) {
        Write-Host "[PTT-DC-Bot] 正在啟動機器人..." -ForegroundColor Cyan
        & "$VenvPython" "$BotScript"
    }
}
