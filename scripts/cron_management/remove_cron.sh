#!/bin/bash

JOB_COMMENT="RAC1SYNC_JOB"

if ! crontab -l 2>/dev/null | grep -q "$JOB_COMMENT"; then
    echo "ℹ️ No s'ha trobat cap cron job de rac1sync per eliminar."
    exit 0
fi

# Elimina qualsevol línia que coincideixi amb JOB_COMMENT
crontab -l 2>/dev/null | grep -v "$JOB_COMMENT" | crontab -

echo "🗑️ Cron job de rac1sync eliminat amb èxit."
