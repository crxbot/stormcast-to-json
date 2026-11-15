# outlook-gewittermodell

Lädt die Ausblick-Karten (Sturm, Gewitter, Starkregen) von
[gewittermodell.de](https://gewittermodell.de/autoforecast), schneidet sie auf
Deutschland zu und lädt das Ergebnis nach Cloudflare R2.

## Ablauf

1. `scripts/outlook.py` lädt `sturm`, `gewitter` und `starkregen` als GeoJSON.
2. Die 16 Bundesländer aus `data/bund-deutschland.json` werden zu einem Polygon verschmolzen
   und die Features damit zugeschnitten (Löcher höherer Stufen bleiben erhalten).
3. Pro Typ entsteht `output/outlook/<typ>.json` (FeatureCollection, Meta unter `metadata`).
4. Der Workflow lädt die Dateien unverändert nach `s3://<bucket>/outlook/`.

## Einrichtung

1. **`data/bund-deutschland.json`** hinzufügen (Polygone der 16 Bundesländer als GeoJSON).
2. Secrets im Repo anlegen (Settings → Secrets and variables → Actions):
   - `R2_BUCKET`
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
3. Workflow `Fetch Outlook Files and Generate Files` per `workflow_dispatch` starten
   (extern per API/Cron-Dienst oder über den `schedule`-Block in der Workflow-Datei).

## Lokal testen

```bash
pip install -r requirements.txt
python scripts/outlook.py --out output/outlook
```

## Ausgabe

| Datei | Inhalt |
|---|---|
| `outlook/sturm.json` | Sturm-Ausblick, Tag 1–8 |
| `outlook/gewitter.json` | Gewitter-Ausblick, Tag 1–8 |
| `outlook/starkregen.json` | Starkregen-Ausblick, Tag 1–8 |

Die Dateien liegen als Klartext-JSON in R2 (`Content-Type: application/json`).
Der Ausgabezeitpunkt steht in `metadata.issued` und in `properties.issued` jedes Features.

## Features

Jedes Feature = ein Tag (`tag` 1–8) + eine Stufe (`level`), (Multi)Polygon. Die Stufen sind
überschneidungsfrei, ein Punkt trifft pro Tag genau eine Stufe.
