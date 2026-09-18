@echo off
call conda activate nuclearapp_latest
if errorlevel 1 (
  echo Abra este arquivo pelo Anaconda Prompt, ou ative o ambiente nuclearapp_latest manualmente.
  pause
  exit /b 1
)
python -m nuclear_segmentation
if errorlevel 1 pause
