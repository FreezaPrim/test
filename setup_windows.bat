@echo off
echo ============================================
echo  Roma + TNPS Analyzer - Setup
echo ============================================

REM Check Python
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org and tick "Add to PATH"
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv .venv

echo Activating...
call .venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org

echo.
echo ============================================
echo  Setup complete!
echo  
echo  To use Roma CLI:
echo    .venv\Scripts\activate
echo    python -m roma
echo    python -m roma add "C:\path\to\your\data"
echo    python -m roma tnps
echo    python -m roma chat
echo  
echo  To run TNPS Analyzer standalone:
echo    python tnps_analyzer.py --data ./data --output Report.xlsx
echo  
echo  To run tests:
echo    python -m pytest tests\ -v
echo ============================================
pause
