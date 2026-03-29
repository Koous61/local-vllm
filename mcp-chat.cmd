@echo off
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\mcp\mcp-chat.py" %*
) else (
  python "%~dp0scripts\mcp\mcp-chat.py" %*
)
