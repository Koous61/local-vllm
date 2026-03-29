@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stack\test-chat.ps1" %*
