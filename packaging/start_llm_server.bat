@echo off
REM ============================================================
REM voice-ime LLM server launcher (v5: content summarization)
REM Starts llama-server (Vulkan backend) with the local Qwen model
REM on port 8081. The ASR server (port 6016) is untouched.
REM Pure ASCII - safe under any Windows code page.
REM
REM NOTE (v5.1+): the client now starts this server ON DEMAND when you
REM click "generate content summary", and shuts it down afterwards.
REM Manual start via this script is no longer required (still fine for
REM debugging - the client will reuse the running server and not kill it).
REM ============================================================
setlocal

REM ---- model path: edit here if your model lives elsewhere ----
set MODEL_PATH=C:\AI\models\gguf\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf
set MODEL_ALIAS=qwen
set LLAMA_DIR=llama-server
set PORT=8081
set CTX=32768

echo.
echo  ==============================================
echo   voice-ime LLM server launcher
echo  ==============================================
echo.

REM ---------- 1. Check llama-server ----------
if exist "%LLAMA_DIR%\llama-server.exe" goto server_ok

echo [1/3] llama-server not found.
if not exist "%LLAMA_DIR%" mkdir "%LLAMA_DIR%"

REM Allow a manually downloaded zip: next to this script, or in TEMP
if exist "llama-win-vulkan.zip" goto copy_local_zip
if exist "%TEMP%\llama-win-vulkan.zip" goto do_extract
goto try_download

:copy_local_zip
copy /y "llama-win-vulkan.zip" "%TEMP%\llama-win-vulkan.zip" >nul
goto do_extract

:try_download
echo        Trying auto download from GitHub ...
curl -sL -o "%TEMP%\llama-win-vulkan.zip" https://github.com/ggml-org/llama.cpp/releases/download/b10621/llama-b10621-bin-win-vulkan-x64.zip
if not errorlevel 1 goto do_extract
del /f /q "%TEMP%\llama-win-vulkan.zip" 2>nul
echo.
echo [ERROR] Auto download failed. Your network may block github.com.
echo        Please download it manually in your browser:
echo        https://github.com/ggml-org/llama.cpp/releases/download/b10621/llama-b10621-bin-win-vulkan-x64.zip
echo        Save the file as llama-win-vulkan.zip next to this script,
echo        then re-run this script.
pause
exit /b 1

:do_extract
tar -xf "%TEMP%\llama-win-vulkan.zip" -C "%LLAMA_DIR%"
if exist "%LLAMA_DIR%\llama-server.exe" goto server_ok
echo [ERROR] llama-server.exe not found after extraction.
pause
exit /b 1

:server_ok
echo        llama-server ready.

REM ---------- 2. Check model ----------
if not exist "%MODEL_PATH%" (
    echo.
    echo [ERROR] Model file not found:
    echo        %MODEL_PATH%
    echo        Edit MODEL_PATH at the top of this script and retry.
    pause
    exit /b 1
)
echo [2/3] Model found.

REM ---------- 3. Start server ----------
echo.
echo [3/3] Starting llama-server ...
echo        Port : %PORT%
echo        Model: %MODEL_PATH%
echo.
echo  Keep this window open. Close it to stop the server.
echo  The voice-ime client will use it for content summaries.
echo.
"%LLAMA_DIR%\llama-server.exe" -m "%MODEL_PATH%" --alias %MODEL_ALIAS% --port %PORT% -c %CTX% -ngl 99
pause
