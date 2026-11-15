#!/usr/bin/env python3
"""Lädt die Outlook-GeoJSONs (sturm / gewitter / starkregen) von gewittermodell.de,
schneidet sie auf Deutschland zu und schreibt pro Typ eine fertige <typ>.json.

Nutzung:  python scripts/outlook.py --out output/outlook
"""
import argparse, json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

BASE = "https://gewittermodell.de/autoforecast"
TYPES = {"sturm": "STURM", "gewitter": "GEWITTER", "starkregen": "STARKREGEN"}
DE_FILE = Path(__file__).resolve().parent.parent / "data" / "bund-deutschland.json"
UA = {"User-Agent": "Mozilla/5.0"}
GRID = 1e-5  # Rundung nach dem Zuschnitt (~1 m)


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def iso(s):
    """String -> ISO-8601 UTC (2026-11-15T12:00:00.000Z) oder None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def fix(g):
    return g if g.is_valid else make_valid(g)


def only_polygons(g):
    """Aus dem Ergebnis nur Flächen behalten (Linien/Punkte vom Zuschnitt verwerfen)."""
    if g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else None


def load_germany(path):
    """16 Bundesländer -> ein Deutschland-Polygon."""
    if not Path(path).is_file():
        sys.exit(f"Deutschland-Datei fehlt: {path}")
    de = json.loads(Path(path).read_text(encoding="utf-8"))
    geoms = [fix(shape(f["geometry"])) for f in de.get("features", []) if f.get("geometry")]
    if not geoms:
        sys.exit("Keine Geometrien in der Deutschland-Datei.")
    return unary_union(geoms)


def clip(geom, germany, germany_prep):
    geom = fix(geom)
    if not germany_prep.intersects(geom):
        return None  # komplett außerhalb
    if not germany_prep.contains(geom):  # komplett innerhalb braucht keinen Zuschnitt
        geom = only_polygons(geom.intersection(germany))
        if geom is None:
            return None
    return only_polygons(shapely.set_precision(geom, GRID))


def build(type_, data, germany, germany_prep):
    label = TYPES[type_]
    meta = data.get("metadata") or {}
    issued = iso(meta.get("erstellt"))
    features = []

    for f in data.get("features", []):
        p = f.get("properties") or {}
        if not f.get("geometry") or not float(p.get("stufe") or 0) > 0:
            continue
        g = clip(shape(f["geometry"]), germany, germany_prep)  # Fehler -> Abbruch (Retry im Workflow)
        if g is None:
            continue
        datum = p.get("datum")
        features.append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {
                "id": len(features) + 1,
                "type": p.get("gefahr") or type_,
                "category": label,
                "categoryLabel": f"{label} AUSBLICK",
                "title": p.get("titel"),
                "tag": p.get("tag"),
                "date": datum,
                "validLabel": p.get("gueltig"),
                "level": p.get("stufe"),
                "levelLabel": p.get("stufe_text"),
                "radiusKm": p.get("umkreis_km"),
                "model": p.get("modell"),
                "name": p.get("name"),
                "description": p.get("description"),
                "start": iso(f"{datum}T00:00:00Z") if datum else None,
                "end": iso(f"{datum}T23:59:59Z") if datum else None,
                "issued": issued,
                "clipped": True,
                "fill": p.get("fill"),
                "fillOpacity": p.get("fill-opacity"),
                "stroke": p.get("stroke"),
                "strokeWidth": p.get("stroke-width"),
                "strokeOpacity": p.get("stroke-opacity"),
            },
        })

    # nach Tag, dann Stufe aufsteigend (höchste Stufe liegt oben)
    features.sort(key=lambda x: (x["properties"]["tag"] or 0, x["properties"]["level"] or 0))
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"type": type_, "issued": issued, "clipped": True, "source": meta},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="output/outlook", help="Ausgabeordner")
    ap.add_argument("--de", default=str(DE_FILE), help="Landesgrenzen-GeoJSON")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    germany = load_germany(args.de)
    germany_prep = prep(germany)

    # erst alles berechnen, dann schreiben -> nie halbe Ausgaben
    results = {}
    for t in TYPES:
        url = f"{BASE}/{t}.geojson"
        print(f"[{t}] lade {url}")
        data = json.loads(http_get(url).strip())
        results[t] = build(t, data, germany, germany_prep)
        print(f"[{t}] {len(results[t]['features'])} Features, issued={results[t]['metadata']['issued']}")

    for t, body in results.items():
        path = out / f"{t}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"[{t}] -> {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()