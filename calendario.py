"""Calendario económico (carpetas rojas y naranjas de Forex Factory) por ntfy.

  python calendario.py noche       Todas las noches a las 21 h (España): guarda la semana de
                                   Forex Factory, el domingo manda el resumen semanal y deja
                                   programados en ntfy los recordatorios de 30 min antes.
  python calendario.py resultados  Cada 10 min: cuando sale un dato, manda cómo salió frente a lo
                                   esperado y al anterior (dato real del calendario de Nasdaq,
                                   porque Forex Factory no deja entrar a los servidores de GitHub).
Opciones: --forzar (ignora la hora), --prueba (imprime, no envía ni guarda).
El topic de ntfy va en el secreto NTFY_TOPIC (este repo es público).
"""
import difflib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DIR = Path(__file__).resolve().parent
CONFIG = DIR / "config.json"
ESTADO = DIR / "estado.json"
SEMANA = DIR / "semana.json"
ES = ZoneInfo("Europe/Madrid")
NY = ZoneInfo("America/New_York")
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/economicevents?date={}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json"}
IMPACTO = {"High": "🔴", "Medium": "🟠"}
DIVISA = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CAD": "🇨🇦", "AUD": "🇦🇺",
          "NZD": "🇳🇿", "CHF": "🇨🇭", "CNY": "🇨🇳"}
PAISES_NASDAQ = {"USD": {"United States"}, "GBP": {"United Kingdom"}, "JPY": {"Japan"},
                 "EUR": {"Euro Zone", "Germany", "France", "Italy", "Spain"},
                 "CAD": {"Canada"}, "AUD": {"Australia"}, "NZD": {"New Zealand"},
                 "CHF": {"Switzerland"}, "CNY": {"China"}}
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
HORA_NOCHE = 21            # hora de España del envío nocturno
MINUTOS_ANTES = 30
VENTANA_H = 26             # recordatorios que se programan en cada pasada nocturna
LIMITE_NTFY = 3800         # bytes; por encima ntfy lo convierte en adjunto
# Resultados: eventos que empiezan entre 40 min atrás y 11 min adelante; se espera al dato hasta
# 10 min después de la hora (Nasdaq suele tardar segundos).
RES_ATRAS, RES_ADELANTE, RES_ESPERA = 40, 11, 10
RES_TARDE_H = 8           # datos que se retrasan: se siguen buscando (sin esperar) hasta 8 h

# (fragmento del título en minúsculas, nombre en español, qué es / por qué importa).
# Se usa la primera coincidencia, así que lo más específico va antes.
EVENTOS = [
    ("adp non-farm", "Empleo privado ADP",
     "Anticipo del informe oficial de empleo del viernes."),
    ("non-farm employment change", "Nóminas no agrícolas (NFP)",
     "Empleos creados el mes pasado. Un dato fuerte suele subir el dólar y los rendimientos; "
     "uno débil refuerza las bajadas de tipos."),
    ("unemployment claims", "Peticiones semanales de subsidio por desempleo",
     "Más peticiones = mercado laboral más débil."),
    ("unemployment rate", "Tasa de paro", "Paro al alza = economía enfriándose."),
    ("claimant count", "Solicitudes de subsidio (Reino Unido)", "Termómetro del empleo británico."),
    ("employment change", "Variación del empleo", "Empleos creados en el periodo."),
    ("average hourly earnings", "Salario medio por hora",
     "Presión salarial: si sube más de lo previsto alimenta la inflación."),
    ("core pce", "PCE subyacente (inflación)",
     "La medida de inflación favorita de la Fed. Por encima de lo previsto aleja las bajadas de tipos."),
    ("trimmed mean cpi", "IPC media recortada (inflación subyacente)",
     "Inflación sin extremos, la que más mira el banco central."),
    ("core cpi", "IPC subyacente (inflación sin energía ni alimentos)",
     "Por encima de lo previsto suele presionar a la bolsa y subir la divisa."),
    ("cpi", "IPC (inflación)",
     "Por encima de lo previsto: presión para mantener o subir tipos; suele pesar sobre la bolsa."),
    ("core ppi", "IPP subyacente (precios de producción)", "Anticipa la inflación que llega al consumidor."),
    ("ppi", "IPP (precios de producción)", "Anticipa la inflación que llega al consumidor."),
    ("gdp price index", "Deflactor del PIB", "Inflación medida dentro del PIB."),
    ("gdp", "PIB", "Crecimiento de la economía."),
    ("core retail sales", "Ventas minoristas subyacentes", "Consumo sin automóviles."),
    ("retail sales", "Ventas minoristas", "Fuerza del consumo."),
    ("ism manufacturing pmi", "PMI manufacturero ISM", "Por encima de 50 = industria en expansión."),
    ("ism services pmi", "PMI de servicios ISM", "Por encima de 50 = servicios en expansión."),
    ("pmi", "PMI", "Encuesta a gerentes de compras: por encima de 50 = expansión."),
    ("federal funds rate", "Decisión de tipos de la Fed", "Mueve todos los mercados."),
    ("fomc statement", "Comunicado de la Fed", "Pistas sobre las próximas decisiones de tipos."),
    ("fomc press conference", "Rueda de prensa de la Fed", "Suele generar mucha volatilidad."),
    ("fomc meeting minutes", "Actas de la Fed", "Detalle del debate de la última reunión."),
    ("fomc economic projections", "Proyecciones económicas de la Fed", "Incluye el gráfico de puntos de tipos."),
    ("main refinancing rate", "Decisión de tipos del BCE", "Mueve el euro y las bolsas europeas."),
    ("monetary policy statement", "Comunicado de política monetaria", "Pistas sobre los próximos tipos."),
    ("official bank rate", "Decisión de tipos del Banco de Inglaterra", "Mueve la libra."),
    ("overnight rate", "Decisión de tipos del Banco de Canadá", "Mueve el dólar canadiense."),
    ("cash rate", "Decisión de tipos", "Decisión del banco central sobre el tipo oficial."),
    ("policy rate", "Decisión de tipos", "Decisión del banco central sobre el tipo oficial."),
    ("rate statement", "Comunicado de tipos", "Explica la decisión de tipos y lo que viene."),
    ("press conference", "Rueda de prensa del banco central", "Suele generar volatilidad."),
    ("consumer confidence", "Confianza del consumidor", "Anticipa el gasto de los hogares."),
    ("consumer sentiment", "Sentimiento del consumidor (U. Michigan)",
     "Confianza de los hogares y expectativas de inflación."),
    ("jolts", "Ofertas de empleo JOLTS", "Demanda de trabajadores de las empresas."),
    ("crude oil inventories", "Inventarios de crudo (EE. UU.)",
     "Más inventario de lo previsto suele bajar el petróleo."),
    ("trade balance", "Balanza comercial", "Exportaciones menos importaciones."),
    ("empire state", "Índice manufacturero Empire State (Nueva York)", "Por encima de 0 = expansión."),
    ("philly fed", "Índice manufacturero de la Fed de Filadelfia", "Por encima de 0 = expansión."),
    ("durable goods", "Pedidos de bienes duraderos", "Inversión de empresas y hogares."),
    ("building permits", "Permisos de construcción", "Anticipa la actividad inmobiliaria."),
    ("housing starts", "Inicio de viviendas", "Actividad del sector inmobiliario."),
    ("pending home sales", "Ventas de viviendas pendientes", "Anticipa las ventas de viviendas."),
    ("existing home sales", "Ventas de viviendas usadas", "Salud del mercado inmobiliario."),
    ("new home sales", "Ventas de viviendas nuevas", "Salud del mercado inmobiliario."),
    ("speaks", None, "Discurso: puede mover el mercado si da pistas sobre tipos, aranceles o economía."),
    ("testifies", None, "Comparecencia: atención a pistas sobre tipos de interés."),
]
# Nombres de Forex Factory que Nasdaq llama distinto (en minúsculas, ya normalizados).
ALIAS_NASDAQ = {
    "non-farm employment change": "nonfarm payrolls",
    "adp non-farm employment change": "adp nonfarm employment change",
    "unemployment claims": "initial jobless claims",
    "prelim uom consumer sentiment": "michigan consumer sentiment",
    "revised uom consumer sentiment": "michigan consumer sentiment",
    "federal funds rate": "fed interest rate decision",
    "main refinancing rate": "ecb interest rate decision",
    "official bank rate": "boe interest rate decision",
    "boj policy rate": "boj interest rate decision",
}


# ---------------------------------------------------------------- utilidades

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)


def traducir(texto):
    """Al español con el traductor gratuito de Google; si falla, se deja el original."""
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=es&dt=t&q="
           + urllib.parse.quote(texto))
    try:
        return "".join(p[0] for p in get_json(url)[0] if p[0]) or texto
    except Exception as e:  # noqa: BLE001
        print(f"traducción: {e}")
        return texto


def publicar(titulo, mensaje, prioridad=3, en=None):
    cuerpo = {"topic": os.environ["NTFY_TOPIC"], "title": titulo, "message": mensaje,
              "priority": prioridad}
    if en:
        cuerpo["delay"] = str(int(en.timestamp()))
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(cuerpo).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def cargar(ruta, defecto):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else defecto


def guardar(ruta, datos):
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def info_evento(titulo):
    t = titulo.lower()
    for clave, nombre, que_es in EVENTOS:
        if clave in t:
            return nombre or traducir(titulo), que_es
    return traducir(titulo), ""


def numero(x):
    m = re.match(r"^\s*(-?[\d,.]+)\s*([%KMBT]?)", html.unescape(x or "").replace("\xa0", " "))
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v * {"": 1, "%": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]


PAIS_EURO = {"german": "Alemania", "french": "Francia", "italian": "Italia", "spanish": "España"}


def cabecera_evento(e):
    nombre, _ = info_evento(e["title"])
    pais = PAIS_EURO.get(e["title"].split()[0].lower())
    return (f"{IMPACTO[e['impact']]} {DIVISA.get(e['country'], '')}{e['country']} · "
            f"{pais + ': ' if pais else ''}{nombre}")


# ---------------------------------------------------------------- Forex Factory

def descargar_semana(cfg):
    """Eventos rojos y naranjas de la semana (divisas de config.json), ordenados por hora."""
    for intento in range(3):  # Forex Factory limita las peticiones seguidas (HTTP 429)
        try:
            with urllib.request.urlopen(urllib.request.Request(FF_URL, headers=UA), timeout=30) as r:
                datos = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or intento == 2:
                raise
            time.sleep(90)
    divisas = set(cfg.get("divisas") or [])
    eventos = [e for e in datos if e.get("impact") in IMPACTO
               and (not divisas or e["country"] in divisas)]
    for e in eventos:
        e["id"] = f"{e['date']}|{e['country']}|{e['title']}"
    return sorted(eventos, key=lambda e: e["date"])


def hora_es(e):
    return datetime.fromisoformat(e["date"]).astimezone(ES)


def expectativa(e):
    """Qué se espera, a partir de la previsión y el dato anterior de Forex Factory."""
    prev, ant = e.get("forecast") or "", e.get("previous") or ""
    if not prev:
        return f"Sin previsión publicada (anterior: {ant})." if ant else ""
    f, a = numero(prev), numero(ant)
    if f is None or a is None:
        return f"Previsión: {prev} · Anterior: {ant}"
    if "rate" in e["title"].lower() and "unemployment" not in e["title"].lower() and "%" in prev:
        if f > a:
            return f"Se espera una SUBIDA de tipos: de {ant} a {prev}."
        if f < a:
            return f"Se espera una BAJADA de tipos: de {ant} a {prev}."
        return f"Se espera que mantenga los tipos en {prev}."
    tendencia = "que suba" if f > a else "que baje" if f < a else "que se mantenga"
    return f"Previsión: {prev} · Anterior: {ant} → se espera {tendencia}."


def trocear(bloques):
    """Agrupa bloques de texto en mensajes que no superen el límite de ntfy."""
    mensajes, actual = [], ""
    for b in bloques:
        if actual and len((actual + "\n\n" + b).encode("utf-8")) > LIMITE_NTFY:
            mensajes.append(actual)
            actual = b
        else:
            actual = f"{actual}\n\n{b}" if actual else b
    return mensajes + ([actual] if actual else [])


def resumen_semanal(eventos):
    por_dia = defaultdict(list)
    for e in eventos:
        por_dia[hora_es(e).date()].append(e)
    bloques = []
    for d in sorted(por_dia):
        lineas = [f"📆 {DIAS_SEMANA[d.weekday()].upper()} {d:%d/%m}"]
        for e in por_dia[d]:
            nombre, _ = info_evento(e["title"])
            linea = (f"{IMPACTO[e['impact']]} {hora_es(e):%H:%M} "
                     f"{DIVISA.get(e['country'], '')}{e['country']} {nombre}")
            if e.get("forecast"):
                linea += f" (prev. {e['forecast']} · ant. {e.get('previous') or '—'})"
            lineas.append(linea)
        bloques.append("\n".join(lineas))
    rojos = sum(e["impact"] == "High" for e in eventos)
    cab = (f"{rojos} 🔴 alto impacto · {len(eventos) - rojos} 🟠 impacto medio. Horas de España. "
           f"Te aviso {MINUTOS_ANTES} min antes de cada uno y te mando el dato cuando salga.")
    return trocear([cab] + bloques)


def recordatorio(grupo):
    hora = hora_es(grupo[0])
    principal = max(grupo, key=lambda e: e["impact"] == "High")
    nombre, _ = info_evento(principal["title"])
    extra = f" (+{len(grupo) - 1})" if len(grupo) > 1 else ""
    titulo = (f"⏰ {MINUTOS_ANTES} min: {IMPACTO[principal['impact']]} {principal['country']} "
              f"{nombre}{extra} · {hora:%H:%M}")
    bloques = []
    for e in grupo:
        _, que_es = info_evento(e["title"])
        bloques.append("\n".join(p for p in (cabecera_evento(e), expectativa(e), que_es) if p))
    prio = 4 if any(e["impact"] == "High" for e in grupo) else 3
    return titulo, "\n\n".join(bloques), prio


# ---------------------------------------------------------------- Nasdaq (dato real)

def normalizar(nombre):
    n = html.unescape(nombre).lower()
    n = n.replace("m/m", "mom").replace("y/y", "yoy").replace("q/q", "qoq")
    n = re.sub(r"[()]", " ", n)
    n = re.sub(r"\b(german|french|italian|spanish|us|uk|prelim|final|flash|revised|advance|estimate)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def filas_nasdaq(fecha_ny):
    """Filas del calendario de Nasdaq de ese día. Nasdaq agrupa por un día desplazado, así que se
    piden el día y el siguiente y se filtra por la hora de NY."""
    filas = []
    for d in (fecha_ny, fecha_ny + timedelta(days=1)):
        try:
            filas += (get_json(NASDAQ_URL.format(f"{d:%Y-%m-%d}")).get("data") or {}).get("rows") or []
        except Exception as e:  # noqa: BLE001
            print(f"nasdaq {d}: {e}")
    return filas


def valor(txt):
    t = html.unescape(txt or "").replace("\xa0", " ").strip()
    return t


def buscar_en_nasdaq(e, filas):
    """Fila de Nasdaq que corresponde al evento de Forex Factory: misma divisa, nombre parecido y,
    si la hay, misma previsión. Primero a la misma hora de NY; si no, en cualquier hora del día
    (Forex Factory pone hora provisional a algunos datos europeos) exigiendo la misma previsión."""
    hora = datetime.fromisoformat(e["date"]).astimezone(NY).strftime("%H:%M")
    paises = PAISES_NASDAQ.get(e["country"], set())
    nombre_ff = normalizar(e["title"])
    objetivo = ALIAS_NASDAQ.get(nombre_ff, nombre_ff)
    f_ff = numero(e.get("forecast"))

    def misma_prevision(f):
        c = numero(f.get("consensus"))
        return f_ff is not None and c is not None and abs(f_ff - c) <= max(abs(f_ff) * 0.005, 1e-9)

    candidatas = [f for f in filas if f.get("country") in paises]
    for misma_hora in (True, False):
        grupo = [f for f in candidatas if (f.get("gmt") == hora) == misma_hora]
        mejor, puntos_mejor = None, 0.0
        for f in grupo:
            puntos = difflib.SequenceMatcher(None, objetivo, normalizar(f.get("eventName", ""))).ratio()
            if misma_prevision(f):
                puntos += 0.5
            elif not misma_hora:
                continue
            # Nasdaq repite el nombre para la versión mensual y la anual: sin la previsión no se sabe cuál es.
            repetido = sum(normalizar(g.get("eventName", "")) == normalizar(f.get("eventName", ""))
                           for g in grupo) > 1
            if repetido and not misma_prevision(f):
                continue
            if puntos > puntos_mejor:
                mejor, puntos_mejor = f, puntos
        if puntos_mejor >= (0.6 if misma_hora else 0.9):
            return mejor
    return None


# Cómo suele reaccionar el S&P 500 a un dato de EE. UU. por ENCIMA de lo esperado:
# +1 positivo, -1 negativo, 0 sin lectura clara. Primera coincidencia (lo específico antes).
SP500 = [
    ("unemployment", -1), ("jobless", -1), ("claims", -1),
    ("crude oil", 0), ("natural gas", 0), ("bill auction", 0), ("bond auction", 0), ("trade balance", 0),
    ("cpi", -1), ("pce", -1), ("ppi", -1), ("price index", -1), ("hourly earnings", -1),
    ("employment cost", -1), ("inflation expectations", -1), ("prices", -1),
    ("federal funds rate", -1), ("interest rate", -1),
    ("non-farm", 1), ("nonfarm", 1), ("employment change", 1), ("jolts", 1),
    ("gdp", 1), ("retail sales", 1), ("pmi", 1), ("confidence", 1), ("sentiment", 1),
    ("durable goods", 1), ("industrial production", 1), ("home sales", 1), ("housing starts", 1),
    ("building permits", 1), ("empire state", 1), ("philly fed", 1), ("personal spending", 1),
]


def sentido(e, actual, esperado):
    """Por encima/debajo de lo esperado y cómo suele tomarlo el S&P 500 (solo datos de EE. UU.)."""
    if actual is None or esperado is None:
        return ""
    if actual == esperado:
        return "🎯 En línea con lo esperado."
    arriba = actual > esperado
    txt = "📈 Por encima de lo esperado" if arriba else "📉 Por debajo de lo esperado"
    if e["country"] != "USD":
        return txt + "."
    t = e["title"].lower()
    efecto = next((s for clave, s in SP500 if clave in t), 0)
    if not efecto:
        return txt + "."
    bueno = arriba if efecto > 0 else not arriba
    return f"{txt} → suele ser {'🟢 positivo' if bueno else '🔴 negativo'} para el S&P 500."


def texto_resultado(e, fila):
    actual = valor(fila.get("actual"))
    esperado_txt = e.get("forecast") or valor(fila.get("consensus"))
    anterior = e.get("previous") or valor(fila.get("previous"))
    revisado = valor(fila.get("previous"))
    lineas = [cabecera_evento(e)]
    partes = [f"Salió: {actual}"]
    if esperado_txt:
        partes.append(f"Esperado: {esperado_txt}")
    if anterior:
        ant = f"Anterior: {anterior}"
        # En el PIB Nasdaq da el trimestre anterior, no la estimación previa: no es una revisión.
        if "gdp" not in e["title"].lower() and revisado and numero(revisado) is not None \
                and numero(anterior) is not None \
                and abs(numero(revisado) - numero(anterior)) > abs(numero(anterior)) * 0.001:
            ant += f" (revisado {revisado})"
        partes.append(ant)
    lineas.append(" · ".join(partes))
    s = sentido(e, numero(actual), numero(esperado_txt))
    if s:
        lineas.append(s)
    return "\n".join(lineas)


def tiene_dato(e):
    return bool(e.get("forecast") or e.get("previous"))


# ---------------------------------------------------------------- modos

def modo_noche(cfg, estado, ahora, forzar, prueba):
    if not forzar and (ahora.hour != HORA_NOCHE or estado.get("ultima_noche") == str(ahora.date())):
        print(f"No toca ({ahora:%d/%m %H:%M} España).")
        return
    eventos = descargar_semana(cfg)
    if not prueba:
        guardar(SEMANA, {"descargado": ahora.isoformat(timespec="minutes"), "eventos": eventos})

    if ahora.weekday() == 6 or forzar:
        futuros = [e for e in eventos if hora_es(e) > ahora]
        mensajes = resumen_semanal(futuros) if futuros else ["Esta semana no hay eventos rojos ni naranjas."]
        for i, m in enumerate(mensajes, 1):
            titulo = "🗓️ Forex Factory: la semana" + (f" ({i}/{len(mensajes)})" if len(mensajes) > 1 else "")
            print(titulo + "\n" + m + "\n") if prueba else publicar(titulo, m, 3)

    programados = estado.setdefault("programados", [])
    ya = set(programados)
    grupos = defaultdict(list)
    for e in eventos:
        aviso = hora_es(e) - timedelta(minutes=MINUTOS_ANTES)
        if e["id"] not in ya and ahora + timedelta(minutes=1) < aviso <= ahora + timedelta(hours=VENTANA_H):
            grupos[e["date"]].append(e)
    for _, grupo in sorted(grupos.items()):
        titulo, mensaje, prio = recordatorio(grupo)
        aviso = hora_es(grupo[0]) - timedelta(minutes=MINUTOS_ANTES)
        if prueba:
            print(f"[programado para {aviso:%d/%m %H:%M}] {titulo}\n{mensaje}\n")
            continue
        publicar(titulo, mensaje, prio, en=aviso)
        programados += [e["id"] for e in grupo]
        print(f"Programado {aviso:%d/%m %H:%M}: {titulo}")
    estado["programados"] = programados[-400:]
    if not forzar:
        estado["ultima_noche"] = str(ahora.date())


def modo_resultados(cfg, estado, ahora, forzar, prueba):
    eventos = cargar(SEMANA, {}).get("eventos", [])
    reportados = estado.setdefault("reportados", [])
    ya = set(reportados)
    atras = timedelta(hours=48 if forzar else RES_TARDE_H)
    grupos = defaultdict(list)
    for e in eventos:
        h = hora_es(e)
        if e["id"] not in ya and tiene_dato(e) and ahora - atras <= h <= ahora + timedelta(minutes=RES_ADELANTE):
            grupos[e["date"]].append(e)
    if not grupos:
        print("Ningún dato pendiente en esta ventana.")
        return
    for fecha, grupo in sorted(grupos.items()):
        hora = hora_es(grupo[0])
        una_vez = forzar or hora < ahora - timedelta(minutes=RES_ATRAS)  # retrasado: solo una consulta
        espera = (hora - datetime.now(ES)).total_seconds() + 20
        if espera > 0:
            print(f"Esperando {espera:.0f} s a las {hora:%H:%M} ({len(grupo)} eventos)")
            time.sleep(espera)
        pendientes = list(grupo)
        limite = hora + timedelta(minutes=RES_ESPERA)
        bloques = []
        while pendientes:
            filas = filas_nasdaq(datetime.fromisoformat(fecha).astimezone(NY).date())
            for e in list(pendientes):
                fila = buscar_en_nasdaq(e, filas)
                if fila and valor(fila.get("actual")):
                    bloques.append((e, texto_resultado(e, fila)))
                    pendientes.remove(e)
            if not pendientes or datetime.now(ES) > limite or una_vez:
                break
            time.sleep(30)
        for e in pendientes:
            print(f"Sin dato en Nasdaq (se reintenta en la próxima pasada): {e['country']} {e['title']}")
        if not bloques:
            continue
        principal = max((e for e, _ in bloques), key=lambda e: e["impact"] == "High")
        nombre, _ = info_evento(principal["title"])
        extra = f" (+{len(bloques) - 1})" if len(bloques) > 1 else ""
        titulo = f"📊 Resultado {hora:%H:%M}: {IMPACTO[principal['impact']]} {principal['country']} {nombre}{extra}"
        mensaje = "\n\n".join(t for _, t in bloques)
        prio = 4 if any(e["impact"] == "High" for e, _ in bloques) else 3
        if prueba:
            print(titulo + "\n" + mensaje + "\n")
            continue
        publicar(titulo, mensaje, prio)
        print(f"Enviado: {titulo}")
        reportados += [e["id"] for e, _ in bloques]
        estado["reportados"] = reportados[-400:]
        guardar(ESTADO, estado)  # por si el siguiente grupo tarda y otra ejecución toma el relevo


def main():
    args = sys.argv[1:]
    modo = args[0] if args else ""
    forzar, prueba = "--forzar" in args, "--prueba" in args
    cfg = cargar(CONFIG, {})
    estado = cargar(ESTADO, {})
    ahora = datetime.now(ES)
    if modo == "noche":
        modo_noche(cfg, estado, ahora, forzar, prueba)
    elif modo == "resultados":
        modo_resultados(cfg, estado, ahora, forzar, prueba)
    else:
        sys.exit("Uso: calendario.py noche|resultados [--forzar] [--prueba]")
    if not prueba:
        guardar(ESTADO, estado)


if __name__ == "__main__":
    main()
