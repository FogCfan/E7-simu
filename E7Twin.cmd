@echo off
rem E7 Twin : lance le serveur local et ouvre le dashboard (double-clic).
rem Le navigateur n'est ouvert qu'une fois le port 8000 a l'ecoute (30 s max).
cd /d "%~dp0"
start "" /b powershell -NoProfile -Command "$u=[DateTime]::Now.AddSeconds(30); while([DateTime]::Now -lt $u){ try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8000); $c.Close(); Start-Process 'http://127.0.0.1:8000'; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; Write-Host 'Le serveur ne repond pas apres 30 s.'"
py -3.14 -m e7twin.cli serve
if errorlevel 1 pause
