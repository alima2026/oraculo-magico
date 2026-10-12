# oraculo_astrologico.py
# Oráculo Astrológico — versión con Claude (Anthropic) + modo premium
import os
import re
import io
import hmac
import json
import secrets
import threading
import html
import urllib.parse
import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import swisseph as swe
from fpdf import FPDF
import anthropic
import requests

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
USOS_POR_CODIGO = int(cfg("USOS_POR_CODIGO", "4") or 4)   # consultas premium por cada código vendido
ADMIN_CLAVE = cfg("ADMIN_CLAVE")                           # clave del panel privado (?admin=1)
UPSTASH_URL = cfg("UPSTASH_REDIS_REST_URL").rstrip("/")     # base de datos para contar los usos
UPSTASH_TOKEN = cfg("UPSTASH_REDIS_REST_TOKEN")
# Mercado Pago automático (Checkout Pro). Si no hay token, se usa PAYMENT_LINK como antes.
MP_ACCESS_TOKEN = cfg("MP_ACCESS_TOKEN")
PRECIO_MONTO = float(cfg("PRECIO_MONTO", "200") or 200)
PRECIO_MONEDA = cfg("PRECIO_MONEDA", "UYU")

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
class Almacen:
    """Guarda cuántas consultas le quedan a cada código.
    - Con Upstash (nube): los usos se mantienen siempre, aunque la app se reinicie.
    - Sin Upstash: se guardan en codigos.json (sirve en tu PC; en la nube se pierde al reiniciar)."""

    PREFIJO = "oraculo:codigo:"

    def __init__(self, url: str = "", token: str = ""):
        self.url, self.token = url, token
        self.cliente = None
        # Si en el token pegaron el comando "redis-cli ... redis://default:CLAVE@host:6379",
        # nos conectamos directo por protocolo Redis (con TLS).
        m = re.search(r"rediss?://\S+", token or "")
        if m:
            import redis
            dsn = m.group(0).replace("redis://", "rediss://", 1)
            self.cliente = redis.from_url(dsn, decode_responses=True, socket_timeout=10)
        self.nube = bool(self.cliente or (url and token))
        self.archivo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codigos.json")
        self.lock = threading.Lock()

    # --- Upstash (Redis por HTTP)
    def _redis(self, *cmd):
        if self.cliente is not None:
            return self.cliente.execute_command(*cmd)
        r = requests.post(self.url, json=list(cmd),
                          headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("result")

    # --- archivo local
    def _leer(self) -> dict:
        try:
            with open(self.archivo, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _guardar(self, datos: dict):
        with open(self.archivo, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=1)

    # --- operaciones
    def restantes(self, codigo: str):
        if self.nube:
            v = self._redis("GET", self.PREFIJO + codigo)
            return None if v is None else int(v)
        with self.lock:
            v = self._leer().get(codigo)
        return None if v is None else int(v)

    def crear(self, codigo: str, usos: int, solo_si_no_existe: bool = False):
        if self.nube:
            if solo_si_no_existe:
                self._redis("SET", self.PREFIJO + codigo, usos, "NX")
            else:
                self._redis("SET", self.PREFIJO + codigo, usos)
            return
        with self.lock:
            d = self._leer()
            if not (solo_si_no_existe and codigo in d):
                d[codigo] = usos
                self._guardar(d)

    def consumir(self, codigo: str):
        """Descuenta 1 uso. Devuelve los que quedan, o None si no tenía."""
        if self.nube:
            n = int(self._redis("DECR", self.PREFIJO + codigo))
            if n < 0:
                self._redis("INCR", self.PREFIJO + codigo)
                return None
            return n
        with self.lock:
            d = self._leer()
            if d.get(codigo, 0) <= 0:
                return None
            d[codigo] -= 1
            self._guardar(d)
            return d[codigo]

    def devolver(self, codigo: str):
        if self.nube:
            self._redis("INCR", self.PREFIJO + codigo)
            return
        with self.lock:
            d = self._leer()
            d[codigo] = d.get(codigo, 0) + 1
            self._guardar(d)

    def email(self, codigo: str):
        if self.nube:
            return self._redis("GET", "oraculo:email:" + codigo)
        with self.lock:
            return self._leer().get("__emails__", {}).get(codigo)

    def guardar_email(self, codigo: str, email: str):
        if self.nube:
            self._redis("SET", "oraculo:email:" + codigo, email)
            return
        with self.lock:
            d = self._leer()
            d.setdefault("__emails__", {})[codigo] = email
            self._guardar(d)

    def codigo_de_pago(self, pago_id: str):
        if self.nube:
            return self._redis("GET", "oraculo:pago:" + pago_id)
        with self.lock:
            return self._leer().get("__pagos__", {}).get(pago_id)

    def reservar_pago(self, pago_id: str, codigo: str) -> str:
        """Asocia el pago a un código una sola vez. Devuelve el código que quedó asociado."""
        if self.nube:
            ok = self._redis("SET", "oraculo:pago:" + pago_id, codigo, "NX")
            return codigo if ok else self._redis("GET", "oraculo:pago:" + pago_id)
        with self.lock:
            d = self._leer()
            pagos = d.setdefault("__pagos__", {})
            if pago_id not in pagos:
                pagos[pago_id] = codigo
                self._guardar(d)
            return pagos[pago_id]

    def listar(self) -> dict:
        if self.nube:
            claves = self._redis("KEYS", self.PREFIJO + "*") or []
            if not claves:
                return {}
            valores = self._redis("MGET", *claves)
            return {k[len(self.PREFIJO):]: int(v or 0) for k, v in zip(claves, valores)}
        with self.lock:
            return {k: v for k, v in self._leer().items() if not k.startswith("__")}


@st.cache_resource
def _almacen(url: str, token: str) -> Almacen:
    a = Almacen(url, token)
    # Los códigos de ACCESS_CODES se cargan una sola vez con USOS_POR_CODIGO usos
    for c in ACCESS_CODES:
        try:
            a.crear(c, USOS_POR_CODIGO, solo_si_no_existe=True)
        except Exception:
            pass
    return a


def almacen() -> Almacen:
    # Se vuelve a crear solo si cambian las claves en Secrets
    return _almacen(UPSTASH_URL, UPSTASH_TOKEN)


def nuevo_codigo() -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I para no confundir
    return "ORA-" + "".join(secrets.choice(alfabeto) for _ in range(6))


def es_premium() -> bool:
    return bool(st.session_state.get("codigo")) and st.session_state.get("creditos", 0) > 0


def usar_credito() -> bool:
    """Descuenta 1 consulta premium del código activo."""
    codigo = st.session_state.get("codigo")
    if not codigo:
        return False
    try:
        quedan = almacen().consumir(codigo)
    except Exception:
        st.error("No pudimos verificar tu código en este momento. Probá de nuevo en un minuto.")
        return False
    if quedan is None:
        st.session_state["creditos"] = 0
        return False
    st.session_state["creditos"] = quedan
    return True


def devolver_credito():
    codigo = st.session_state.get("codigo")
    if codigo:
        try:
            almacen().devolver(codigo)
            st.session_state["creditos"] = st.session_state.get("creditos", 0) + 1
        except Exception:
            pass


# ============================================================
# MERCADO PAGO (cobro automático → código automático)
# ============================================================
MP_API = "https://api.mercadopago.com"


def mp_crear_cobro(email: str = "") -> str:
    """Crea el cobro y devuelve el link de pago de Mercado Pago."""
    r = requests.post(
        f"{MP_API}/checkout/preferences",
        headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"},
        json={
            "items": [{
                "title": f"{APP_NAME} - Pack de {USOS_POR_CODIGO} consultas premium",
                "quantity": 1,
                "unit_price": PRECIO_MONTO,
                "currency_id": PRECIO_MONEDA,
            }],
            **({"payer": {"email": email}} if email else {}),
            "external_reference": email or "web",
            "back_urls": {"success": APP_PUBLIC_LINK, "pending": APP_PUBLIC_LINK, "failure": APP_PUBLIC_LINK},
            "auto_return": "approved",
            "statement_descriptor": "ORACULO",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["init_point"]


def mp_canjear_pago(pago_id: str):
    """Verifica el pago con Mercado Pago y devuelve (codigo, creditos, mensaje_error)."""
    pago_id = re.sub(r"\D", "", str(pago_id))
    if not pago_id:
        return None, 0, "Número de operación inválido."
    alm = almacen()
    ya = alm.codigo_de_pago(pago_id)
    if ya:
        return ya, alm.restantes(ya) or 0, None
    r = requests.get(f"{MP_API}/v1/payments/{pago_id}",
                     headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}, timeout=15)
    if r.status_code == 404:
        return None, 0, "No encontramos ese pago. Revisá el número de operación."
    if r.status_code in (401, 403):
        return None, 0, "No pudimos consultar el pago (configuración de Mercado Pago). Avisanos por WhatsApp."
    r.raise_for_status()
    pago = r.json()
    if pago.get("status") != "approved":
        return None, 0, "El pago todavía no está aprobado. Si pagaste en efectivo, puede demorar unas horas."
    if float(pago.get("transaction_amount") or 0) + 0.01 < PRECIO_MONTO or pago.get("currency_id") != PRECIO_MONEDA:
        return None, 0, "El monto de ese pago no corresponde al pack premium."
    codigo = alm.reservar_pago(pago_id, nuevo_codigo())
    if alm.restantes(codigo) is None:          # recién creado: le cargamos las consultas
        alm.crear(codigo, USOS_POR_CODIGO)
        ref = (pago.get("external_reference") or "").strip().lower()
        email = ref if "@" in ref else ((pago.get("payer") or {}).get("email") or "").strip().lower()
        if "@" in email:
            alm.guardar_email(codigo, email)
    return codigo, alm.restantes(codigo) or 0, None


def activar_codigo(codigo: str, creditos: int):
    st.session_state["codigo"] = codigo
    st.session_state["creditos"] = creditos
    st.session_state["codigo_nuevo"] = codigo
    st.session_state["regenerar"] = True


def bloque_premium(motivo: str):
    """Caja de venta + campo para activar código."""
    with st.container(border=True):
        st.markdown(
            f"""<div class="premium">
<div class="corona">👑</div>
<h3>Oráculo Premium</h3>
<p style="color:var(--suave)">{html.escape(motivo)}</p>
<div class="precio">{html.escape(PRECIO_PREMIUM)}</div>
<div class="unico">pack de {USOS_POR_CODIGO} consultas premium · acceso inmediato</div>
<ul>
<li>Lectura <b>completa</b> de tu carta: esencia, amor, dinero, trabajo y desafíos</li>
<li>Preguntas <b>profundas</b> a tu oráculo personal</li>
<li>Usalas como quieras: tu carta, la de tu pareja, la de un amigo…</li>
<li><b>PDF</b> de cada carta para guardar o regalar</li>
</ul></div>""",
            unsafe_allow_html=True,
        )
        clave = motivo[:10]
        link_pago = PAYMENT_LINK
        if MP_ACCESS_TOKEN:
            # Un cobro por visitante: el botón lleva directo a Mercado Pago y, al volver, el código aparece solo
            if not st.session_state.get("link_pago_mp"):
                try:
                    st.session_state["link_pago_mp"] = mp_crear_cobro()
                except Exception:
                    st.session_state["link_pago_mp"] = ""
            link_pago = st.session_state["link_pago_mp"] or PAYMENT_LINK
        if link_pago:
            st.link_button("✨ Quiero mi lectura completa", link_pago, type="primary", width="stretch")
            if MP_ACCESS_TOKEN:
                st.caption("Pagás en Mercado Pago y al volver tu código aparece automáticamente.")

        if WHATSAPP_VENTAS:
            st.link_button("💬 Prefiero comprar por WhatsApp",
                           whatsapp_link("Hola! Quiero el Oráculo Premium 🔮", WHATSAPP_VENTAS),
                           width="stretch")
        if not MP_ACCESS_TOKEN:
            st.caption(f"Después del pago recibís un código personal con {USOS_POR_CODIGO} consultas.")
        with st.form(f"form_codigo_{motivo[:10]}"):
            codigo = st.text_input("¿Ya tenés tu código?", placeholder="ORA-XXXXXX")
            email = st.text_input("Tu email (el que usaste al comprar)", placeholder="nombre@correo.com")
            if st.form_submit_button("Activar"):
                codigo = codigo.strip().upper()
                email = email.strip().lower()
                email_ok = True
                try:
                    quedan = almacen().restantes(codigo) if codigo else None
                    dueño = almacen().email(codigo) if quedan is not None else None
                    email_ok = (not dueño) or hmac.compare_digest(dueño, email)
                except Exception:
                    quedan = None
                    st.error("No pudimos verificar el código ahora. Probá de nuevo en un minuto.")
                if quedan is None:
                    st.error("Código inválido.")
                elif not email_ok:
                    st.error("Este código pertenece a otra persona. Escribí el mismo email con el que compraste.")
                elif quedan <= 0:
                    st.error("Este código ya usó todas sus consultas. Podés comprar un pack nuevo.")
                else:
                    activar_codigo(codigo, quedan)
                    st.session_state["codigo_nuevo"] = ""
                    st.rerun()
        if MP_ACCESS_TOKEN:
            with st.expander("¿Pagaste y no te apareció el código?"):
                with st.form(f"form_recuperar_{clave}"):
                    op = st.text_input("Número de operación de Mercado Pago",
                                       help="Lo encontrás en el comprobante o en el email de Mercado Pago.")
                    if st.form_submit_button("Recuperar mi código"):
                        try:
                            cod, cred, err = mp_canjear_pago(op)
                        except Exception:
                            cod, cred, err = None, 0, "No pudimos verificar el pago ahora. Probá en un minuto."
                        if err:
                            st.error(err)
                        else:
                            activar_codigo(cod, cred)
                            st.rerun()


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

# ============================================================
# PANEL PRIVADO (entrar con  https://tu-app.streamlit.app/?admin=1 )
# ============================================================
if st.query_params.get("admin") == "1":
    seccion("ADMIN", "Panel de códigos", "Solo para vos. Generá un código cada vez que alguien te paga.")
    if not ADMIN_CLAVE:
        st.error("Falta configurar ADMIN_CLAVE en los Secrets.")
        st.stop()
    if not st.session_state.get("admin_ok"):
        with st.form("login_admin"):
            clave = st.text_input("Clave de administrador", type="password")
            if st.form_submit_button("Entrar", type="primary"):
                if hmac.compare_digest(clave, ADMIN_CLAVE):
                    st.session_state["admin_ok"] = True
                    st.rerun()
                else:
                    st.error("Clave incorrecta.")
        st.stop()

    with st.expander("🩺 Diagnóstico de conexiones", expanded=True):
        # Claude
        st.write("**Claude (IA):**", "✅ clave cargada" if AI_OK else "❌ falta ANTHROPIC_API_KEY")
        # Upstash
        if not UPSTASH_TOKEN:
            st.write("**Base de datos (Upstash):** ❌ falta UPSTASH_REDIS_REST_TOKEN")
        else:
            try:
                almacen()._redis("PING")
                st.write("**Base de datos (Upstash):** ✅ conectada")
            except Exception as e:
                st.write(f"**Base de datos (Upstash):** ❌ no conecta ({type(e).__name__}). Revisá el token.")
        # Mercado Pago
        if not MP_ACCESS_TOKEN:
            st.write("**Mercado Pago:** ⚠️ sin MP_ACCESS_TOKEN (se usa el link fijo)")
        elif re.fullmatch(r"APP_USR-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", MP_ACCESS_TOKEN):
            st.write("**Mercado Pago:** ❌ pegaste la *Public Key*. Hace falta el *Access Token* "
                     "(más largo, con números: APP_USR-1234567890123456-...).")
        else:
            try:
                r = requests.get(f"{MP_API}/users/me",
                                 headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}, timeout=10)
                if r.status_code == 200:
                    u = r.json()
                    st.write(f"**Mercado Pago:** ✅ conectado a la cuenta {u.get('nickname', '')} "
                             f"({u.get('email', '')}) · país {u.get('site_id', '')}")
                    try:
                        mp_crear_cobro()
                        st.write("**Crear cobro:** ✅ funciona")
                    except requests.HTTPError as e:
                        st.write(f"**Crear cobro:** ❌ {e.response.status_code}: {e.response.text[:200]}")
                else:
                    st.write(f"**Mercado Pago:** ❌ error {r.status_code}: {r.text[:160]}")
            except Exception as e:
                st.write(f"**Mercado Pago:** ❌ sin conexión ({type(e).__name__})")

    alm = almacen()
    if not alm.nube:
        st.warning("Base de datos no configurada (Upstash). Los usos se guardan en un archivo local: "
                   "en Streamlit Cloud se pierden si la app se reinicia.")

    with st.container(border=True):
        usos = st.number_input("Consultas del código", min_value=1, max_value=50, value=USOS_POR_CODIGO)
        cliente = st.text_input("Nombre del cliente (opcional)")
        email_cli = st.text_input("Email del cliente (si lo ponés, el código solo funciona con ese email)")
        if st.button("✨ Generar código nuevo", type="primary", width="stretch"):
            cod = nuevo_codigo()
            alm.crear(cod, int(usos))
            if email_cli.strip():
                alm.guardar_email(cod, email_cli.strip().lower())
            st.session_state["ultimo_codigo"] = (cod, int(usos), cliente.strip(), email_cli.strip().lower())

    if st.session_state.get("ultimo_codigo"):
        cod, usos_cod, cliente, email_cli = st.session_state["ultimo_codigo"]
        st.success(f"Código creado: **{cod}** ({usos_cod} consultas)")
        mensaje = (f"¡Gracias por tu compra{', ' + cliente if cliente else ''}! 🔮\n\n"
                   f"Tu código del Oráculo Premium es: *{cod}*\n"
                   f"Tenés {usos_cod} consultas (lectura completa de carta o preguntas al oráculo).\n\n"
                   f"Entrá a {APP_PUBLIC_LINK} y escribilo donde dice \"¿Ya tenés tu código?\""
                   + (f" junto con tu email ({email_cli})." if email_cli else "."))
        st.code(mensaje, language=None)
        st.link_button("💬 Enviar por WhatsApp", whatsapp_link(mensaje))

    if MP_ACCESS_TOKEN:
        with st.container(border=True):
            st.markdown("#### 💬 Link de pago para mandar por WhatsApp")
            st.caption("Cuando el cliente paga con este link, vuelve a la app y su código aparece solo.")
            mail_wa = st.text_input("Email del cliente", key="mail_wa")
            if st.button("Crear link de pago", width="stretch"):
                if "@" not in mail_wa:
                    st.error("Escribí el email del cliente.")
                else:
                    try:
                        link = mp_crear_cobro(mail_wa.strip().lower())
                        msg = (f"¡Hola! 🔮 Este es tu link para el Oráculo Premium ({PRECIO_PREMIUM}, "
                               f"{USOS_POR_CODIGO} consultas):\n{link}\n\n"
                               "Cuando termines de pagar, volvés a la app y tu código aparece automáticamente.")
                        st.code(msg, language=None)
                        st.link_button("💬 Abrir WhatsApp con este mensaje", whatsapp_link(msg))
                    except Exception:
                        st.error("No se pudo crear el link. Revisá MP_ACCESS_TOKEN.")

    with st.expander("Ver todos los códigos y sus consultas restantes"):
        try:
            todos = alm.listar()
        except Exception as e:
            todos = {}
            st.error(f"No se pudo leer la base de datos: {e}")
        if todos:
            st.dataframe(
                [{"Código": k, "Consultas restantes": v} for k, v in sorted(todos.items())],
                width="stretch", hide_index=True,
            )
        else:
            st.write("Todavía no hay códigos.")
    st.stop()

defaults = {
    "carta": None, "oraculo_respuesta": "", "share_text": "", "respuesta_rapida": "",
    "tema_rapido": "plata", "codigo": "", "creditos": 0, "preguntas_usadas": 0, "regenerar": False,
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

# Vuelta desde Mercado Pago: ?payment_id=...&status=approved
_pago_id = st.query_params.get("payment_id") or st.query_params.get("collection_id")
if MP_ACCESS_TOKEN and _pago_id and _pago_id != "null":
    try:
        _cod, _cred, _err = mp_canjear_pago(_pago_id)
    except Exception:
        _cod, _cred, _err = None, 0, "No pudimos verificar tu pago todavía. Usá “¿Pagaste y no te apareció el código?”."
    st.query_params.clear()
    if _err:
        st.warning(_err)
    else:
        activar_codigo(_cod, _cred)

if st.session_state.get("codigo_nuevo"):
    with st.container(border=True):
        st.markdown(
            f"""<div class="premium"><div class="corona">🎉</div><h3>¡Pago confirmado!</h3>
<p>Tu código personal es</p><div class="precio">{html.escape(st.session_state['codigo_nuevo'])}</div>
<div class="unico">Guardalo (sacale captura): lo vas a necesitar si volvés otro día.</div></div>""",
            unsafe_allow_html=True,
        )

aviso_premium = st.empty()   # se completa al final, con el saldo ya actualizado

if not AI_OK:
    st.warning("El oráculo está en mantenimiento. Podés calcular tu carta igual; la interpretación vuelve en breve.")

# -------------------------------
# PREGUNTA RÁPIDA (gancho gratis)
# -------------------------------
seccion("GRATIS", "Mensaje del día", "Elegí lo que te está pesando hoy y recibí un consejo en segundos.")

with st.container(border=True):
    tema = st.selectbox("¿Qué tema te está pesando hoy?", TEMAS_RAPIDOS,
                        index=TEMAS_RAPIDOS.index(st.session_state["tema_rapido"]))
    if st.button("✨ Recibir mi mensaje", width="stretch"):
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
    st.markdown("**Fecha de nacimiento**")
    hoy = datetime.date.today()
    MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
             "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    cd, cm, ca = st.columns([1, 1.6, 1.2])
    dia = cd.selectbox("Día", list(range(1, 32)), index=None, placeholder="Día")
    mes = cm.selectbox("Mes", list(range(1, 13)), format_func=lambda m: MESES[m - 1],
                       index=None, placeholder="Mes")
    anio = ca.selectbox("Año", list(range(hoy.year, 1899, -1)), index=None, placeholder="Año")
    st.markdown("**Hora de nacimiento** (si no la sabés, dejá 12:00)")
    ch, cmin = st.columns(2)
    hh = ch.selectbox("Hora", list(range(24)), index=12, format_func=lambda h: f"{h:02d} h")
    mm = cmin.selectbox("Minutos", list(range(60)), index=0, format_func=lambda m: f"{m:02d} min")
    hora = datetime.time(hh, mm)
    idx = st.selectbox("¿Dónde naciste?", options=list(range(len(labels))),
                       format_func=lambda i: labels[i], index=idx_mvd)
    submitted = st.form_submit_button("🔮 Revelar mi carta", type="primary", width="stretch")


def generar_interpretacion(carta: dict):
    completa = es_premium() and usar_credito()   # la lectura completa usa 1 consulta
    try:
        with st.spinner("Interpretando tu carta..."):
            carta["interpretacion"] = interpretar_carta(carta["calc"], carta["nombre"], completa=completa)
            carta["interpretacion_completa"] = completa
    except ErrorIA as e:
        if completa:
            devolver_credito()
        carta["interpretacion"] = ""
        st.error(f"La carta se calculó bien, pero la interpretación falló: {e}")


if submitted:
    fecha = None
    if dia and mes and anio:
        try:
            fecha = datetime.date(anio, mes, dia)
        except ValueError:
            fecha = None
    if not nombre.strip():
        st.error("Poné tu nombre.")
    elif not (dia and mes and anio):
        st.error("Elegí día, mes y año de nacimiento.")
    elif fecha is None:
        st.error("Esa fecha no existe (revisá el día y el mes).")
    elif fecha > datetime.date.today():
        st.error("La fecha de nacimiento no puede ser futura.")
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

    if carta.get("interpretacion_completa"):
        st.download_button(
            "📜 Descargar mi carta en PDF",
            data=exportar_pdf(carta),
            file_name=f"Carta_Natal_{carta['nombre'].replace(' ', '_')}.pdf",
            mime="application/pdf",
        )
    elif es_premium():
        if st.button(f"👑 Ver la lectura completa de esta carta (usa 1 de tus {st.session_state['creditos']} consultas)",
                     type="primary", width="stretch"):
            generar_interpretacion(carta)
            st.rerun()
    else:
        bloque_premium("Esto es solo un adelanto. Accedé a tu lectura completa.")

    separador()

    # ORÁCULO
    seccion("PASO 2", "Preguntale a tu oráculo", "Amor, plata, trabajo, una decisión… lo que necesites.")
    restantes = max(0, PREGUNTAS_GRATIS - st.session_state["preguntas_usadas"])
    if es_premium():
        st.caption("Cada pregunta usa 1 de tus consultas premium.")
    else:
        st.caption(f"Te queda{'' if restantes == 1 else 'n'} {restantes} pregunta{'' if restantes == 1 else 's'} gratis")

    puede_preguntar = es_premium() or restantes > 0
    if puede_preguntar:
        with st.form("form_oraculo"):
            pregunta = st.text_area("Escribí tu pregunta", placeholder="Ej: ¿Es buen momento para cambiar de trabajo?",
                                    height=110, max_chars=500)
            ask = st.form_submit_button("🔮 Consultar al oráculo", type="primary", width="stretch")

        if ask:
            if not pregunta.strip():
                st.warning("Escribí tu pregunta.")
            else:
                premium = es_premium()
                if premium and not usar_credito():
                    st.warning("Tu código ya no tiene consultas disponibles.")
                    st.stop()
                try:
                    with st.spinner("El oráculo está leyendo tu carta..."):
                        resp = responder_oraculo(pregunta.strip(), c, carta["nombre"])
                    st.session_state["oraculo_respuesta"] = resp
                    st.session_state["share_text"] = build_share_text(pregunta.strip(), resp)
                    if not premium:
                        st.session_state["preguntas_usadas"] += 1
                    st.rerun()
                except ErrorIA as e:
                    if premium:
                        devolver_credito()
                    st.error(str(e))

    if st.session_state["oraculo_respuesta"]:
        with st.container(border=True):
            st.markdown("#### ✦ El oráculo responde")
            st.markdown(st.session_state["oraculo_respuesta"])
        st.link_button("💬 Compartir por WhatsApp", whatsapp_link(st.session_state["share_text"]))

    if not puede_preguntar:
        bloque_premium("Usaste tu pregunta gratis. Seguí consultando con un pack premium.")

if es_premium():
    n = st.session_state["creditos"]
    aviso_premium.success(f"👑 Premium activo · te queda{'' if n == 1 else 'n'} {n} consulta{'' if n == 1 else 's'}")
elif st.session_state.get("codigo"):
    aviso_premium.info("👑 Usaste todas las consultas de tu código. ¡Gracias! Podés comprar un pack nuevo cuando quieras.")

st.markdown(
    f"""<div class="pie">✦ ✧ ✦<br>{APP_NAME}<br>
Orientación simbólica y de autoconocimiento. No sustituye asesoramiento profesional.<br>
App creada por Carlos Changala &amp; IA</div>""",
    unsafe_allow_html=True,
)
