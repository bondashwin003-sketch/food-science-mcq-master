@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment...
  py -m venv .venv
  if errorlevel 1 (
    python -m venv .venv
  )
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Check your internet connection and Python installation.
  pause
  exit /b 1
)
echo.
echo Starting Food Science MCQ Master...
python -m streamlit run app.py
pause
