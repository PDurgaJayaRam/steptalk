@echo off
echo Starting AI Voice Phone Assistant...
echo.
echo Make sure you have configured your credentials in backend\.env
echo For webhooks, set BASE_URL to your ngrok or deployed URL.
echo.
cd backend
pip install -r requirements.txt
python main.py
