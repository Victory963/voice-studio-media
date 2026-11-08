@echo off
cd /d %~dp0
set PY=%~dp0engine\GPT-SoVITS-v2-240821\runtime\python.exe
if not exist "%PY%" (
  echo Engine not installed yet. Please double-click Step1-Install-Engine.bat first.
  pause
  exit /b 1
)
set VOICE_DIR=%~dp0voice
set WHISPER_PY=%PY%
set GSV_URL=http://127.0.0.1:9880
set NLTK_DATA=%~dp0nltk_data
set PATH=%~dp0engine\GPT-SoVITS-v2-240821;%~dp0engine\GPT-SoVITS-v2-240821\runtime;%PATH%
curl -s -m 2 http://127.0.0.1:9880/ >nul 2>nul
if errorlevel 1 (
  echo Starting voice engine in its own window. The window now stays open
  echo even if the engine stops, so any error stays visible.
  start "VoiceEngine" /d "%~dp0engine\GPT-SoVITS-v2-240821" cmd /k ""%PY%" api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"
) else (
  echo Voice engine is already running.
)
curl -s -m 6 http://127.0.0.1:8899/api/status >nul 2>nul
if errorlevel 1 (
  echo Starting voice studio...
  start "VoiceStudio" cmd /k ""%PY%" "%~dp0studio\studio.py""
) else (
  echo Voice studio is already running.
)
echo Waiting for the studio to come up...
for /L %%i in (1,1,30) do (
  curl -s -m 6 http://127.0.0.1:8899/api/status >nul 2>nul && goto STUDIO_UP
  timeout /t 2 >nul
)
echo Studio did not come up. Please screenshot the VoiceStudio window and send it to Muse.
pause
exit /b 1
:STUDIO_UP
echo Studio is up. Waiting for the voice engine to finish loading...
echo On CPU this can take 1 to 3 minutes. Ready line: Uvicorn running
for /L %%i in (1,1,90) do (
  curl -s -m 2 http://127.0.0.1:9880/ >nul 2>nul && goto ENGINE_UP
  timeout /t 2 >nul
)
echo.
echo WARNING: the engine did not come up after about 3 minutes.
echo The VoiceEngine window is still open. Please screenshot it and send it to Muse.
echo The page will open anyway so you can see the status dot.
goto OPEN_PAGE
:ENGINE_UP
echo Engine is up.
:OPEN_PAGE
start http://127.0.0.1:8899
echo.
echo Browser opened. Keep BOTH black windows open - VoiceEngine and VoiceStudio.
echo Closing them stops the service.
pause
