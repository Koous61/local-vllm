@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stack\stop.ps1" %*
