@echo off
REM FinanzasPersonalesIA - Django Development Server Startup Script

echo.
echo ========================================
echo  FinanzasPersonalesIA - Dev Server
echo ========================================
echo.

cd /d E:\FinanzasPersonalesIA

echo [1/4] Checking Django setup...
python manage.py check
if errorlevel 1 (
    echo ERROR: Django configuration failed!
    pause
    exit /b 1
)

echo [2/4] Running migrations...
python manage.py migrate --noinput

echo [3/4] Collecting static files...
python manage.py collectstatic --noinput

echo.
echo [4/4] Starting development server...
echo.
echo ========================================
echo  Server running at:
echo  
echo  http://localhost:8000
echo  http://localhost:8000/dashboard
echo  http://localhost:8000/admin
echo  
echo  Test Login:
echo  Username: demo_user
echo  Password: demo1234
echo  
echo  Press CTRL+C to stop the server
echo ========================================
echo.

python manage.py runserver

pause
