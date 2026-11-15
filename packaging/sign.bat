@echo off
REM ============================================================
REM  Sign one release artifact, if signing is configured.
REM  Usage:  packaging\sign.bat <file>
REM
REM  Signing is opt-in through the environment so a developer build stays
REM  unsigned and a release build signs without editing this script:
REM
REM    CUVYRO_SIGN_CERT       path to a .pfx (unset means skip, exit 0)
REM    CUVYRO_SIGN_PASSWORD   its password
REM    CUVYRO_SIGN_TIMESTAMP  RFC 3161 server, default http://timestamp.digicert.com
REM    CUVYRO_SIGNTOOL        signtool.exe; PATH and the Windows SDK are tried
REM    CUVYRO_SIGN_REQUIRED   set to refuse the unsigned path (a release build)
REM
REM  Exits 0 only when the file is signed, or when signing is deliberately not
REM  configured and not required. Every other outcome is an error: an unsigned
REM  artifact must never pass as a release by accident.
REM
REM  Flow is label-based rather than nested blocks on purpose. cmd loses the
REM  code of an `exit /b N` that sits inside two levels of parentheses and
REM  reports 0 instead, which would turn a refused signature into a pass.
REM ============================================================
setlocal
set "TARGET=%~1"
if not defined TARGET goto :no_file
if not exist "%TARGET%" goto :missing_file
if defined CUVYRO_SIGN_CERT goto :have_cert
if defined CUVYRO_SIGN_REQUIRED goto :required_without_cert
echo   sign: %~nx1 left unsigned ^(CUVYRO_SIGN_CERT not set^)
exit /b 0

:required_without_cert
echo   sign: CUVYRO_SIGN_REQUIRED is set but CUVYRO_SIGN_CERT is not
exit /b 1

:no_file
echo   sign: no file given
exit /b 1

:missing_file
echo   sign: file not found: %TARGET%
exit /b 1

:have_cert
if exist "%CUVYRO_SIGN_CERT%" goto :have_tool
echo   sign: certificate not found: %CUVYRO_SIGN_CERT%
exit /b 1

:have_tool
set "SIGNTOOL=%CUVYRO_SIGNTOOL%"
if defined SIGNTOOL goto :sign
for %%I in (signtool.exe) do set "SIGNTOOL=%%~$PATH:I"
if defined SIGNTOOL goto :sign
for /d %%D in ("%ProgramFiles(x86)%\Windows Kits\10\bin\10.*") do (
  if exist "%%D\x64\signtool.exe" set "SIGNTOOL=%%D\x64\signtool.exe"
)
if not defined SIGNTOOL goto :no_tool

:sign
set "TIMESTAMP=%CUVYRO_SIGN_TIMESTAMP%"
if not defined TIMESTAMP set "TIMESTAMP=http://timestamp.digicert.com"
echo   sign: %~nx1
REM `call` on both lines: a .bat named through CUVYRO_SIGNTOOL would otherwise
REM take over the batch and skip the verification below it.
call "%SIGNTOOL%" sign /fd sha256 /td sha256 /tr "%TIMESTAMP%" /f "%CUVYRO_SIGN_CERT%" /p "%CUVYRO_SIGN_PASSWORD%" "%TARGET%"
if errorlevel 1 goto :sign_failed
call "%SIGNTOOL%" verify /pa /v "%TARGET%" >nul
if errorlevel 1 goto :verify_failed
echo   sign: ok %~nx1
exit /b 0

:sign_failed
echo   sign: signing failed for %~nx1
exit /b 1

:verify_failed
echo   sign: signature does not verify on %~nx1
exit /b 1

:no_tool
echo   sign: signtool.exe not found; set CUVYRO_SIGNTOOL or install the SDK
exit /b 1
