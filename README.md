# Jobby Job Search Dashboard

FastAPI-basierter Job-Finder mit profilgesteuerter Suche. StepStone und Indeed werden bevorzugt über öffentliche Suchmaschinen-Discovery nach konkreten Stellenanzeigen durchsucht. Gespeicherte Browser-Sessions können optional für zusätzliche Treffer verwendet werden.

## Verhalten

- Das gespeicherte Dashboard-Profil bestimmt Anstellungsart, Ort, Rollen, Skills, Keywords, Stunden, Remote-Präferenzen und Ausschlüsse.
- Die Suche erzeugt konkrete Detail-URLs für StepStone und Indeed.
- Portal-Startseiten, Suchseiten, PDFs und sonstige Nicht-Stellen werden verworfen.
- Wenn ein Portal die Detailseite mit 403/429/Anti-Bot-Seiten schützt, wird der konkrete Suchmaschinentreffer nicht automatisch in eine Portal-Startseite umgewandelt.
- Optional kann eine interaktive, persistente Browser-Session über die Login-Seite verwendet werden.
- CAPTCHA, 2FA und Login-/Sicherheitsabfragen werden nicht automatisiert umgangen. Sie werden im Browser manuell durch den Benutzer erledigt.
- Es gibt keine automatischen Bewerbungen und keine Live-Job-Aktionen.

## Render

Docker installiert Chromium, Xvfb und noVNC. Start erfolgt über `start.sh`; externes Port 10000 wird von nginx auf FastAPI weitergeleitet.

Für dauerhafte Daten auf Render PostgreSQL über `DATABASE_URL` verwenden.

## Tests

```bash
pytest -q
```

## Environment

`env.example` enthält die wichtigsten Variablen. `DASHBOARD_ACCESS_TOKEN` schützt die interaktive Login-Route.
