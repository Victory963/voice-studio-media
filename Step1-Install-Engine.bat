@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo  Voice Studio - Step 1: Install voice engine
echo  Download is about 5.8GB, one time only.
echo  If it stops, double-click again to resume.
echo ============================================
if exist engine\GPT-SoVITS-v2-240821\runtime\python.exe (
  echo Engine is already installed. Nothing to do.
  pause
  exit /b 0
)
where curl >nul 2>nul || (echo curl is missing. Please update Windows 10/11 first. & pause & exit /b 1)
if not exist downloads mkdir downloads
set PKG=downloads\GPT-SoVITS-v2-240821.7z
if exist %PKG% (
  powershell -NoProfile -Command "if ((Get-Item '%PKG%').Length -lt 1000000) { exit 1 } else { exit 0 }"
  if errorlevel 1 (
    echo Found a tiny broken leftover file from the earlier failed run. Deleting it.
    del %PKG%
  )
)
echo Downloading GPT-SoVITS official Windows package...
curl -f -L -C - -o %PKG% "https://huggingface.co/lj1995/GPT-SoVITS-windows-package/resolve/main/GPT-SoVITS-v2-240821.7z"
if errorlevel 1 (echo Download did not finish. Check the network, then double-click this file again to resume. & pause & exit /b 1)
powershell -NoProfile -Command "if ((Get-Item '%PKG%').Length -lt 5000000000) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo The downloaded file is far too small, so the download went wrong.
  echo Deleting it. Please double-click this file to download again.
  del %PKG%
  pause
  exit /b 1
)
echo Download OK. Extracting with Windows tar, please wait...
if not exist engine mkdir engine
tar -xf %PKG% -C engine
if exist engine\GPT-SoVITS-v2-240821\runtime\python.exe (
  echo.
  echo Install complete! From now on, just double-click Step2-Open-Studio.bat
) else (
  echo Extraction did not produce the engine files.
  echo Try extracting %PKG% manually with 7-Zip into the engine folder.
)
pause
