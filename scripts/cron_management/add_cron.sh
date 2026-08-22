#!/bin/bash

if [ -z "$1" ]; then
  echo "⚠️ Error: Cal proporcionar l'expressió cron."
  echo "Ús: $0 \"<expressió cron>\""
  echo "Exemple: $0 \"0 * * * *\" (cada hora)"
  exit 1
fi

CRON_EXP="$1"
PROJECT_DIR="/Users/jordi/Downloads/files"
CMD="$PROJECT_DIR/.venv/bin/rac1sync sync"
LOG_FILE="$PROJECT_DIR/rac1sync.log"
JOB_COMMENT="RAC1SYNC_JOB"

# Afegim la feina al crontab
(crontab -l 2>/dev/null | grep -v "$JOB_COMMENT"; echo "$CRON_EXP cd $PROJECT_DIR && $CMD >> $LOG_FILE 2>&1 # $JOB_COMMENT") | crontab -

echo "✅ Cron job programat amb èxit amb l'expressió: $CRON_EXP"
echo "Es guardarà el registre d'execució a: $LOG_FILE"
