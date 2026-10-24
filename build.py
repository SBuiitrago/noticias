#!/usr/bin/env python3
"""Lee los feeds de feeds.json y genera docs/index.html. Solo librería estándar.

Uso:
    python build.py                      genera docs/index.html
    python build.py --comprobar          solo informa de qué fuentes funcionan (no escribe nada)
    python build.py --fecha 2028-07-20   simula otra fecha (para ver secciones por temporada)

Opciones de feeds.json (todas opcionales):
  Globales:  max_por_fuente, max_dias, deduplicar (true/false), similitud (0-1, más bajo = más agresivo)
  Sección:   prioridad (0 = primero; quien procesa antes se queda con la noticia repetida),
             max_por_fuente, max_dias, activa (ventana o lista de ventanas {"desde","hasta"})
  Fuente:    max_por_fuente, max_dias, activa,
             filtro (el titular debe contener alguna), excluir (el titular no debe contener ninguna),
             incluir_url / excluir_url (trozos que debe / no debe tener el enlace)
"""
import json
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (compatible; MisNoticias/1.0)"
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
TZ = ZoneInfo("Europe/Madrid")
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ---------------------------------------------------------------- descarga y lectura

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def descargar(urls):
    """Descarga cada URL una sola vez. Devuelve {url: (datos|None, error|None)}."""
    def uno(u):
        try:
            return u, (fetch(u), None)
        except Exception as e:  # una fuente caída no debe romper la página
            return u, (None, f"{type(e).__name__}: {e}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(uno, urls))


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
    """Devuelve lista de dicts {titulo, url, fecha} para RSS 2.0, RSS 1.0 (RDF) o Atom."""
    root = ET.fromstring(data)
    items = []
    for it in root.iter("item"):
        items.append({
            "titulo": text_of(it, "title"),
            "url": text_of(it, "link"),
            "fecha": parse_date(text_of(it, "pubDate", DC + "date")),
        })
    for it in root.iter(RSS1 + "item"):
        items.append({
            "titulo": text_of(it, RSS1 + "title"),
            "url": text_of(it, RSS1 + "link"),
            "fecha": parse_date(text_of(it, DC + "date")),
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


# ---------------------------------------------------------------- ventanas de fechas

def activa(obj, hoy):
    """True si el objeto (sección o fuente) no tiene ventana 'activa' o hoy cae dentro de alguna."""
    v = obj.get("activa")
    if v is None:
        return True
    ventanas = v if isinstance(v, list) else [v]
    for w in ventanas:
        desde = date.fromisoformat(w["desde"]) if w.get("desde") else date.min
        hasta = date.fromisoformat(w["hasta"]) if w.get("hasta") else date.max
        if desde <= hoy <= hasta:
            return True
    return False


# ---------------------------------------------------------------- deduplicación

TRACKING = {
    "fbclid", "gclid", "ref", "xtor", "cmp", "ocid", "rss", "rssid", "sref", "taid",
    "mc_cid", "mc_eid", "source", "feed", "oc", "at_medium", "at_campaign", "at_source",
    "at_format", "at_ptr_name", "at_link_type", "at_link_origin", "at_link_id", "at_bbc_team",
    "at_campaign_type", "at_custom1", "at_custom2", "at_custom3", "at_custom4",
}

STOP = set("""
el la los las un una unos unas del al con por para sin sobre entre tras hasta desde que
como mas pero muy hay tiene tienen todo todos cuando donde quien cual esta este estos estas
ante bajo contra durante segun otros otras otro otra nuevo nueva nuevos nuevas primer primera
ultimo ultima hoy ayer manana tras puede podria ser fue son han sus
the and for with from after before over into about amid says say new how why what who will
has have not but are was this that these its you your their they them than then also
""".split())


def url_canonica(url):
    """Normaliza un enlace para detectar el mismo artículo (sin utm, sin www, sin barra final)."""
    p = urlsplit(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    if host == "news.google.com":  # el identificador va en la ruta; la query no importa
        return host + p.path
    qs = [(k, v) for k, v in parse_qsl(p.query)
          if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return host + p.path.rstrip("/") + ("?" + urlencode(qs) if qs else "")


def sin_medio(titulo):
    """Google News añade ' - Medio' al final del titular; se quita para comparar."""
    base, sep, medio = titulo.rpartition(" - ")
    return base if sep and 0 < len(medio) <= 45 else titulo


def tokens(titulo, url=""):
    """Conjunto de raíces (5 letras) de las palabras significativas del titular."""
    if "news.google.com" in url:
        titulo = sin_medio(titulo)
    t = unicodedata.normalize("NFKD", titulo.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    out = set()
    for w in re.findall(r"[a-z0-9]+", t):
        if w in STOP:
            continue
        if w.isdigit():
            if len(w) >= 2:
                out.add(w)
        elif len(w) >= 3:
            out.add(w[:5])
    return frozenset(out)


def parecidos(a, b, umbral):
    """Dos titulares hablan de lo mismo si comparten al menos 3 raíces y se solapan mucho."""
    inter = len(a & b)
    if inter < 3:
        return False
    return inter / len(a | b) >= umbral or inter / min(len(a), len(b)) >= 0.8


# ---------------------------------------------------------------- selección de noticias

def candidatas(src, sec, cfg, datos, err, ahora):
    """Noticias de una fuente tras aplicar filtros y ventana de días, de más nueva a más vieja."""
    if err:
        return [], err
    try:
        items = parse_feed(datos)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    dias = src.get("max_dias", sec.get("max_dias", cfg["max_dias"]))
    limite = ahora - timedelta(days=dias)
    filtro = [w.lower() for w in src.get("filtro", [])]
    excluir = [w.lower() for w in src.get("excluir", [])]
    url_si = [w.lower() for w in src.get("incluir_url", [])]
    url_no = [w.lower() for w in src.get("excluir_url", [])]
    out = []
    for i in items:
        if not i["titulo"] or not i["url"].startswith(("http://", "https://")):
            continue
        t, u = i["titulo"].lower(), i["url"].lower()
        if filtro and not any(w in t for w in filtro):
            continue
        if excluir and any(w in t for w in excluir):
            continue
        if url_si and not any(w in u for w in url_si):
            continue
        if url_no and any(w in u for w in url_no):
            continue
        if i["fecha"] and i["fecha"] < limite:
            continue
        out.append(i)
    out.sort(key=lambda i: i["fecha"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out, None


def seleccionar(cfg, secciones, crudos, ahora):
    """Elige las noticias de cada fuente evitando repetidos.

    secciones: [(seccion, [fuentes activas])] en orden de pantalla.
    Se procesa por 'prioridad' (menor primero) y, dentro de ella, por orden de pantalla; la
    primera aparición de una noticia es la que se queda. El tope por fuente se aplica después
    de descartar repetidas, así que cada fuente rellena con noticias más antiguas si hace falta.
    Devuelve ([(seccion, [(fuente, noticias, error, n_candidatas)])], repetidas_descartadas).
    """
    umbral = cfg.get("similitud", 0.4)
    dedupe = cfg.get("deduplicar", True)
    orden = sorted(range(len(secciones)), key=lambda k: (secciones[k][0].get("prioridad", 1), k))
    vistas_url, vistas_tok = set(), []
    hecho = {}
    repetidas = 0
    for k in orden:
        sec, fuentes = secciones[k]
        bloques = []
        for src in fuentes:
            datos, err = crudos[src["url"]]
            items, err = candidatas(src, sec, cfg, datos, err, ahora)
            tope = src.get("max_por_fuente", sec.get("max_por_fuente", cfg["max_por_fuente"]))
            elegidas = []
            for i in items:
                if len(elegidas) >= tope:
                    break
                u = url_canonica(i["url"])
                tk = tokens(i["titulo"], i["url"])
                if dedupe and (u in vistas_url or any(parecidos(tk, o, umbral) for o in vistas_tok)):
                    repetidas += 1
                    continue
                vistas_url.add(u)
                vistas_tok.append(tk)
                elegidas.append(i)
            bloques.append((src, elegidas, err, len(items)))
        hecho[k] = bloques
    return [(secciones[k][0], hecho[k]) for k in range(len(secciones))], repetidas


# ---------------------------------------------------------------- HTML

def slug(texto):
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def render(cfg, datos, ahora, repetidas=0):
    def fecha(d):
        if not d:
            return ""
        d = d.astimezone(TZ)
        return f"{d.day} {MESES[d.month - 1]} · {d:%H:%M}"

    secciones = []
    total = 0
    for sec, bloques in datos:
        tarjetas = []
        for src, items, err, _ in bloques:
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
            tarjetas.append(f'<article class="fuente"><h3>{escape(src["nombre"])}</h3>{cuerpo}</article>')
        secciones.append(
            f'<section id="{slug(sec["nombre"])}"><h2>{escape(sec["nombre"])}</h2>'
            f'<div class="grid">{"".join(tarjetas)}</div></section>'
        )
    nav = "".join(f'<a href="#{slug(s["nombre"])}">{escape(s["nombre"])}</a>' for s, _ in datos)
    act = ahora.astimezone(TZ)
    extra = f" · {repetidas} repetidas omitidas" if repetidas else ""
    return PAGE.format(
        titulo=escape(cfg["titulo"]),
        nav=nav,
        secciones="".join(secciones),
        actualizado=f"{act.day} {MESES[act.month - 1]} {act.year}, {act:%H:%M}",
        total=total,
        extra=extra,
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
<p>Actualizado {actualizado} · {total} noticias{extra}</p>
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


# ---------------------------------------------------------------- programa principal

def main(argv):
    comprobar = "--comprobar" in argv
    ahora = datetime.now(timezone.utc)
    hoy = ahora.astimezone(TZ).date()
    if "--fecha" in argv:
        hoy = date.fromisoformat(argv[argv.index("--fecha") + 1])

    cfg = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    secciones = []
    for sec in cfg["secciones"]:
        if not activa(sec, hoy):
            continue
        fuentes = [f for f in sec["fuentes"] if activa(f, hoy)]
        secciones.append((sec, fuentes))

    urls = sorted({f["url"] for _, fs in secciones for f in fs})
    crudos = descargar(urls)
    datos, repetidas = seleccionar(cfg, secciones, crudos, ahora)

    fallos = 0
    for sec, bloques in datos:
        for src, items, err, n in bloques:
            if err:
                fallos += 1
                print(f"FALLA [{sec['nombre']}] {src['nombre']}: {err}",
                      file=sys.stdout if comprobar else sys.stderr)
            elif comprobar:
                print(f"OK    [{sec['nombre']}] {src['nombre']}: {n} candidatas, {len(items)} mostradas")

    if comprobar:
        print(f"\n{len(urls)} feeds distintos, {fallos} fuentes con error, {repetidas} repetidas descartadas")
        return 0

    total = sum(len(i) for _, b in datos for _, i, _, _ in b)
    if total == 0:
        print("ERROR: no se obtuvo ninguna noticia; no se sobrescribe la página.", file=sys.stderr)
        return 1

    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(cfg, datos, ahora, repetidas), encoding="utf-8")
    print(f"OK -> docs/index.html ({total} noticias, {repetidas} repetidas omitidas, {fallos} fuentes con error)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
