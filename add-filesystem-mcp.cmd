@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\add-filesystem-mcp.ps1" %*
