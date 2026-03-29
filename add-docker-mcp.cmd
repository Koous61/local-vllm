@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\mcp\add-docker-mcp.ps1" %*
