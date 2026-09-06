@echo off
chcp 65001 >nul
cd /d "%~dp0.."
setlocal
set "PYCMD="
python -c "import sys" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  echo [错误] 未找到可用的 Python，请安装 Python 3.10+（64 位）
  pause
  exit /b 1
)

echo ================================================
echo  wx-agent 依赖安装
echo ================================================
echo.

rem —— 判断是否离线安装 ——
if exist "offline\wheels\wechatauto_replica-1.1.5.1-py3-none-any.whl" (
  echo [离线模式] 检测到 offline\wheels，将完全离线安装。
  echo 注意：offline\wheels 里的二进制包是 Python 3.10（cp310）版，
  echo       请使用 Python 3.10（64 位）。当前解释器版本：
  %PYCMD% -c "import sys; print('       Python', sys.version.split()[0])"
  echo.
  echo 开始离线安装全部依赖 ...
  %PYCMD% -m pip install --no-index --find-links=offline/wheels -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [失败] 离线安装失败。常见原因：
    echo   1) 当前 Python 不是 3.10（cp310 wheel 装不上）——请换 Python 3.10 或用联网安装；
    echo   2) offline\wheels 缺包——请在有网机器上执行 pip download -r requirements.txt -d offline\wheels 补齐。
  ) else (
    echo.
    echo [成功] 离线安装完成。
  )
) else (
  echo [联网模式] 未找到 offline\wheels，联网安装。
  echo.
  echo [1/2] 安装 wechatauto-replica（微信接入核心）...
  %PYCMD% -m pip install "wechatauto-replica==1.1.5.1"
  echo.
  echo [2/2] 安装其余依赖 ...
  %PYCMD% -m pip install requests urllib3 Pillow cryptography zstandard uiautomation comtypes pywin32 pyperclip psutil colorama winsdk imageio-ffmpeg
)

echo.
echo 安装结束，运行 scripts\自检.bat 验证环境。
pause
