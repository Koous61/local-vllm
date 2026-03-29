@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\mcp\disable-mcp.ps1" %*
