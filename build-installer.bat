@echo off
REM ============================================================
REM  Notch - build a Windows installer in one command.
REM  Usage:  build-installer.bat [version]     (default 1.6.0)
REM  Output: dist\NotchSetup-<version>.exe
REM ============================================================
setlocal
cd /d "%~dp0"

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.6.0"

set "PY=.venv\Scripts\python.exe"
REM Prefer `python` (present on CI runners); fall back to the `py` launcher.
REM Each check re-tests the result instead of relying on a variable, because
REM %VAR% expands at parse time inside a parenthesised block.
if exist "%PY%" echo [1/5] Virtual environment found.
if not exist "%PY%" (
  echo [1/5] Creating virtual environment...
  python -m venv .venv
)
if not exist "%PY%" (
  echo        Retrying with the py launcher...
  py -3.13 -m venv .venv
)
if not exist "%PY%" (
  echo ERROR: could not create a virtual environment. Install Python 3.13.
  goto :err
)

echo [2/5] Installing dependencies...
"%PY%" -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto :err

REM The app prefers the sherpa-onnx streaming model in model\sherpa; a Vosk
REM model (model\conf + model\am) is only the fallback for old installs.
if not exist "model\sherpa\tokens.txt" (
  if not exist "model\conf" (
    echo ERROR: no speech model found.
    echo Put the sherpa-onnx streaming zipformer int8 export in model\sherpa:
    echo   https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2
    echo   ...so that model\sherpa\tokens.txt exists, and set
    echo   model\silero_vad.onnx from
    echo   https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
    goto :err
  )
)

REM Head tracking reads the face landmarker from model\face_landmarker.task.
REM It is a plain file beside the speech model, so it travels with the build.
if not exist "model\face_landmarker.task" (
  echo ERROR: no face landmark model found.
  echo Put the MediaPipe face landmarker at model\face_landmarker.task:
  echo   https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
  goto :err
)

echo [3/5] Freezing app with PyInstaller...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
"%PY%" -m PyInstaller --noconfirm --clean packaging\notch.spec
if errorlevel 1 goto :err

if not exist "dist\Notch\Notch.exe" (
  echo ERROR: PyInstaller did not produce dist\Notch\Notch.exe
  goto :err
)

echo [4/5] Placing the model folder next to the executable...
xcopy /e /i /y "model" "dist\Notch\model" >nul
if errorlevel 1 goto :err

echo [5/5] Compiling installer with Inno Setup...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo ERROR: Inno Setup 6 not found.
  echo Install it with:  winget install JRSoftware.InnoSetup
  goto :err
)

"%ISCC%" /Q /DMyAppVersion=%VERSION% packaging\notch.iss
if errorlevel 1 goto :err

echo.
echo ============================================================
echo  BUILD OK
echo  Installer: dist\NotchSetup-%VERSION%.exe
echo ============================================================
exit /b 0

:err
echo.
echo ============================================================
echo  BUILD FAILED
echo ============================================================
exit /b 1
