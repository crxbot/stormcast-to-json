# outlook-gewittermodell

Lädt die Ausblick-Karten (Sturm, Gewitter, Starkregen) von
[gewittermodell.de](https://gewittermodell.de/autoforecast), schneidet sie auf
Deutschland zu und lädt das Ergebnis nach Cloudflare R2.

## Ablauf

1. `scripts/outlook.py` lädt `sturm`, `gewitter` und `starkregen` als GeoJSON.
2. StormCast schneidet auf eine vereinfachte Grenze zu
   ([grenze_deutschland.geojson](https://gewittermodell.de/autoforecast/grenze_deutschland.geojson)).
   Gegenüber der eigenen Grenze (`data/bund-deutschland.json`) entstehen dadurch Lücken am Rand
   und kleine Löcher im Land. Das Script berechnet *eigene Grenze minus StormCast-Grenze* und füllt
   diese Bereiche pro Tag mit der jeweils nächstgelegenen Stufe auf (bei Gleichstand gewinnt die höhere).
3. Danach wird bündig auf die eigene Grenze zugeschnitten (Löcher höherer Stufen bleiben erhalten,
   die Stufen überlappen sich nicht).
4. Pro Typ entsteht `output/outlook/<typ>.json` (FeatureCollection, Meta unter `metadata`).
5. Der Workflow lädt die Dateien unverändert nach `s3://<bucket>/outlook/`.

Stellschrauben stehen oben im Script: `FILL_STEPS` (maximale Reichweite beim Auffüllen, in Grad),
`FILL_EPS` (Überlappung zur Datenkante) und `GRID` (Koordinatenrundung).

## Einrichtung

1. `data/bund-deutschland.json` (Polygone der 16 Bundesländer) liegt bereits im Repo. Das ist die Grenze, auf die zugeschnitten wird.
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
