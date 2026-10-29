@echo off

:: Define release version tag
set VERSION=v0.2.2

:: Activate virtual environment
call ./.venv/Scripts/activate.bat

:: NOTE: requirements.txt is maintained by hand (runtime only). Do NOT write
:: `pip freeze` over it — the build venv also holds packaging tools, which
:: belong in a separate build requirement file if needed.

:: Build with Pyinstaller
pyinstaller --onefile --windowed ^
    --add-data "./.venv/Lib/site-packages/hid.cp312-win_amd64.pyd;." ^
    --hidden-import "hid" ^
    --hidden-import "pynput.keyboard._win32" ^
    --hidden-import "pynput.mouse._win32" ^
    --hidden-import "pystray._win32" ^
    --name "OTD_Wacom_Touch_Driver" ^
    touch_controller.py %*

:: --- Rename and move executable ---
:: Define source (dist) and destination (current dir)
set src=dist\OTD_Wacom_Touch_Driver.exe
set dest=.

:: Get current date and time in YYMMDD-HHMM format.
:: wmic is deprecated/removed on Windows 11 24H2+, so use PowerShell here.
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyMMdd-HHmm"') do set filedate=%%a

:: Define new filename (with date/time suffix)
set newname=OTD_Wacom_Touch_Driver_pyinst_%VERSION%_%filedate%.exe
::set newname=OTD_Wacom_Touch_Driver_pyinst_%filedate%.exe

:: Check if source file exists
if exist %src% (
    :: Move and rename the file
    :: copy /y %src% %dest%\OTD_Wacom_Touch_Driver_.exe
    :: Create a clean release file for GitHub Upload
    copy /y %src% %dest%\OTD_Wacom_Touch_Driver_%VERSION%_win64.exe
    move /y %src% %dest%\%newname%
    echo Moved and renamed: %newname%
) else (
    echo ERROR: Source file not found: %src%
)

pause
