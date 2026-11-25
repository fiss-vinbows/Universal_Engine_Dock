@echo off
REM Universal Engine Dock 起動用バッチファイル。ダブルクリックで仮想環境の pythonw から起動する
REM (pythonw はコンソールを出さない。エラーは crash_log.txt に残る)
cd /d "%~dp0"
start "" venv\Scripts\pythonw.exe -m uedock
