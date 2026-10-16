@echo off
REM ============================================================
REM  打包 TG 频道批量退出工具 为 单文件 exe
REM  用法：在本文件所在目录双击运行，或命令行执行 build_exe.bat
REM  前提：当前 python 必须自带 tkinter（官方 CPython 默认即含）。
REM        若用“精简/嵌入式”Python 打包，打出的 exe 会因缺 tkinter 而无法运行！
REM ============================================================

python -c "import tkinter" 2>NUL
if errorlevel 1 (
  echo [错误] 当前 python 未包含 tkinter，打出来的 exe 将无法启动界面。
  echo 请改用官方 CPython（https://www.python.org）后重试。
  pause
  exit /b 1
)

python -m pip install -q --upgrade pip
python -m pip install -q pyinstaller
python -m pip install -q -r requirements.txt

python -m PyInstaller --noconfirm --onefile --windowed --name "TG频道批量退出工具" ^
  --hidden-import telethon.tl.types ^
  --hidden-import telethon.tl.functions ^
  --hidden-import telethon.tl.custom ^
  --hidden-import telethon.errors ^
  --hidden-import telethon.crypto ^
  --hidden-import python_socks ^
  --hidden-import python_socks.async_.asyncio ^
  --hidden-import python_socks.async_.asyncio._proxy ^
  --hidden-import python_socks.async_.asyncio._stream ^
  tg_channel_leave.py

echo.

REM 自动读取版本号，把产物改名为带版本号的形式（如 TG频道批量退出工具.v1.7.6.exe）
for /f "delims=" %%v in ('python -c "import re;print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"', open('tg_channel_leave.py', encoding='utf-8').read()).group(1))"') do set APP_VER=%%v

if exist "dist\TG频道批量退出工具.exe" (
  move /Y "dist\TG频道批量退出工具.exe" "dist\TG频道批量退出工具.%APP_VER%.exe" >NUL
  echo 构建完成，exe 位于：dist\TG频道批量退出工具.%APP_VER%.exe
) else (
  echo 构建似乎未完成，请检查上方输出。
)
pause
