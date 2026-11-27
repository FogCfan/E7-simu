#!/bin/bash
# E7 Twin (macOS) : lance le serveur local et ouvre le dashboard (double-clic dans le Finder).
# A faire une fois, dans le Terminal, depuis le dossier du depot :
#   chmod +x E7Twin.command
# Pendant de E7Twin.cmd. Le navigateur n'est ouvert qu'une fois le port 8000
# a l'ecoute (30 s max).

cd "$(dirname "$0")" || exit 1

# Python du venv du depot (.venv) s'il existe : le python3 de Homebrew n'a pas e7twin.
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

(
  for _ in $(seq 1 60); do
    if nc -z 127.0.0.1 8000 2>/dev/null; then
      open "http://127.0.0.1:8000"
      exit 0
    fi
    sleep 0.5
  done
  echo "Le serveur ne repond pas apres 30 s."
) &

"$PY" -m e7twin.cli serve
status=$?
if [ "$status" -ne 0 ]; then
  echo "E7 Twin s'est arrete avec le code $status. Appuyez sur Entree pour fermer."
  read -r _
fi
exit "$status"
