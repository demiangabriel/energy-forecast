@echo off
REM Pornire cu UN SINGUR CLICK — creeaza mediul virtual daca lipseste,
REM instaleaza dependentele, pregateste .env din sablon daca lipseste, apoi
REM lanseaza aplicatia. Inlocuieste secventa manuala din README (venv /
REM activate / pip install / uvicorn) cu dublu-click pe acest fisier.
REM
REM IMPORTANT daca ati descarcat un fisier .zip: extrageti-l INTEGRAL
REM (clic dreapta -> Extract All) INAINTE de a rula acest fisier. Rularea
REM direct din interiorul unei ferestre de arhiva (fara extragere) nu poate
REM crea .venv/.env langa el si inchide fereastra imediat, fara mesaj clar.
setlocal enabledelayedexpansion
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo EROARE: Python nu a fost gasit in PATH. Instalati Python 3.11+ de pe python.org
    echo si bifati "Add python.exe to PATH" la instalare, apoi rulati din nou acest fisier.
    goto :eof_cu_pauza
)

if not exist .venv (
    echo Creez mediul virtual .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo EROARE la crearea mediului virtual — vedeti mesajul de mai sus.
        goto :eof_cu_pauza
    )
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo EROARE la activarea mediului virtual ^(.venv pare incomplet/corupt^).
    echo Stergeti folderul .venv si rulati din nou acest fisier.
    goto :eof_cu_pauza
)

if not exist .env (
    echo Creez .env din .env.example ^(completati SMTP etc. mai tarziu, daca e nevoie^)...
    copy .env.example .env >nul
)

echo Verific dependentele ^(rapid, daca sunt deja instalate^)...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo EROARE la instalarea dependentelor — vedeti mesajul de mai sus.
    goto :eof_cu_pauza
)

echo.
echo Pornesc aplicatia — deschideti http://127.0.0.1:8000 in browser.
echo (Opriti cu Ctrl+C)
echo.
uvicorn app.main:app --reload

:eof_cu_pauza
echo.
pause
