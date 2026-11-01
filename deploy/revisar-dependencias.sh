#!/bin/bash
# Revisa requirements.txt contra la base de avisos de seguridad de Python (PyPI Advisory Database).
# Se corre en el servidor antes de desplegar:  cd ~/ig-bot && bash deploy/revisar-dependencias.sh
cd "$(dirname "$0")/.." || exit 1
PIP=.venv/bin/pip
[ -x .venv/bin/pip-audit ] || $PIP install -q pip-audit || { echo "No se pudo instalar pip-audit (¿sin red?)"; exit 2; }
.venv/bin/pip-audit -r requirements.txt --progress-spinner off && echo "OK: sin fallas conocidas en las dependencias"
