@echo off
REM Simple wrapper to call Piper with one-line text input
REM Usage: say "Hello there, testing Piper"

setlocal

REM Location of piper.exe
set PIPER_EXE=D:\dev\windows\gloo\tools\piper\piper.exe

REM Location of voice model (.onnx and .onnx.json must be together)
set VOICE_MODEL=D:\dev\windows\gloo\tools\piper\models\en_GB-northern_english_male-medium.onnx

REM Output wav file
set OUTPUT_FILE=out.wav

if "%~1"=="" (
  echo Usage: say "Your text here"
  exit /b 1
)

REM Pass the first argument as input text to Piper
echo %~1 | "%PIPER_EXE%" -m "%VOICE_MODEL%" -f "%OUTPUT_FILE%"

echo Generated %OUTPUT_FILE%

endlocal
