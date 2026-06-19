@echo off
cd /d "%~dp0"
title WorkLab Scheduler
echo Iniciando scheduler WorkLab...
python scheduler_trigger.py
pause
