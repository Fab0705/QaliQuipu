@echo off
REM ==========================================================
REM  Build de Chasqui-Log como ejecutable standalone (Windows)
REM ==========================================================

REM 1. Instala PyInstaller si no lo tienes (dentro de tu .venv)
pip install pyinstaller

REM 2. Limpia builds anteriores
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del ChasquiLog.spec 2>nul

REM 3. Genera el .exe
REM    --onefile     -> un solo archivo .exe (mas facil de distribuir)
REM    --windowed    -> sin consola negra detras de la GUI
REM    --collect-all -> incluye los assets internos de customtkinter y pywhatkit
REM                     (temas .json, imagenes, etc. que si no se incluyen
REM                     rompen la interfaz al abrir el .exe en otra PC)
pyinstaller --noconfirm --onefile --windowed ^
    --name ChasquiLog ^
    --collect-all customtkinter ^
    --collect-all pywhatkit ^
    index.py

REM 4. Copia las bases de datos junto al .exe generado
REM    (se dejan FUERA del .exe para que la app pueda escribir en ellas;
REM    si se empaquetan adentro, los cambios no se guardarian)
copy qalinode_pos.db dist\qalinode_pos.db
copy qalinode_test.db dist\qalinode_test.db
copy PyWhatKit_DB.txt dist\PyWhatKit_DB.txt

echo.
echo ============================================
echo  Listo. Tu app esta en la carpeta dist\
echo  Copia TODA esa carpeta (no solo el .exe)
echo  a la otra computadora.
echo ============================================
pause