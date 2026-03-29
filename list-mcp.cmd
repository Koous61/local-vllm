@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\mcp\list-mcp.ps1" %*
