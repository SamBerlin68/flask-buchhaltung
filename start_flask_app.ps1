Set-Location "D:\projects\flask_app"
.\venv\Scripts\Activate.ps1

Start-Process "http://127.0.0.1:5000"

$env:FLASK_APP = "app.py"
$env:FLASK_ENV = "development"
flask run

