"""
Hemisferio — recolector diario de feeds.
Lee feeds.txt, descarga cada feed, filtra por palabras clave de salud mental,
deduplica por URL y guarda:
  - data/signals.json  → histórico (90 días) de todo lo filtrado
  - data/latest.md     → resumen compacto de los últimos 8 días, para leer en Claude
"""
import json, re, hashlib, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
import feedparser, requests

FEEDS_FILE = Path("feeds.txt")
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
JSON_FILE = DATA_DIR / "signals.json"
MD_FILE = DATA_DIR / "latest.md"
KEEP_DAYS = 90
LATEST_DAYS = 8

# --- Palabras clave (minúsculas, sin acentos no hace falta: se normaliza) ---
KEYWORDS = [
    # genéricas
    "salud mental", "saude mental", "mental health", "behavioral health", "behavioural health",
    "psicolog", "psiquiatr", "psychiatr", "psycholog", "terapeut", "therapist", "psicoterap", "psychotherap",
    "bienestar emocional", "bem-estar emocional", "bem estar emocional", "wellbeing", "well-being",
    # trastornos
    "ansiedad", "ansiedade", "anxiety", "depres", "burnout", "estres laboral", "estresse", "stress laboral",
    "suicid", "autolesi", "self-harm", "tdah", "adhd", "autis", "neurodiverg", "trastorno alimentar",
    "transtorno alimentar", "eating disorder", "bipolar", "esquizofren", "schizophren", "insomni", "sueno", "sono",
    # adicciones
    "adicci", "adicao", "addiction", "ludopat", "vicio em apostas", "juego patologico", "gambling",
    # digital / modelos
    "telepsicolog", "teleterapia", "terapia online", "terapia digital", "digital therapeutic", "app de bienestar",
    "chatbot terap", "ia terap", "ai therap", "companion ai",
    # tratamientos emergentes
    "psicodelic", "psychedel", "psilocib", "ketamina", "esketamina", "spravato", "tms", "neuroestimul",
    # regulación / empleo
    "nr-1", "nr1", "riesgos psicosociales", "riscos psicossociais", "psychosocial risk", "baja por ansiedad",
    "incapacidad temporal", "afastamento", "caps ", "raps", "estrategia de salud mental",
]

def norm(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.lower()
    s = (s.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
           .replace("ã","a").replace("õ","o").replace("ç","c").replace("ñ","n").replace("â","a").replace("ê","e").replace("ô","o"))
    return re.sub(r"\s+", " ", s).strip()

def matched(text: str):
    t = norm(text)
    return [k for k in KEYWORDS if k in t]

def read_feeds():
    feeds, folder = [], "SIN CARPETA"
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            folder = line[3:].strip()
        elif line.startswith("http"):
            feeds.append((folder, line))
    return feeds

def load_json():
    if JSON_FILE.exists():
        return json.loads(JSON_FILE.read_text(encoding="utf-8"))
    return []

def entry_date(e):
    for key in ("published_parsed", "updated_parsed"):
        if e.get(key):
            try:
                return datetime(*e[key][:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)

def main():
    now = datetime.now(timezone.utc)
    items = {i["id"]: i for i in load_json()}
    stats = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; HemisferioBot/1.0)"}

    for folder, url in read_feeds():
        n_total = n_kept = 0
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            source = parsed.feed.get("title", url)[:80]
            for e in parsed.entries:
                n_total += 1
                title = html.unescape(e.get("title", ""))
                summary = norm(e.get("summary", "") or e.get("description", ""))[:400]
                link = e.get("link", "")
                if not link:
                    continue
                kws = matched(title + " " + summary)
                if not kws:
                    continue
                d = entry_date(e)
                if d < now - timedelta(days=KEEP_DAYS):
                    continue
                uid = hashlib.md5(link.encode()).hexdigest()[:12]
                if uid not in items:
                    items[uid] = {
                        "id": uid, "fecha": d.date().isoformat(), "carpeta": folder,
                        "fuente": source, "titulo": title, "resumen": summary,
                        "url": link, "keywords": kws, "capturado": now.date().isoformat(),
                    }
                    n_kept += 1
            stats.append((folder, source, n_total, n_kept, "ok"))
        except Exception as ex:
            stats.append((folder, url, 0, 0, f"ERROR {type(ex).__name__}"))

    # purga > 90 días y guarda
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    items = {k: v for k, v in items.items() if v["fecha"] >= cutoff}
    JSON_FILE.write_text(json.dumps(sorted(items.values(), key=lambda x: x["fecha"], reverse=True),
                                    ensure_ascii=False, indent=1), encoding="utf-8")

    # latest.md: últimos 8 días, agrupado por carpeta
    since = (now - timedelta(days=LATEST_DAYS)).date().isoformat()
    recent = [v for v in items.values() if v["fecha"] >= since]
    lines = [f"# Hemisferio — señales {since} → {now.date().isoformat()}",
             f"{len(recent)} ítems filtrados de {sum(s[2] for s in stats)} leídos en {len(stats)} feeds.", ""]
    for folder in sorted({v["carpeta"] for v in recent}):
        lines.append(f"## {folder}")
        for v in sorted([x for x in recent if x["carpeta"] == folder], key=lambda x: x["fecha"], reverse=True):
            lines.append(f"- [{v['fecha']}] **{v['titulo']}** — {v['fuente']}")
            if v["resumen"]:
                lines.append(f"  {v['resumen'][:250]}")
            lines.append(f"  {v['url']}")
        lines.append("")
    lines.append("## Estado de los feeds (hoy)")
    for folder, src, tot, kept, st in stats:
        lines.append(f"- {folder} · {src[:50]} · leídos {tot} · nuevos {kept} · {st}")
    MD_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: {len(recent)} recientes, {len(items)} en histórico")

if __name__ == "__main__":
    main()
