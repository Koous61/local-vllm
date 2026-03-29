@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\mcp\add-filesystem-mcp.ps1" %*
