@echo off
REM ============================================================
REM  Cuvyro - build a Windows installer in one command.
REM  Usage:  build-installer.bat [version]     (default 1.6.2)
REM          build-installer.bat [version] --freeze-only    (for MSIX)
REM  Output: dist\CuvyroSetup-<version>.exe
REM
REM  Signing is opt-in through the CUVYRO_SIGN_* environment; see
REM  packaging\sign.bat. The exe is signed before the installer is compiled and
REM  the installer afterwards, which is the order a release verifies.
REM ============================================================
setlocal
cd /d "%~dp0"

set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.6.2"

set "PY=.venv\Scripts\python.exe"
REM Prefer `python` (present on CI runners); fall back to the `py` launcher.
REM Each check re-tests the result instead of relying on a variable, because
REM %VAR% expands at parse time inside a parenthesised block.
if exist "%PY%" echo [1/7] Virtual environment found.
if not exist "%PY%" (
  echo [1/7] Creating virtual environment...
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

echo [2/7] Installing dependencies...
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

echo [3/7] Freezing app with PyInstaller...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
"%PY%" -m PyInstaller --noconfirm --clean packaging\cuvyro.spec
if errorlevel 1 goto :err

if not exist "dist\Cuvyro\Cuvyro.exe" (
  echo ERROR: PyInstaller did not produce dist\Cuvyro\Cuvyro.exe
  goto :err
)

echo [4/7] Placing the model folder next to the executable...
xcopy /e /i /y "model" "dist\Cuvyro\model" >nul
if errorlevel 1 goto :err
if /i "%~2"=="--freeze-only" exit /b 0

echo [5/7] Signing the executable...
call "%~dp0packaging\sign.bat" "dist\Cuvyro\Cuvyro.exe"
if errorlevel 1 goto :err

echo [6/7] Compiling installer with Inno Setup...
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo ERROR: Inno Setup 6 not found.
  echo Install it with:  winget install JRSoftware.InnoSetup
  goto :err
)

"%ISCC%" /Q /DMyAppVersion=%VERSION% packaging\cuvyro.iss
if errorlevel 1 goto :err

echo [7/7] Signing the installer...
call "%~dp0packaging\sign.bat" "dist\CuvyroSetup-%VERSION%.exe"
if errorlevel 1 goto :err

echo.
echo ============================================================
echo  BUILD OK
echo  Installer: dist\CuvyroSetup-%VERSION%.exe
echo ============================================================
exit /b 0

:err
echo.
echo ============================================================
echo  BUILD FAILED
echo ============================================================
exit /b 1
