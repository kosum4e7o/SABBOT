# Build (v20.2)

`GitHub Actions -> Build Windows Installer` runs, in this order, and FAILS on the first problem:

1. unit tests (`python -m unittest discover -s tests -t .`)
2. PyInstaller **ONEDIR** (`dist/Stream_Activity_Bot/`)
3. `scripts/verify_onedir.ps1` - EXE, python312.dll, PySide6, shiboken6, Qt6 DLLs, qwindows.dll, .pyd
4. Inno Setup compiles `installer/StreamActivityBot.iss` -> `installer_output/Stream_Activity_Bot_Setup.exe`
5. installer size check + "only one file" check
6. `scripts/smoke_test_installer.ps1`: silent install, file check, `--selftest` of the installed EXE,
   start app (12 s alive), silent uninstall, files removed
7. upload artifact `Stream_Activity_Bot_Installer` (exactly one file)

Local developer run: `pip install -r requirements.txt` then `python stream_activity_bot.py`.
User data (config, tokens, chat DB, log) lives in `%APPDATA%\Stream Activity Bot` and survives uninstall.
