#!/usr/bin/env python3
"""Prüft auf handelsregister.de, ob eine Firma eingetragen ist, und meldet
einen neuen Treffer einmalig per ntfy (optional mit E-Mail-Weiterleitung).

Konfiguration über Umgebungsvariablen (siehe hr-tracker.env.example).
"""
import argparse
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.handelsregister.de/rp_web/"
RESULT_TABLE_ID = "ergebnissForm:selectedSuchErgebnisFormTable"
# Schlagwort-Optionen des Portals: 1 = alle Wörter, 2 = mind. ein Wort, 3 = exakter Name
SEARCH_MODES = {"all": "1", "any": "2", "exact": "3"}


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Fehlende Konfiguration: {name}")
    return value


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(text.split())


def hidden_fields(form):
    return {
        i["name"]: i.get("value", "")
        for i in form.find_all("input", {"type": "hidden"})
        if i.get("name")
    }


def search(term, mode, timeout=30):
    """Führt die 'Normale Suche' aus und gibt die Trefferliste zurück."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (hr-tracker)", "Connection": "close"})

    r = s.get(BASE + "welcome.xhtml", timeout=timeout)
    r.raise_for_status()
    navi = BeautifulSoup(r.text, "html.parser").find("form", id="naviForm")
    if navi is None:
        raise RuntimeError("Startseite hat unerwartetes Format (naviForm fehlt)")

    data = hidden_fields(navi)
    data.update({"naviForm:normaleSucheLink": "naviForm:normaleSucheLink",
                 "target": "normaleSucheLink"})
    r = s.post(BASE + "welcome.xhtml", data=data, timeout=timeout)
    r.raise_for_status()
    form = BeautifulSoup(r.text, "html.parser").find("form", id="form")
    if form is None:
        raise RuntimeError("Suchmaske hat unerwartetes Format (form fehlt)")

    data = hidden_fields(form)
    data.update({
        "form:schlagwoerter": term,
        "form:schlagwortOptionen": SEARCH_MODES[mode],
        "form:ergebnisseProSeite_input": "100",
        "form:btnSuche": "",
    })
    r = s.post(r.url, data=data, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find(id=RESULT_TABLE_ID)
    if table is None:
        # Kein Ergebnis-Container heißt: Fehlerseite/Session-Problem, nicht "nicht gefunden".
        raise RuntimeError("Ergebnisseite hat unerwartetes Format (Tabelle fehlt)")

    results = []
    for row in table.select("tr[data-ri]"):
        court = row.select_one("span.fontWeightBold")
        name = row.select_one("span.marginLeft20")
        seat = row.select_one("td.sitzSuchErgebnisse")
        state_cell = row.select_one("td.fontTableNameSize")
        status = [c.get_text(strip=True) for c in row.select("span.verticalText")]
        results.append({
            "register": court.get_text(" ", strip=True) if court else "",
            "name": name.get_text(" ", strip=True) if name else "",
            "sitz": seat.get_text(" ", strip=True) if seat else "",
            "land": state_cell.find(string=True, recursive=False).strip() if state_cell else "",
            "status": status[-1] if status else "",
        })
    return results


def search_with_retry(term, mode, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return search(term, mode)
        except Exception as exc:  # Netzwerk oder Portal gerade nicht verfügbar
            if attempt == attempts:
                raise
            print(f"Versuch {attempt} fehlgeschlagen: {exc}; neuer Versuch …", file=sys.stderr)
            time.sleep(10 * attempt)


def matches(result, name_filter, seat_filter):
    if name_filter and normalize(name_filter) not in normalize(result["name"]):
        return False
    if seat_filter and normalize(seat_filter) not in normalize(result["sitz"]):
        return False
    return True


PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def notify(title, message, priority="default", tags=()):
    """Sendet über die JSON-API von ntfy, damit Umlaute/Emoji sicher ankommen."""
    server, _, topic = env("NTFY_URL", required=True).rstrip("/").rpartition("/")
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": PRIORITIES[priority],
        "tags": list(tags),
        "click": env("NTFY_CLICK", BASE + "normalesuche/welcome.xhtml"),
    }
    if env("NTFY_EMAIL"):
        payload["email"] = env("NTFY_EMAIL")
    headers = {}
    if env("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env('NTFY_TOKEN')}"
    r = requests.post(server, json=payload, headers=headers, timeout=30)
    r.raise_for_status()


def load_state(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"notified": [], "failures": 0}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nur suchen und Treffer ausgeben, nichts senden/speichern")
    parser.add_argument("--test-notify", action="store_true",
                        help="Testnachricht über ntfy senden und beenden")
    args = parser.parse_args()

    if args.test_notify:
        notify("HR-Tracker: Test", "Wenn du das liest, funktioniert die Benachrichtigung.", tags=["test_tube"])
        print("Testnachricht gesendet.")
        return

    # Mehrere Firmen mit ";" trennen, z. B. "AnOtterInvest;Faroca Ventures"
    terms = [t.strip() for t in env("HR_SEARCH", required=True).split(";") if t.strip()]
    mode = env("HR_SEARCH_MODE", "all")
    if mode not in SEARCH_MODES:
        sys.exit(f"HR_SEARCH_MODE muss eines von {', '.join(SEARCH_MODES)} sein")
    seat_filter = env("HR_SITZ", "")
    alert_after = int(env("FAILURE_ALERT_AFTER", "3"))
    state_path = Path(env("STATE_FILE", "/var/lib/hr-tracker/state.json"))
    state = load_state(state_path)

    hits, errors = [], []
    for i, term in enumerate(terms):
        if i:
            time.sleep(5)  # Portal schonen
        try:
            results = search_with_retry(term, mode)
        except Exception as exc:
            print(f"Suche nach „{term}“ fehlgeschlagen: {exc}", file=sys.stderr)
            errors.append(f"{term}: {exc}")
            continue
        # Treffer müssen den Suchbegriff im Namen enthalten (filtert Beifang des Portals)
        found = [r for r in results if matches(r, term, seat_filter)]
        print(f"{len(results)} Ergebnis(se) für „{term}“, davon {len(found)} passend.")
        for h in found:
            print(f"  {h['name']} | {h['sitz']} | {h['register']} | {h['status']}")
        hits.extend(found)
    if args.dry_run:
        sys.exit(1 if errors else 0)

    if errors:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] == alert_after:
            notify("HR-Tracker: Abfrage schlägt fehl",
                   f"Die Suche ist {alert_after}× in Folge fehlgeschlagen.\n" + "\n".join(errors),
                   priority="low", tags=["warning"])
    else:
        if state.get("failures", 0) >= alert_after:
            notify("HR-Tracker: wieder in Ordnung", "Die Suche läuft wieder.",
                   priority="low", tags=["white_check_mark"])
        state["failures"] = 0

    new = [h for h in hits if h["register"] not in state["notified"]]
    if new:
        lines = [f"{h['name']}\nSitz: {h['sitz']}\n{h['register']} ({h['land']}), Status: {h['status']}"
                 for h in new]
        notify("Handelsregister: Eintragung gefunden", "\n\n".join(lines),
               priority="high", tags=["tada", "office"])
        state["notified"].extend(h["register"] for h in new)
        print(f"Benachrichtigung für {len(new)} neue(n) Treffer gesendet.")
    save_state(state_path, state)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
