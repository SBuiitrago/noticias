#!/usr/bin/env python3
"""Lee los feeds de feeds.json y genera docs/index.html. Solo librería estándar."""
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (compatible; MisNoticias/1.0)"
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def text_of(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(data):
    """Devuelve lista de dicts {titulo, url, fecha} para RSS 2.0 o Atom."""
    root = ET.fromstring(data)
    items = []
    for it in root.iter("item"):
        link = text_of(it, "link")
        items.append({
            "titulo": text_of(it, "title"),
            "url": link,
            "fecha": parse_date(text_of(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
        })
    for en in root.iter(ATOM + "entry"):
        link = ""
        for l in en.findall(ATOM + "link"):
            if l.get("rel", "alternate") == "alternate" and l.get("href"):
                link = l.get("href")
                break
        items.append({
            "titulo": text_of(en, ATOM + "title"),
            "url": link,
            "fecha": parse_date(text_of(en, ATOM + "published", ATOM + "updated")),
        })
    return items


def load_source(src, cfg):
    try:
        items = parse_feed(fetch(src["url"]))
    except Exception as e:  # una fuente caída no debe romper la página
        return src, [], f"{type(e).__name__}: {e}"
    filtro = [w.lower() for w in src.get("filtro", [])]
    limite = datetime.now(timezone.utc) - timedelta(days=cfg["max_dias"])
    out = []
    for i in items:
        if not i["titulo"] or not i["url"].startswith(("http://", "https://")):
            continue
        if filtro and not any(w in i["titulo"].lower() for w in filtro):
            continue
        if i["fecha"] and i["fecha"] < limite:
            continue
        out.append(i)
    out.sort(key=lambda i: i["fecha"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return src, out[: cfg["max_por_fuente"]], None


def render(cfg, results, ahora):
    tz = ZoneInfo("Europe/Madrid")
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

    def fecha(d):
        if not d:
            return ""
        d = d.astimezone(tz)
        return f"{d.day} {meses[d.month - 1]} · {d:%H:%M}"

    secciones = []
    total = 0
    for sec in cfg["secciones"]:
        bloques = []
        for src, items, err in results[sec["nombre"]]:
            total += len(items)
            if err:
                cuerpo = f'<p class="err">No se pudo leer esta fuente ({escape(err)})</p>'
            elif not items:
                cuerpo = '<p class="vacio">Sin novedades recientes</p>'
            else:
                cuerpo = "<ul>" + "".join(
                    f'<li><a href="{escape(i["url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
                    f'{escape(i["titulo"])}</a><time>{escape(fecha(i["fecha"]))}</time></li>'
                    for i in items
                ) + "</ul>"
            bloques.append(f'<article class="fuente"><h3>{escape(src["nombre"])}</h3>{cuerpo}</article>')
        secciones.append(
            f'<section id="{escape(sec["nombre"].lower().replace(" ", "-"))}"><h2>{escape(sec["nombre"])}</h2>'
            f'<div class="grid">{"".join(bloques)}</div></section>'
        )
    nav = "".join(
        f'<a href="#{escape(s["nombre"].lower().replace(" ", "-"))}">{escape(s["nombre"])}</a>'
        for s in cfg["secciones"]
    )
    act = ahora.astimezone(tz)
    return PAGE.format(
        titulo=escape(cfg["titulo"]),
        nav=nav,
        secciones="".join(secciones),
        actualizado=f"{act.day} {meses[act.month - 1]} {act.year}, {act:%H:%M}",
        total=total,
    )


PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--tx:#1d1d1b;--mut:#6b6a66;--ac:#0b5fff;--bd:#e4e2dc}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--tx:#eceae4;--mut:#9a988f;--ac:#7aa7ff;--bd:#2d2d2a}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--bd);padding:12px 16px;z-index:2}}
header h1{{margin:0;font-size:1.25rem}}
header p{{margin:2px 0 8px;color:var(--mut);font-size:.85rem}}
nav{{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}}
nav a{{white-space:nowrap;padding:4px 12px;border:1px solid var(--bd);border-radius:99px;color:var(--tx);text-decoration:none;font-size:.85rem}}
input{{width:100%;margin-top:8px;padding:8px 12px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--tx);font:inherit}}
main{{max-width:1200px;margin:0 auto;padding:8px 16px 48px}}
section{{padding-top:16px}}
h2{{font-size:1.1rem;margin:0 0 10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
.fuente{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 14px}}
h3{{margin:0 0 6px;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}}
ul{{list-style:none;margin:0;padding:0}}
li{{padding:7px 0;border-top:1px solid var(--bd)}}
li:first-child{{border-top:0}}
li a{{color:var(--tx);text-decoration:none;font-weight:500}}
li a:hover{{color:var(--ac)}}
time{{display:block;color:var(--mut);font-size:.75rem}}
.err,.vacio{{color:var(--mut);font-size:.85rem;margin:4px 0}}
.oculto{{display:none}}
</style>
</head>
<body>
<header>
<h1>{titulo}</h1>
<p>Actualizado {actualizado} · {total} noticias</p>
<nav>{nav}</nav>
<input id="q" type="search" placeholder="Buscar en los titulares…" aria-label="Buscar">
</header>
<main>{secciones}</main>
<script>
var q=document.getElementById('q');
q.addEventListener('input',function(){{
  var t=q.value.toLowerCase();
  document.querySelectorAll('li').forEach(function(li){{
    li.classList.toggle('oculto',t&&li.textContent.toLowerCase().indexOf(t)<0);
  }});
}});
</script>
</body>
</html>
"""


def main():
    cfg = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    fuentes = [(s["nombre"], f) for s in cfg["secciones"] for f in s["fuentes"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda p: (p[0], load_source(p[1], cfg)), fuentes))
    results = {s["nombre"]: [] for s in cfg["secciones"]}
    for sec, r in res:
        results[sec].append(r)
        if r[2]:
            print(f"AVISO {r[0]['nombre']}: {r[2]}", file=sys.stderr)
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(cfg, results, datetime.now(timezone.utc)), encoding="utf-8")
    print("OK -> docs/index.html")


if __name__ == "__main__":
    main()
