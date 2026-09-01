"""
Hemisferio — recolector diario de feeds (v2).
1. Lee feeds.txt y descarga cada feed (con reintento cambiando de User-Agent).
2. Filtra por TEMA (salud mental) y puntúa por TIPO DE SEÑAL al estilo Hemingway Report:
   hechos con datos (rondas, M&A, lanzamientos, regulación, estudios) suman; opinión, consejos,
   famosos y convocatorias institucionales restan.
3. Deduplica noticias repetidas en varios medios (misma historia → un solo ítem, con recuento).
4. Guarda data/signals.json (histórico 90 días) y data/latest.md (últimos 8 días, ordenado A/B/C).
"""
import json, re, hashlib, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
import feedparser, requests

FEEDS_FILE = Path("feeds.txt")
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
JSON_FILE = DATA_DIR / "signals.json"
MD_FILE = DATA_DIR / "latest.md"
KEEP_DAYS, LATEST_DAYS = 90, 8

# ---------- 1. TEMA: tiene que aparecer al menos una ----------
TOPIC = [
    "salud mental", "saude mental", "mental health", "behavioral health", "behavioural health",
    "psicolog", "psiquiatr", "psychiatr", "psycholog", "psicoterap", "psychotherap", "terapeut", "therapist",
    "bienestar emocional", "bem-estar emocional", "bem estar emocional",
    "ansiedad", "ansiedade", "anxiety", "depres", "burnout", "suicid", "autolesi", "self-harm",
    "tdah", "adhd", "autis", "neurodiverg", "trastorno alimentar", "transtorno alimentar", "eating disorder",
    "bipolar", "esquizofren", "schizophren", "insomni",
    "adicci", "adicao", "addiction", "ludopat", "vicio em apostas", "juego patologico", "gambling",
    "telepsicolog", "teleterapia", "terapia online", "terapia digital", "digital therapeutic",
    "psicodelic", "psychedel", "psilocib", "ketamina", "esketamina", "spravato",
    "nr-1", "riesgos psicosociales", "riscos psicossociais", "psychosocial risk",
    "caps ", "raps ", "estrategia de salud mental", "plano de saude mental",
    # players conocidos (así entran aunque el titular no diga "salud mental")
    "headspace", "calm app", "betterhelp", "talkspace", "lyra health", "spring health", "kooth", "wysa", "woebot",
    "ifeel", "unobravo", "therapyside", "mindfully", "zenklub", "vittude", "conexa saude", "koa health",
    "psicologia y mente", "wellhub", "gympass", "mena.ai",
]

# ---------- 2. TIPO DE SEÑAL (estilo Hemingway): peso y categoría ----------
SIGNAL = {
    "deal": (4, ["ronda de financiacion", "ronda de inversion", "cierra una ronda", "cierra ronda", "rodada de investimento",
                 "rodada de", "levanta", "capta", "recauda", "raises", "raised", "funding", "inversion de",
                 "investimento de", "aporte de", "serie a", "serie b", "seed", "venture", "fondo de", "fundo de",
                 "valoracion", "valuation", "financiacion de", "financiamento de"]),
    "ma":   (4, ["adquiere", "adquire", "acquires", "acquisition", "compra la", "compra a", "fusion", "fusao",
                 "merger", "opa ", "integra a", "absorve"]),
    "producto": (3, ["lanza", "lanca", "launches", "presenta su", "nueva app", "novo app", "plataforma",
                     "startup", "healthtech", "chatbot", "inteligencia artificial", "ia ", "ai "]),
    "partnership": (3, ["acuerdo con", "acordo com", "alianza", "parceria", "partnership", "partners with",
                        "convenio", "convenio", "contrato con", "licitacion", "licitacao", "adjudica"]),
    "regulacion": (4, ["ley ", "lei ", "decreto", "regulacion", "regulamenta", "regulation", "senado", "congreso",
                       "congresso", "camara", "parlamento", "aprueba", "aprova", "boe", "anvisa", "aemps",
                       "cofepris", "anmat", "ans ", "cfp ", "cfm ", "reforma", "proyecto de ley", "projeto de lei",
                       "portaria", "resolucion", "resolucao", "sancion", "multa"]),
    "research": (3, ["estudio", "estudo", "study", "ensayo clinico", "ensaio clinico", "trial", "investigacion", "pesquisa",
                     "% de", "por ciento", "por cento", "percent", "encuesta", "inquerito", "survey", "datos",
                     "dados", "informe", "relatorio", "report", "casos", "atenciones", "atendimentos", "prevalencia",
                     "consultas", "diagnosticos", "pacientes atendidos"]),
    "mercado": (3, ["cierra", "cierre", "cerro", "fecha as portas", "encerra", "shuts down", "closes", "quiebra",
                    "falencia", "concurso de acreedores", "despidos", "layoffs", "demissoes", "facturacion",
                    "faturamento", "ingresos", "receita", "resultados", "earnings", "ebitda", "cotiza", "ipo",
                    "expande", "expansion", "abre en", "chega ao brasil", "llega a espana", "entra en"]),
    "sistema_publico": (2, ["sanidad", "ministerio", "ministerio", "sus ", "sns ", "hospital", "unidad de",
                            "unidade de", "atencion primaria", "atencao primaria", "inversion publica",
                            "presupuesto", "orcamento", "comunidad", "generalitat", "junta", "prr ", "fondos"]),
    "empleador_aseguradora": (3, ["aseguradora", "seguradora", "operadora", "plano de saude", "seguro",
                                  "empresa", "empleados", "funcionarios", "trabajadores", "trabalhadores",
                                  "employer", "insurer", "mutua", "nr-1", "riesgos psicosociales", "riscos psicossociais"]),
}
HARD_FACT = ["millones", "milhoes", "million", "%", "por ciento", "por cento", "euros", "reais", "dolares",
             "usd", "r$", "€", "$"]  # +2 si hay número/cantidad

# ---------- 3. RUIDO: resta ----------
NOISE = [
    # consejos / consumo
    "consejos", "dicas", "tips", "como superar", "como lidiar", "como combatir", "como saber si", "que es ",
    "por que aparece", "senales de", "sinais de", "claves para", "habitos", "rutina", "meditacion", "mindfulness",
    "horoscopo", "zodiaco", "receta", "ejercicio", "dieta",
    # opinión / entrevista blanda
    "opinion", "columna", "editorial", "tribuna", "entrevista", "reflexion", "filosof", "philosoph", "ensayo sobre", "libro",
    # famosos / deporte
    "selena gomez", "famoso", "celebrid", "cantante", "actor", "actriz", "futbolista", "jugador", "influencer",
    "trump", "reality",
    # convocatorias institucionales
    "inscricoes", "inscripciones", "webinar", "webinario", "live ", "roda de conversa", "mostra", "seminario",
    "jornada", "congreso de", "evento", "premio", "concurso", "campanha", "campaña", "dia mundial", "dia de la",
    # sucesos / casos individuales
    "asesin", "homicid", "detenid", "juicio", "condena", "denuncia a",
]

def norm(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s).lower()
    for a, b in zip("áéíóúãõçñâêôàü", "aeiouaocnaeoau"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()

def has(word, t):
    return re.search(r"(?<![a-z0-9])" + re.escape(word), t) is not None

def score_item(title, summary):
    t = norm(title + " " + summary)
    topic = [k for k in TOPIC if has(k, t)]
    if not topic:
        return None
    pts, cats = 0, {}
    for cat, (w, words) in SIGNAL.items():
        hits = [x for x in words if has(x, t)]
        if hits:
            pts += w; cats[cat] = len(hits)
    if any(has(x, t) for x in HARD_FACT) or re.search(r"\d", norm(title)):
        pts += 2
    noise = [n for n in NOISE if has(n, t)]
    pts -= 3 * len(noise)
    cat = max(cats, key=cats.get) if cats else "otro"
    prio = "A" if pts >= 6 else "B" if pts >= 3 else "C"
    return {"puntos": pts, "prioridad": prio, "categoria_auto": cat, "topic": topic[:3], "ruido": noise[:3]}

def title_key(title):
    t = norm(re.sub(r"\s[-–|]\s[^-–|]{2,40}$", "", title))  # quita " - Medio" al final
    words = [w for w in re.findall(r"[a-z0-9]{4,}", t) if w not in
             {"para", "sobre", "como", "desde", "entre", "salud", "mental", "saude", "health", "sobre", "contra"}]
    return " ".join(sorted(set(words))[:8])

def read_feeds():
    feeds, folder, label = [], "SIN CARPETA", None
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            folder, label = line[3:].strip(), None
        elif line.endswith(":") and not line.startswith("http"):
            label = line[:-1].strip()
        elif line.startswith("http"):
            feeds.append((folder, label, line)); label = None
    return feeds

def fetch(url):
    uas = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Mozilla/5.0 (compatible; HemisferioBot/1.0; +https://github.com)"]
    last = None
    for ua in uas:
        try:
            r = requests.get(url, headers={"User-Agent": ua, "Accept": "application/rss+xml, application/xml, text/xml, */*"}, timeout=30)
            r.raise_for_status()
            return r.content
        except Exception as ex:
            last = ex
    raise last

def entry_date(e):
    for key in ("published_parsed", "updated_parsed"):
        if e.get(key):
            try: return datetime(*e[key][:6], tzinfo=timezone.utc)
            except Exception: pass
    return datetime.now(timezone.utc)

def main():
    now = datetime.now(timezone.utc)
    items = {i["id"]: i for i in (json.loads(JSON_FILE.read_text(encoding="utf-8")) if JSON_FILE.exists() else [])}
    stats = []
    for folder, label, url in read_feeds():
        n_total = n_kept = 0
        try:
            parsed = feedparser.parse(fetch(url))
            is_gnews = "news.google.com" in url
            source = f"Google News · {label}" if is_gnews else (parsed.feed.get("title") or url)[:60]
            for e in parsed.entries:
                n_total += 1
                title = html.unescape(e.get("title", "")).strip()
                link = e.get("link", "")
                if not link or not title:
                    continue
                medio = source
                if is_gnews and " - " in title:              # "Titular - Medio"
                    title, medio = title.rsplit(" - ", 1)
                summary = norm(e.get("summary", "") or e.get("description", ""))[:400]
                if is_gnews and norm(title) in summary:      # el resumen de GN es el título repetido
                    summary = ""
                sc = score_item(title, summary)
                if not sc:
                    continue
                d = entry_date(e)
                if d < now - timedelta(days=KEEP_DAYS):
                    continue
                uid = hashlib.md5(link.encode()).hexdigest()[:12]
                if uid in items:
                    continue
                items[uid] = {"id": uid, "fecha": d.date().isoformat(), "carpeta": folder, "feed": source,
                              "medio": medio.strip(), "titulo": title.strip(), "resumen": summary, "url": link,
                              "clave": title_key(title), "capturado": now.date().isoformat(), **sc}
                n_kept += 1
            stats.append((folder, source, n_total, n_kept, "ok"))
        except Exception as ex:
            stats.append((folder, label or url, 0, 0, f"ERROR {type(ex).__name__}"))

    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    items = {k: v for k, v in items.items() if v["fecha"] >= cutoff}
    JSON_FILE.write_text(json.dumps(sorted(items.values(), key=lambda x: (x["fecha"], x["puntos"]), reverse=True),
                                    ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- latest.md: últimos 8 días, deduplicado por historia, ordenado por prioridad ----
    since = (now - timedelta(days=LATEST_DAYS)).date().isoformat()
    recent = [v for v in items.values() if v["fecha"] >= since]
    groups = {}
    for v in sorted(recent, key=lambda x: -x["puntos"]):
        groups.setdefault(v["clave"], []).append(v)
    stories = []
    for g in groups.values():
        lead = g[0]; lead = dict(lead); lead["otros_medios"] = [x["medio"] for x in g[1:]]
        stories.append(lead)
    stories.sort(key=lambda x: (-x["puntos"], x["fecha"]), reverse=False)

    L = [f"# Hemisferio — señales {since} → {now.date().isoformat()}",
         f"{len(recent)} ítems con tema salud mental de {sum(s[2] for s in stats)} leídos · {len(stories)} historias únicas · "
         f"A={sum(s['prioridad']=='A' for s in stories)} B={sum(s['prioridad']=='B' for s in stories)} C={sum(s['prioridad']=='C' for s in stories)}", ""]
    for prio, titulo in (("A", "PRIORIDAD A — hechos con datos (leer artículo completo)"),
                         ("B", "PRIORIDAD B — contexto útil"),
                         ("C", "PRIORIDAD C — probable ruido (solo títulos)")):
        sub = [s for s in stories if s["prioridad"] == prio]
        if not sub: continue
        L.append(f"## {titulo}")
        for s in sub:
            extra = f" (+{len(s['otros_medios'])} medios más)" if s["otros_medios"] else ""
            if prio == "C":
                L.append(f"- [{s['fecha']}] {s['titulo']} — {s['medio']}{extra}")
                continue
            L.append(f"- [{s['fecha']}] [{s['carpeta'][:2]}] **{s['titulo']}** — {s['medio']}{extra} · cat≈{s['categoria_auto']} · {s['puntos']} pts")
            if s["resumen"]:
                L.append(f"  {s['resumen'][:220]}")
            L.append(f"  {s['url']}")
        L.append("")
    L.append("## Estado de los feeds (hoy)")
    for folder, src, tot, kept, st in stats:
        L.append(f"- {folder[:10]} · {src[:55]} · leídos {tot} · nuevos {kept} · {st}")
    MD_FILE.write_text("\n".join(L), encoding="utf-8")
    print(f"OK: {len(stories)} historias recientes, {len(items)} en histórico")

if __name__ == "__main__":
    main()
