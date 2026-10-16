# HR-Tracker

Fragt alle 4 Stunden die „Normale Suche“ auf [handelsregister.de](https://www.handelsregister.de) ab
und schickt **einmalig** eine ntfy-Push-Nachricht (optional zusätzlich als E-Mail), sobald die Firma
auftaucht. Schlägt die Abfrage mehrmals in Folge fehl (Portal down oder HTML geändert), kommt eine Warnung.

## Installation auf dem VPS

```bash
sudo mkdir -p /opt/hr-tracker
sudo cp hr_tracker.py requirements.txt /opt/hr-tracker/
sudo python3 -m venv /opt/hr-tracker/venv
sudo /opt/hr-tracker/venv/bin/pip install -r /opt/hr-tracker/requirements.txt

sudo cp hr-tracker.env.example /etc/hr-tracker.env
sudo chmod 600 /etc/hr-tracker.env
sudo nano /etc/hr-tracker.env          # HR_SEARCH, NTFY_URL, ggf. NTFY_EMAIL eintragen
```

Vorher testen:

```bash
set -a; . /etc/hr-tracker.env; set +a
/opt/hr-tracker/venv/bin/python /opt/hr-tracker/hr_tracker.py --dry-run      # zeigt Treffer, sendet nichts
/opt/hr-tracker/venv/bin/python /opt/hr-tracker/hr_tracker.py --test-notify  # Testnachricht
```

Tipp: Mit `--dry-run` und dem Namen einer bestehenden Firma (z. B. der GmbH, falls schon eingetragen)
prüfen, dass die Filter greifen.

Timer aktivieren:

```bash
sudo cp hr-tracker.service hr-tracker.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hr-tracker.timer
systemctl list-timers hr-tracker.timer   # nächster Lauf
journalctl -u hr-tracker.service         # Logs
```

Alternativ per Cron: `0 */4 * * * set -a; . /etc/hr-tracker.env; /opt/hr-tracker/venv/bin/python /opt/hr-tracker/hr_tracker.py`
(dann `STATE_FILE` auf einen beschreibbaren Pfad setzen).

## Hinweise

- Bereits gemeldete Registernummern stehen in `state.json`; zum erneuten Melden die Datei löschen.
- Für eine weitere Firma: zweite Env-Datei + eigene Service-Unit mit eigenem `STATE_FILE`.
- Das Portal erlaubt max. 60 Abfragen/Stunde; der Tracker macht eine Suche alle 4 Stunden.
