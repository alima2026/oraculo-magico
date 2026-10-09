# oraculo_astrologico.py
# Oráculo Astrológico — versión con Claude (Anthropic) + modo premium
import os
import re
import io
import hmac
import html
import urllib.parse
import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import swisseph as swe
from fpdf import FPDF
import anthropic

# ============================================================
# STREAMLIT: SIEMPRE PRIMERO
# ============================================================
st.set_page_config(page_title="Oráculo Astrológico", page_icon="🔮", layout="centered")


# ============================================================
# CONFIG (st.secrets o variables de entorno)
# ============================================================
def cfg(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()


APP_NAME = "Oráculo Astrológico"
APP_PUBLIC_LINK = cfg("APP_PUBLIC_LINK", "https://TU-LINK-DE-LA-APP.com")

# Claude
ANTHROPIC_API_KEY = cfg("ANTHROPIC_API_KEY")
CLAUDE_MODEL = cfg("CLAUDE_MODEL", "claude-haiku-5-5")  # barato y rápido; cambialo sin tocar código
AI_OK = bool(ANTHROPIC_API_KEY)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if AI_OK else None
# Haiku 5.5 "piensa" antes de responder y eso consume del límite: dejamos margen amplio
# (solo se paga lo que realmente usa) y le pedimos poco esfuerzo de razonamiento.
LIMITE_TOKENS = 16000
ESFUERZO = cfg("ORACULO_ESFUERZO", "low")

# Monetización
PAYMENT_LINK = cfg("PAYMENT_LINK", "")            # ej: link de Mercado Pago / Stripe
WHATSAPP_VENTAS = cfg("WHATSAPP_VENTAS", "")      # ej: 59899123456 (sin + ni espacios)
PRECIO_PREMIUM = cfg("PRECIO_PREMIUM", "USD 5")
ACCESS_CODES = [c.strip().upper() for c in cfg("ACCESS_CODES", "").split(",") if c.strip()]
PREGUNTAS_GRATIS = int(cfg("PREGUNTAS_GRATIS", "1") or 1)

# ============================================================
# DATA
# ============================================================
# Cada ciudad lleva su zona horaria: la hora de nacimiento es LOCAL
# y Swiss Ephemeris necesita UTC (antes se calculaba mal el ascendente).
CIUDADES = {
    ("Montevideo", "Uruguay"): {"lat": -34.9011, "lon": -56.1645, "tz": "America/Montevideo"},
    ("Salto", "Uruguay"): {"lat": -31.3833, "lon": -57.9667, "tz": "America/Montevideo"},
    ("Paysandú", "Uruguay"): {"lat": -32.3214, "lon": -58.0756, "tz": "America/Montevideo"},
    ("Maldonado", "Uruguay"): {"lat": -34.9000, "lon": -54.9500, "tz": "America/Montevideo"},
    ("Rivera", "Uruguay"): {"lat": -30.9053, "lon": -55.5508, "tz": "America/Montevideo"},
    ("Buenos Aires", "Argentina"): {"lat": -34.6037, "lon": -58.3816, "tz": "America/Argentina/Buenos_Aires"},
    ("Córdoba", "Argentina"): {"lat": -31.4201, "lon": -64.1888, "tz": "America/Argentina/Cordoba"},
    ("Rosario", "Argentina"): {"lat": -32.9442, "lon": -60.6505, "tz": "America/Argentina/Cordoba"},
    ("Porto Alegre", "Brasil"): {"lat": -30.0346, "lon": -51.2177, "tz": "America/Sao_Paulo"},
    ("São Paulo", "Brasil"): {"lat": -23.5505, "lon": -46.6333, "tz": "America/Sao_Paulo"},
    ("Santiago", "Chile"): {"lat": -33.4489, "lon": -70.6693, "tz": "America/Santiago"},
    ("Asunción", "Paraguay"): {"lat": -25.2637, "lon": -57.5759, "tz": "America/Asuncion"},
    ("Lima", "Perú"): {"lat": -12.0464, "lon": -77.0428, "tz": "America/Lima"},
    ("Bogotá", "Colombia"): {"lat": 4.7110, "lon": -74.0721, "tz": "America/Bogota"},
    ("México DF", "México"): {"lat": 19.4326, "lon": -99.1332, "tz": "America/Mexico_City"},
    ("Madrid", "España"): {"lat": 40.4168, "lon": -3.7038, "tz": "Europe/Madrid"},
    ("Barcelona", "España"): {"lat": 41.3851, "lon": 2.1734, "tz": "Europe/Madrid"},
    ("Miami", "Estados Unidos"): {"lat": 25.7617, "lon": -80.1918, "tz": "America/New_York"},
}

PLANETAS = [
    (swe.SUN, "Sol"), (swe.MOON, "Luna"), (swe.MERCURY, "Mercurio"), (swe.VENUS, "Venus"),
    (swe.MARS, "Marte"), (swe.JUPITER, "Júpiter"), (swe.SATURN, "Saturno"),
    (swe.URANUS, "Urano"), (swe.NEPTUNE, "Neptuno"), (swe.PLUTO, "Plutón"),
]

SIGNOS = [
    "Aries", "Tauro", "Géminis", "Cáncer", "Leo", "Virgo",
    "Libra", "Escorpio", "Sagitario", "Capricornio", "Acuario", "Piscis",
]

TEMAS_RAPIDOS = ["amor", "plata", "laburo", "ansiedad", "decisión"]

# ============================================================
# HELPERS ASTRO
# ============================================================
def signo_zodiacal(longitud: float) -> str:
    return SIGNOS[int(longitud // 30) % 12]


def grado_en_signo(longitud: float) -> str:
    g = longitud % 30
    return f"{int(g)}°{int((g % 1) * 60):02d}'"


def casa_de(longitud: float, cuspides) -> int:
    """Devuelve en qué casa (1-12) cae una longitud."""
    for i in range(12):
        ini = cuspides[i]
        fin = cuspides[(i + 1) % 12]
        if ini <= fin:
            if ini <= longitud < fin:
                return i + 1
        elif longitud >= ini or longitud < fin:
            return i + 1
    return 1


def calcular_carta(fecha: datetime.date, hora: datetime.time, lat: float, lon: float, tz: str):
    # Hora local -> UTC (respeta horarios de verano históricos)
    local = datetime.datetime.combine(fecha, hora).replace(tzinfo=ZoneInfo(tz))
    utc = local.astimezone(datetime.timezone.utc)
    hora_decimal = utc.hour + utc.minute / 60 + utc.second / 3600
    jd = swe.julday(utc.year, utc.month, utc.day, hora_decimal)

    casas, ascmc = swe.houses(jd, lat, lon, b"P")  # Placidus
    casas = [float(c) for c in casas[:12]]
    ascendente = float(ascmc[0])
    medio_cielo = float(ascmc[1])

    planetas = {}
    for code, nombre in PLANETAS:
        result = swe.calc_ut(jd, code)
        lonp = float(result[0][0] if isinstance(result[0], (list, tuple)) else result[0])
        planetas[nombre] = {
            "lon": lonp,
            "signo": signo_zodiacal(lonp),
            "grado": grado_en_signo(lonp),
            "casa": casa_de(lonp, casas),
        }

    return {
        "planetas": planetas,
        "asc": ascendente,
        "asc_signo": signo_zodiacal(ascendente),
        "mc_signo": signo_zodiacal(medio_cielo),
        "casas": casas,
        "utc": utc.strftime("%Y-%m-%d %H:%M UTC"),
    }


def resumen_carta(c: dict) -> str:
    lineas = [f"- {n}: {p['signo']} {p['grado']}, casa {p['casa']}" for n, p in c["planetas"].items()]
    lineas.append(f"- Ascendente: {c['asc_signo']} ({grado_en_signo(c['asc'])})")
    lineas.append(f"- Medio Cielo: {c['mc_signo']}")
    return "\n".join(lineas)


# ============================================================
# CLAUDE
# ============================================================
SISTEMA = (
    "Sos un astrólogo profesional, serio, cálido y responsable. "
    "Escribís en español rioplatense neutro. Hablás de tendencias y posibilidades, "
    "nunca de certezas ni destino fijo. No das consejos médicos, legales ni financieros concretos: "
    "si el tema lo requiere, sugerís consultar a un profesional. "
    "Usás texto simple con títulos cortos y viñetas; sin emojis."
)


class ErrorIA(Exception):
    pass


def preguntar_claude(prompt: str, max_tokens: int = 700, temperature: float = 0.7) -> str:
    if not AI_OK:
        raise ErrorIA("El oráculo no está configurado (falta ANTHROPIC_API_KEY).")
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=LIMITE_TOKENS,
            system=SISTEMA,  # Haiku 5.5 no acepta 'temperature'
            messages=[{"role": "user", "content": prompt}],
            extra_body={"output_config": {"effort": ESFUERZO}},
        )
        texto = "".join(b.text for b in resp.content if b.type == "text").strip()
        # Si se quedó sin espacio, le pedimos que termine (máx 2 veces)
        intentos = 0
        while resp.stop_reason == "max_tokens" and intentos < 2:
            intentos += 1
            resp = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=LIMITE_TOKENS,
                system=SISTEMA,
                extra_body={"output_config": {"effort": ESFUERZO}},
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": "Seguí exactamente donde quedaste, sin repetir nada ni saludar."},
                ],
            )
            extra = "".join(b.text for b in resp.content if b.type == "text")
            sep = " " if extra[:1].isalnum() and texto[-1:].isalnum() else ""
            texto = (texto + sep + extra).strip()
        return texto
    except anthropic.AuthenticationError:
        raise ErrorIA("La clave de la IA no es válida. Revisá ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise ErrorIA("El oráculo está con mucha demanda. Probá de nuevo en un minuto.")
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower() or "billing" in str(e).lower():
            raise ErrorIA("El oráculo está temporalmente sin saldo. Volvé a intentar más tarde.")
        raise ErrorIA("No se pudo procesar la consulta.")
    except anthropic.NotFoundError:
        raise ErrorIA(f"El modelo '{CLAUDE_MODEL}' no existe. Revisá CLAUDE_MODEL.")
    except anthropic.APIConnectionError:
        raise ErrorIA("No hay conexión con el oráculo. Probá de nuevo.")
    except anthropic.APIStatusError:
        raise ErrorIA("El oráculo tuvo un problema momentáneo. Probá de nuevo en un rato.")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def oraculo_rapido(tema: str, dia: str) -> str:
    # Cacheado por tema y día: todos ven la misma respuesta del día y no pagás de más.
    prompt = f"""Hoy es {dia}. Alguien dice que hoy le pesa el tema: "{tema}".
Dale una respuesta breve y concreta:
- 3 viñetas accionables (nada de frases vacías)
- 1 pregunta final para que vuelva a consultar
- máximo 450 caracteres"""
    r = preguntar_claude(prompt, max_tokens=600, temperature=0.8)
    if len(r) < 80:
        raise ErrorIA("El oráculo no terminó la respuesta. Probá de nuevo.")
    return r


def interpretar_carta(c: dict, nombre: str, completa: bool) -> str:
    if completa:
        pedido = """Hacé una interpretación PROFESIONAL y COMPLETA. Secciones:
1) Esencia (Sol, Luna, Ascendente)
2) Amor y vínculos
3) Dinero y trabajo
4) Bienestar y energía
5) Desafíos y cómo trabajarlos
6) Consejo final
Usá ejemplos cotidianos. Entre 700 y 1000 palabras."""
        max_tokens = 4000
    else:
        pedido = """Hacé un ADELANTO breve (máx 150 palabras): esencia según Sol, Luna y Ascendente.
Terminá con una frase que invite a ver la lectura completa (amor, dinero, trabajo y desafíos)."""
        max_tokens = 800

    prompt = f"""Carta natal de {nombre}:
{resumen_carta(c)}

{pedido}"""
    return preguntar_claude(prompt, max_tokens=max_tokens)


def clasificar_pregunta(pregunta: str) -> str:
    p = pregunta.lower()
    palabras_casa = ["casa", "propiedad", "departamento", "apartamento", "terreno", "hipoteca", "vivienda"]
    palabras_dinero = ["dinero", "plata", "ahorro", "ingreso", "finanzas", "invertir", "inversión",
                       "crédito", "prestamo", "préstamo", "negocio", "sueldo"]
    if any(w in p for w in palabras_casa + palabras_dinero):
        return "economica"
    return "general"


def responder_oraculo(pregunta: str, c: dict, nombre: str) -> str:
    if clasificar_pregunta(pregunta) == "economica":
        formato = """La pregunta es ECONÓMICA / PATRIMONIAL. Enfoque práctico, sin coaching motivacional.
FORMATO:
1) Escenario general (favorable / incierto / poco propicio)
2) Qué tendría que alinearse para que se concrete
3) Riesgo principal a vigilar
4) Ventana temporal orientativa (sin fechas exactas)
5) Recomendación concreta y realista"""
        max_tokens, temp = 2000, 0.6
    else:
        formato = """Justificá con datos de la carta (planetas, signos, casas, ascendente).
FORMATO:
1) Diagnóstico breve (2-3 líneas)
2) Insight accionable con ejemplo cotidiano
3) Punto ciego / tensión a observar
4) Tres caminos posibles (viñetas)
5) 2 o 3 preguntas de seguimiento muy específicas
Máximo 1700 caracteres."""
        max_tokens, temp = 2000, 0.75

    prompt = f"""Pregunta de {nombre}: "{pregunta}"

Carta natal:
{resumen_carta(c)}

{formato}"""
    return preguntar_claude(prompt, max_tokens=max_tokens, temperature=temp)


# ============================================================
# PDF
# ============================================================
def _latin1(texto: str) -> str:
    """Las fuentes base del PDF solo aceptan latin-1: limpiamos markdown, emojis y comillas raras."""
    reemplazos = {"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-",
                  "…": "...", "•": "-", "→": "->", " ": " "}
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
    texto = re.sub(r"^#+\s*", "", texto, flags=re.M)
    return texto.encode("latin-1", "ignore").decode("latin-1")


def exportar_pdf(carta: dict) -> bytes:
    c = carta["calc"]
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(80, 30, 120)
    pdf.cell(0, 16, _latin1("Carta Natal"), new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 7, _latin1(
        f"Nombre: {carta['nombre']}\n"
        f"Nacimiento: {carta['fecha']} {carta['hora']} - {carta['ciudad']}, {carta['pais']}\n"
        f"Sol en {c['planetas']['Sol']['signo']} - Luna en {c['planetas']['Luna']['signo']} - Ascendente {c['asc_signo']}"
    ))
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, _latin1("Interpretación"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, _latin1(carta["interpretacion"] or "-"))

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 5, _latin1(f"{APP_NAME} - {APP_PUBLIC_LINK}\n"
                                 "Orientación simbólica. No sustituye asesoramiento profesional."))
    return bytes(pdf.output())


# ============================================================
# COMPARTIR
# ============================================================
def build_share_text(pregunta: str, respuesta: str) -> str:
    corta = respuesta.strip()
    if len(corta) > 700:
        corta = corta[:700].rsplit(" ", 1)[0] + "..."
    return (
        f"🔮 *{APP_NAME}*\n\n"
        f"📌 *Mi pregunta:* {pregunta}\n\n"
        f"{corta}\n\n"
        f"✨ Hacé tu carta natal gratis: {APP_PUBLIC_LINK}"
    )


def whatsapp_link(texto: str, numero: str = "") -> str:
    return f"https://wa.me/{numero}?text={urllib.parse.quote(texto)}"


# ============================================================
# PREMIUM
# ============================================================
def codigo_valido(codigo: str) -> bool:
    codigo = codigo.strip().upper()
    return any(hmac.compare_digest(codigo, c) for c in ACCESS_CODES)


def es_premium() -> bool:
    return st.session_state.get("premium", False)


def bloque_premium(motivo: str):
    """Caja de venta + campo para activar código."""
    with st.container(border=True):
        st.markdown(
            f"""<div class="premium">
<div class="corona">👑</div>
<h3>Oráculo Premium</h3>
<p style="color:var(--suave)">{html.escape(motivo)}</p>
<div class="precio">{html.escape(PRECIO_PREMIUM)}</div>
<div class="unico">pago único · acceso inmediato</div>
<ul>
<li>Lectura <b>completa</b> de tu carta: esencia, amor, dinero, trabajo y desafíos</li>
<li><b>Preguntas ilimitadas</b> a tu oráculo personal</li>
<li><b>PDF</b> de tu carta para guardar o regalar</li>
</ul></div>""",
            unsafe_allow_html=True,
        )
        col1, col2 = st.columns(2)
        if PAYMENT_LINK:
            col1.link_button("✨ Quiero mi lectura completa", PAYMENT_LINK, type="primary",
                             use_container_width=True)
        if WHATSAPP_VENTAS:
            col2.link_button("💬 Comprar por WhatsApp",
                             whatsapp_link("Hola! Quiero el Oráculo Premium 🔮", WHATSAPP_VENTAS),
                             use_container_width=True)
        st.caption("Después del pago recibís un código de acceso.")
        with st.form(f"form_codigo_{motivo[:10]}"):
            codigo = st.text_input("¿Ya tenés tu código?", type="password")
            if st.form_submit_button("Activar"):
                if codigo_valido(codigo):
                    st.session_state["premium"] = True
                    st.session_state["regenerar"] = True
                    st.rerun()
                else:
                    st.error("Código inválido.")


# ============================================================
# DISEÑO (místico oscuro: noche estrellada, violeta y dorado)
# ============================================================
GLIFOS = {k: v + "\ufe0e" for k, v in {
    "Aries": "♈", "Tauro": "♉", "Géminis": "♊", "Cáncer": "♋", "Leo": "♌", "Virgo": "♍",
    "Libra": "♎", "Escorpio": "♏", "Sagitario": "♐", "Capricornio": "♑", "Acuario": "♒", "Piscis": "♓"}.items()}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Inter:wght@400;500;600&display=swap');

:root{
  --noche:#0b0820; --noche2:#151033; --panel:rgba(30,22,68,.72); --borde:rgba(212,175,95,.28);
  --oro:#e3c27a; --oro2:#b8913f; --lila:#b9a6ff; --texto:#ece8ff; --suave:#a9a3c9;
}

/* Fondo: noche con estrellas */
.stApp{
  background:
    radial-gradient(1px 1px at 12% 18%, #fff 50%, transparent 51%),
    radial-gradient(1px 1px at 78% 12%, #fff 50%, transparent 51%),
    radial-gradient(1.5px 1.5px at 40% 66%, #f6e7c1 50%, transparent 51%),
    radial-gradient(1px 1px at 88% 52%, #fff 50%, transparent 51%),
    radial-gradient(1px 1px at 22% 84%, #fff 50%, transparent 51%),
    radial-gradient(1.5px 1.5px at 62% 30%, #fff 50%, transparent 51%),
    radial-gradient(1px 1px at 6% 48%, #d9ccff 50%, transparent 51%),
    radial-gradient(1px 1px at 95% 88%, #fff 50%, transparent 51%),
    radial-gradient(900px 600px at 50% -10%, rgba(124,92,255,.28), transparent 70%),
    radial-gradient(700px 500px at 100% 100%, rgba(227,194,122,.10), transparent 70%),
    linear-gradient(180deg, var(--noche) 0%, var(--noche2) 100%) !important;
  background-attachment: fixed !important;
  color: var(--texto);
}
html, body, .stApp, p, li, label, span, div{ font-family:'Inter',sans-serif; }
.stApp p, .stApp li, .stApp label, .stMarkdown, [data-testid="stCaptionContainer"]{ color:var(--texto) !important; }
[data-testid="stCaptionContainer"] p{ color:var(--suave) !important; }
h1,h2,h3,h4{ font-family:'Cinzel',serif !important; color:var(--oro) !important; letter-spacing:.5px; }

/* Ocultar marca y menús de Streamlit */
header[data-testid="stHeader"]{ background:transparent !important; }
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer,
[data-testid="stStatusWidget"]{ display:none !important; visibility:hidden !important; }
.block-container{ padding-top:2rem !important; max-width:760px; }

/* Portada */
.hero{ text-align:center; padding:2.2rem 1rem 1.2rem; }
.hero .bola{ font-size:64px; filter:drop-shadow(0 0 24px rgba(185,166,255,.7)); }
.hero h1{ font-size:2.6rem; margin:.3rem 0 .2rem; line-height:1.15;
  background:linear-gradient(90deg,#f7e3ad,#e3c27a,#b9a6ff); -webkit-background-clip:text; background-clip:text;
  -webkit-text-fill-color:transparent; }
.hero .tag{ font-family:'Cinzel',serif; color:var(--lila); font-size:1.05rem; letter-spacing:2px; text-transform:uppercase; }
.hero .sub{ color:var(--suave); max-width:520px; margin:.8rem auto 0; font-size:1rem; }
.chips{ display:flex; gap:.5rem; justify-content:center; flex-wrap:wrap; margin-top:1.2rem; }
.chip{ border:1px solid var(--borde); background:rgba(255,255,255,.04); color:var(--texto);
  padding:.35rem .8rem; border-radius:999px; font-size:.82rem; }

/* Títulos de sección */
.seccion{ text-align:center; margin:2.2rem 0 .8rem; }
.seccion .num{ display:inline-block; color:var(--oro); border:1px solid var(--borde); border-radius:999px;
  font-size:.75rem; padding:.15rem .7rem; letter-spacing:2px; }
.seccion h2{ margin:.5rem 0 .2rem; font-size:1.6rem; }
.seccion p{ color:var(--suave) !important; margin:0; }
.separador{ text-align:center; color:var(--oro2); letter-spacing:12px; margin:1.8rem 0; opacity:.8; }

/* Paneles: formularios y contenedores con borde */
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"]{
  background:var(--panel) !important; border:1px solid var(--borde) !important; border-radius:18px !important;
  box-shadow:0 10px 40px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.04); backdrop-filter:blur(6px);
}

/* Botones */
.stButton button, .stFormSubmitButton button, .stLinkButton a, .stDownloadButton button{
  border-radius:999px !important; border:1px solid var(--oro2) !important; background:transparent !important;
  color:var(--oro) !important; font-weight:600 !important; padding:.55rem 1.4rem !important; transition:all .2s;
}
.stButton button:hover, .stFormSubmitButton button:hover, .stLinkButton a:hover, .stDownloadButton button:hover{
  background:rgba(227,194,122,.12) !important; box-shadow:0 0 18px rgba(227,194,122,.25);
}
.stFormSubmitButton button[kind="primaryFormSubmit"], .stButton button[kind="primary"], .stLinkButton a[kind="primary"]{
  background:linear-gradient(90deg,#e3c27a,#c79a45) !important; color:#1a1233 !important; border:none !important;
  box-shadow:0 6px 24px rgba(227,194,122,.35);
}
.stButton button p, .stFormSubmitButton button p, .stLinkButton a p, .stDownloadButton button p{ color:inherit !important; }

/* Avisos */
[data-testid="stAlert"]{ background:rgba(185,166,255,.08) !important; border:1px solid rgba(185,166,255,.3) !important;
  border-radius:14px !important; }
[data-testid="stAlert"] *{ color:var(--texto) !important; }

/* Lectura */
.lectura{ background:rgba(255,255,255,.03); border-left:3px solid var(--oro); border-radius:12px; padding:1rem 1.2rem; }

/* Tarjetas Sol / Luna / Ascendente */
.trio{ display:grid; grid-template-columns:repeat(3,1fr); gap:.8rem; margin:1rem 0 1.4rem; }
.astro{ text-align:center; background:var(--panel); border:1px solid var(--borde); border-radius:16px; padding:1rem .5rem; }
.astro .g{ font-size:2.1rem; color:var(--oro); line-height:1; }
.astro .t{ font-size:.72rem; letter-spacing:2px; text-transform:uppercase; color:var(--suave); margin-top:.5rem; }
.astro .s{ font-family:'Cinzel',serif; font-size:1.15rem; color:var(--texto); margin-top:.15rem; }

/* Tarjeta Premium */
.premium{ text-align:center; padding:.4rem .2rem 0; }
.premium .corona{ font-size:2rem; }
.premium h3{ margin:.2rem 0; }
.premium .precio{ font-family:'Cinzel',serif; font-size:2.4rem; color:var(--oro); margin:.3rem 0 0; }
.premium .unico{ color:var(--suave); font-size:.85rem; margin-bottom:.9rem; }
.premium ul{ list-style:none; padding:0; margin:0 auto 1rem; max-width:420px; text-align:left; }
.premium li{ padding:.35rem 0; border-bottom:1px dashed rgba(212,175,95,.18); }
.premium li:before{ content:"✦  "; color:var(--oro); }

.pie{ text-align:center; color:var(--suave); font-size:.8rem; margin:2.5rem 0 1rem; line-height:1.6; }

@media (max-width:640px){
  .hero h1{ font-size:2rem; } .hero .bola{ font-size:52px; }
  .astro .s{ font-size:.95rem; } .astro .g{ font-size:1.7rem; }
}
</style>
"""


def seccion(numero: str, titulo: str, sub: str = ""):
    st.markdown(
        f'<div class="seccion"><span class="num">{numero}</span><h2>{titulo}</h2>'
        + (f"<p>{sub}</p>" if sub else "") + "</div>",
        unsafe_allow_html=True,
    )


def separador():
    st.markdown('<div class="separador">✦ ✧ ✦</div>', unsafe_allow_html=True)


# ============================================================
# UI
# ============================================================
st.markdown(CSS, unsafe_allow_html=True)

defaults = {
    "carta": None, "oraculo_respuesta": "", "share_text": "", "respuesta_rapida": "",
    "tema_rapido": "plata", "premium": False, "preguntas_usadas": 0, "regenerar": False,
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

st.markdown(
    """<div class="hero">
<div class="bola">🔮</div>
<div class="tag">Las estrellas tienen algo para decirte</div>
<h1>Oráculo Astrológico</h1>
<p class="sub">Tu carta natal calculada con precisión astronómica e interpretada para tu vida real:
amor, dinero, trabajo y decisiones.</p>
<div class="chips"><span class="chip">✦ Cálculo astronómico exacto</span>
<span class="chip">✦ Lectura personalizada</span><span class="chip">✦ Resultado en segundos</span></div>
</div>""",
    unsafe_allow_html=True,
)

if es_premium():
    st.success("👑 Modo Premium activo — disfrutá tu lectura completa")

if not AI_OK:
    st.warning("El oráculo está en mantenimiento. Podés calcular tu carta igual; la interpretación vuelve en breve.")

# -------------------------------
# PREGUNTA RÁPIDA (gancho gratis)
# -------------------------------
seccion("GRATIS", "Mensaje del día", "Elegí lo que te está pesando hoy y recibí un consejo en segundos.")

with st.container(border=True):
    tema = st.selectbox("¿Qué tema te está pesando hoy?", TEMAS_RAPIDOS,
                        index=TEMAS_RAPIDOS.index(st.session_state["tema_rapido"]))
    if st.button("✨ Recibir mi mensaje", use_container_width=True):
        st.session_state["tema_rapido"] = tema
        try:
            with st.spinner("Consultando a los astros..."):
                st.session_state["respuesta_rapida"] = oraculo_rapido(tema, datetime.date.today().isoformat())
        except ErrorIA as e:
            st.session_state["respuesta_rapida"] = ""
            st.error(str(e))

    if st.session_state["respuesta_rapida"]:
        st.markdown(st.session_state["respuesta_rapida"])

separador()

# -------------------------------
# FORM CARTA NATAL
# -------------------------------
seccion("PASO 1", "Tu carta natal", "Con tu fecha, hora y lugar de nacimiento.")

city_country_options = sorted(CIUDADES.keys(), key=lambda x: (x[1], x[0]))
labels = [f"{c}, {p}" for c, p in city_country_options]
idx_mvd = city_country_options.index(("Montevideo", "Uruguay"))

with st.form("datos_usuario"):
    nombre = st.text_input("¿Cuál es tu nombre?")
    col1, col2 = st.columns(2)
    fecha = col1.date_input("Fecha de nacimiento", value=datetime.date(1990, 1, 1),
                            min_value=datetime.date(1900, 1, 1), max_value=datetime.date.today(),
                            format="DD/MM/YYYY")
    hora = col2.time_input("Hora de nacimiento", value=datetime.time(12, 0), step=60)
    idx = st.selectbox("¿Dónde naciste?", options=list(range(len(labels))),
                       format_func=lambda i: labels[i], index=idx_mvd)
    submitted = st.form_submit_button("🔮 Revelar mi carta", type="primary", use_container_width=True)


def generar_interpretacion(carta: dict):
    try:
        with st.spinner("Interpretando tu carta..."):
            carta["interpretacion"] = interpretar_carta(carta["calc"], carta["nombre"], completa=es_premium())
            carta["interpretacion_completa"] = es_premium()
    except ErrorIA as e:
        carta["interpretacion"] = ""
        st.error(f"La carta se calculó bien, pero la interpretación falló: {e}")


if submitted:
    if not nombre.strip():
        st.error("Poné tu nombre.")
    else:
        ciudad, pais = city_country_options[idx]
        info = CIUDADES[(ciudad, pais)]
        try:
            calc = calcular_carta(fecha, hora, info["lat"], info["lon"], info["tz"])
        except Exception as e:
            st.error(f"Error al calcular la carta natal: {e}")
        else:
            carta = {
                "nombre": nombre.strip(),
                "fecha": fecha.strftime("%d/%m/%Y"),
                "hora": hora.strftime("%H:%M"),
                "ciudad": ciudad,
                "pais": pais,
                "calc": calc,
                "interpretacion": "",
                "interpretacion_completa": False,
            }
            if AI_OK:
                generar_interpretacion(carta)
            st.session_state["carta"] = carta
            st.session_state["oraculo_respuesta"] = ""
            st.session_state["share_text"] = ""

# Si activó premium con una carta ya hecha, rehacemos la interpretación completa
carta = st.session_state["carta"]
if carta and st.session_state["regenerar"]:
    st.session_state["regenerar"] = False
    if AI_OK and not carta.get("interpretacion_completa"):
        generar_interpretacion(carta)

# -------------------------------
# MOSTRAR CARTA + PDF + ORÁCULO
# -------------------------------
if carta:
    c = carta["calc"]
    st.markdown(
        f"""<div class="seccion"><h2>Carta de {html.escape(carta['nombre'])}</h2>
<p>{carta['fecha']} · {carta['hora']} · {html.escape(carta['ciudad'])}, {html.escape(carta['pais'])}</p></div>
<div class="trio">
<div class="astro"><div class="g">{GLIFOS[c['planetas']['Sol']['signo']]}</div><div class="t">Sol</div><div class="s">{c['planetas']['Sol']['signo']}</div></div>
<div class="astro"><div class="g">{GLIFOS[c['planetas']['Luna']['signo']]}</div><div class="t">Luna</div><div class="s">{c['planetas']['Luna']['signo']}</div></div>
<div class="astro"><div class="g">{GLIFOS[c['asc_signo']]}</div><div class="t">Ascendente</div><div class="s">{c['asc_signo']}</div></div>
</div>""",
        unsafe_allow_html=True,
    )

    if carta["interpretacion"]:
        with st.container(border=True):
            st.markdown(carta["interpretacion"])

    if not es_premium():
        bloque_premium("Esto es solo un adelanto. Accedé a tu lectura completa.")
    else:
        st.download_button(
            "📜 Descargar mi carta en PDF",
            data=exportar_pdf(carta),
            file_name=f"Carta_Natal_{carta['nombre'].replace(' ', '_')}.pdf",
            mime="application/pdf",
        )

    separador()

    # ORÁCULO
    seccion("PASO 2", "Preguntale a tu oráculo", "Amor, plata, trabajo, una decisión… lo que necesites.")
    restantes = max(0, PREGUNTAS_GRATIS - st.session_state["preguntas_usadas"])
    if not es_premium():
        st.caption(f"Te queda{'' if restantes == 1 else 'n'} {restantes} pregunta{'' if restantes == 1 else 's'} gratis")

    puede_preguntar = es_premium() or restantes > 0
    if puede_preguntar:
        with st.form("form_oraculo"):
            pregunta = st.text_area("Escribí tu pregunta", placeholder="Ej: ¿Es buen momento para cambiar de trabajo?",
                                    height=110, max_chars=500)
            ask = st.form_submit_button("🔮 Consultar al oráculo", type="primary", use_container_width=True)

        if ask:
            if not pregunta.strip():
                st.warning("Escribí tu pregunta.")
            else:
                try:
                    with st.spinner("El oráculo está leyendo tu carta..."):
                        resp = responder_oraculo(pregunta.strip(), c, carta["nombre"])
                    st.session_state["oraculo_respuesta"] = resp
                    st.session_state["share_text"] = build_share_text(pregunta.strip(), resp)
                    if not es_premium():
                        st.session_state["preguntas_usadas"] += 1
                    st.rerun()
                except ErrorIA as e:
                    st.error(str(e))

    if st.session_state["oraculo_respuesta"]:
        with st.container(border=True):
            st.markdown("#### ✦ El oráculo responde")
            st.markdown(st.session_state["oraculo_respuesta"])
        st.link_button("💬 Compartir por WhatsApp", whatsapp_link(st.session_state["share_text"]))

    if not puede_preguntar:
        bloque_premium("Usaste tu pregunta gratis. Seguí consultando sin límite.")

st.markdown(
    f"""<div class="pie">✦ ✧ ✦<br>{APP_NAME}<br>
Orientación simbólica y de autoconocimiento. No sustituye asesoramiento profesional.<br>
App creada por Carlos Changala &amp; IA</div>""",
    unsafe_allow_html=True,
)
