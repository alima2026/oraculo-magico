# oraculo_astrologico.py
import os
import re
import urllib.parse
import datetime
import streamlit as st
import swisseph as swe
from fpdf import FPDF
import openai

# ============================================================
# STREAMLIT: SIEMPRE PRIMERO
# ============================================================
st.set_page_config(page_title="Oráculo Astrológico", layout="centered")

# ============================================================
# CONFIG
# ============================================================
APP_NAME = "Oráculo Astrológico"
APP_PUBLIC_LINK = os.getenv("APP_PUBLIC_LINK", "https://TU-LINK-DE-LA-APP.com")

# OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_OK = bool(openai.api_key)

# ============================================================
# DATA
# ============================================================
CIUDADES = {
    ("Montevideo", "Uruguay"): {"lat": -34.9011, "lon": -56.1645},
    ("Buenos Aires", "Argentina"): {"lat": -34.6037, "lon": -58.3816},
    ("Madrid", "España"): {"lat": 40.4168, "lon": -3.7038},
    ("México DF", "México"): {"lat": 19.4326, "lon": -99.1332},
    ("Barcelona", "España"): {"lat": 41.3851, "lon": 2.1734},
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
# HELPERS
# ============================================================
def signo_zodiacal(longitud: float) -> str:
    signo = int(longitud // 30) % 12
    return SIGNOS[signo]


def validar_fecha(fecha: str) -> bool:
    try:
        datetime.datetime.strptime(fecha, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validar_hora(hora: str) -> bool:
    return bool(re.match(r"^\d{2}:\d{2}$", hora))


def calcular_carta(fecha: str, hora: str, lat: float, lon: float):
    anio, mes, dia = map(int, fecha.split("-"))
    hh, mm = map(int, hora.split(":"))
    hora_decimal = hh + (mm / 60)

    jd = swe.julday(anio, mes, dia, hora_decimal)

    posiciones = {}
    posiciones_signo = {}

    for code, nombre in PLANETAS:
        result = swe.calc_ut(jd, code)
        lonp = result[0][0] if isinstance(result[0], (list, tuple)) else result[0]
        posiciones[nombre] = float(lonp)
        posiciones_signo[nombre] = signo_zodiacal(lonp)

    casas, ascmc = swe.houses(jd, lat, lon)
    ascendente = float(ascmc[0])
    asc_signo = signo_zodiacal(ascendente)

    return posiciones, posiciones_signo, ascendente, asc_signo, casas


def interpretar_carta(posiciones_signo, asc, asc_signo, nombre):
    prompt = f"""
Haz una interpretación profesional y humana de una carta natal para {nombre}.
Incluye: dinero, salud, amor y trabajo.

Planetas (en signos): {posiciones_signo}.
Ascendente: {asc_signo} ({asc:.2f}°).

Requisitos:
- Clara y realista (sin prometer certezas absolutas).
- Con ejemplos cotidianos.
- Positiva, pero advertí desafíos si es necesario.
"""
    resp = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=700,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def oraculo_rapido(tema: str):
    prompt = f"""
Dame una respuesta breve, concreta y profesional para alguien que dice que hoy le pesa: "{tema}".
Reglas:
- 3 bullets accionables (no frases vacías)
- 1 pregunta final para que vuelva a consultar
- tono rioplatense neutro
- máximo 450 caracteres
"""
    resp = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def clasificar_pregunta(pregunta: str) -> str:
    """
    Clasifica rápidamente el tipo de pregunta para ajustar el estilo de respuesta.
    """
    p = pregunta.lower()

    palabras_casa = ["casa", "propiedad", "departamento", "terreno", "hipoteca", "vivienda"]
    palabras_dinero = ["dinero", "plata", "ahorro", "ingreso", "finanzas", "invertir", "inversión", "crédito", "prestamo", "préstamo"]
    palabras_tiempo = ["cuándo", "cuando", "este año", "el año", "mes", "semana", "2025", "2026", "2027"]

    if any(w in p for w in palabras_casa):
        return "patrimonial"
    if any(w in p for w in palabras_dinero) and any(w in p for w in palabras_tiempo):
        return "economica_temporal"
    if any(w in p for w in palabras_dinero):
        return "economica"
    return "general"


def responder_oraculo(pregunta, posiciones_signo, asc, asc_signo, nombre):
    """
    Responde según el tipo de pregunta. Si es dinero/casa/inversión, fuerza un formato coherente:
    escenarios, condiciones, riesgos y ventana temporal (sin certezas).
    """
    tipo = clasificar_pregunta(pregunta)

    if tipo in ["patrimonial", "economica_temporal", "economica"]:
        prompt = f"""
Actuás como un ORÁCULO ASTROLÓGICO PROFESIONAL orientado a DECISIONES REALES.

La pregunta es de tipo ECONÓMICO / PATRIMONIAL.
Respondé con seriedad y enfoque práctico.

PROHIBIDO:
- hablar de creatividad, arte, meditación o emociones genéricas
- responder como coach motivacional
- prometer hechos cerrados

OBLIGATORIO:
- hablar de escenarios, condiciones y probabilidades
- explicar qué tendría que alinearse para que ocurra
- ser prudente y concreto

Pregunta del consultante:
"{pregunta}"

Datos de la carta de {nombre}:
- Planetas en signos: {posiciones_signo}
- Ascendente: {asc_signo} ({asc:.2f}°)

FORMATO OBLIGATORIO:
1) Lectura del escenario general (favorable / incierto / poco propicio)
2) Condiciones necesarias para que se concrete
3) Riesgo principal a vigilar
4) Ventana temporal orientativa (sin fechas exactas)
5) Recomendación concreta y realista

Español rioplatense neutro.
No uses certezas absolutas.
"""
        max_tokens = 520
        temperature = 0.6
    else:
        prompt = f"""
Actuás como un ORÁCULO ASTROLÓGICO PROFESIONAL, serio, humano y responsable.

REGLAS:
- Justificá con datos de la carta (planetas/signos/ascendente).
- Hablá en términos de tendencias/posibilidades, no de destino fijo.
- Dejá una "tensión elegante" (algo a observar/decidir).
- Finalizá con 2 a 3 preguntas de seguimiento MUY específicas.

Pregunta del consultante:
"{pregunta}"

Datos de la carta de {nombre}:
- Planetas en signos: {posiciones_signo}
- Ascendente: {asc_signo} ({asc:.2f}°)

FORMATO:
1) Diagnóstico breve (2–3 líneas)
2) Insight accionable (con ejemplo cotidiano)
3) Punto ciego / tensión elegante
4) Siguientes 3 caminos (viñetas)

Español rioplatense neutro.
Máx 1700 caracteres.
"""
        max_tokens = 420
        temperature = 0.75

    resp = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content.strip()


def exportar_pdf(nombre, fecha, hora, ciudad, pais, posiciones_signo, asc, asc_signo, interpretacion):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 20)
    pdf.set_text_color(80, 30, 120)
    pdf.cell(0, 20, "Carta Natal", ln=1, align="C")

    pdf.set_font("Arial", "", 14)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(
        0, 10,
        f"Nombre: {nombre}\nFecha: {fecha} Hora: {hora}\nCiudad: {ciudad}, {pais}\nAscendente: {asc_signo} ({asc:.2f}°)"
    )

    pdf.ln(2)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Posiciones planetarias:", ln=1)

    pdf.set_font("Arial", "", 12)
    for planeta, signo in posiciones_signo.items():
        pdf.cell(0, 8, f"{planeta} en {signo}", ln=1)

    pdf.ln(2)
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Interpretación:", ln=1)

    pdf.set_font("Arial", "", 12)
    pdf.multi_cell(0, 8, interpretacion)

    filename = f"Carta_Natal_{nombre.replace(' ', '_')}.pdf"
    pdf.output(filename)
    return filename


def build_share_text(nombre: str, pregunta: str, respuesta: str) -> str:
    respuesta = respuesta.strip()
    return (
        f"🔮 *{APP_NAME}*\n\n"
        f"📌 *Tema:* {pregunta}\n\n"
        f"{respuesta}\n\n"
        f"🧪 Probalo gratis: {APP_PUBLIC_LINK}\n"
        f"🤖 Si querés un análisis más profundo mediante IA, escribí *QUIERO ASESORAMIENTO* "
        f"y coordinamos el paso a paso (con posibilidad de cita 1 a 1 si el caso lo requiere).\n\n"
        f"#Oraculo #Decisiones #Astrología"
    )


def whatsapp_link(texto: str) -> str:
    msg = urllib.parse.quote(texto)
    return f"https://wa.me/?text={msg}"


# ============================================================
# UI
# ============================================================
st.title("🔮 Oráculo Astrológico")
st.write("Completá tus datos y obtené tu carta natal profesional. Podés exportarla en PDF y hacerle una pregunta a tu oráculo.")

# Session state
if "carta" not in st.session_state:
    st.session_state["carta"] = None
if "oraculo_respuesta" not in st.session_state:
    st.session_state["oraculo_respuesta"] = ""
if "share_text" not in st.session_state:
    st.session_state["share_text"] = ""
if "respuesta_rapida" not in st.session_state:
    st.session_state["respuesta_rapida"] = ""
if "tema_rapido" not in st.session_state:
    st.session_state["tema_rapido"] = "plata"

if not OPENAI_OK:
    st.warning("⚠️ Falta configurar OPENAI_API_KEY. La app no podrá consultar al Oráculo.")

# -------------------------------
# PREGUNTA RÁPIDA (TRÁFICO)
# -------------------------------
st.markdown("### ⚡ Pregunta rápida (para tráfico)")
st.caption("Probalo en 10 segundos. Después podés hacer la carta completa.")

tema = st.selectbox("¿Qué tema te está pesando hoy?", TEMAS_RAPIDOS, index=TEMAS_RAPIDOS.index(st.session_state["tema_rapido"]))

if st.button("Responder rápido"):
    st.session_state["tema_rapido"] = tema
    if not OPENAI_OK:
        st.session_state["respuesta_rapida"] = "⚠️ No puedo responder ahora: falta configurar OPENAI_API_KEY."
    else:
        try:
            st.session_state["respuesta_rapida"] = oraculo_rapido(tema)
        except Exception as e:
            st.session_state["respuesta_rapida"] = f"Error: {e}"

if st.session_state["respuesta_rapida"]:
    st.success(st.session_state["respuesta_rapida"])

st.markdown("---")

# -------------------------------
# FORM CARTA NATAL (Ciudad+País correctos)
# -------------------------------
st.markdown("## 📌 Tu carta natal (profesional)")

city_country_options = sorted(CIUDADES.keys(), key=lambda x: (x[1], x[0]))
labels = [f"{c}, {p}" for c, p in city_country_options]

with st.form("datos_usuario"):
    nombre = st.text_input("¿Cuál es tu nombre?")
    fecha = st.text_input("¿Cuál es tu fecha de nacimiento?", value="1990-01-01")
    hora = st.text_input("¿A qué hora naciste? (HH:MM)", value="12:00")
    idx = st.selectbox("¿Dónde naciste? (Ciudad + País)", options=list(range(len(labels))), format_func=lambda i: labels[i])
    submitted = st.form_submit_button("Consultar carta")

if submitted:
    if not nombre.strip():
        st.error("Poné tu nombre.")
    elif not validar_fecha(fecha):
        st.error("Fecha inválida. Usá formato YYYY-MM-DD.")
    elif not validar_hora(hora):
        st.error("Hora inválida. Usá formato HH:MM.")
    else:
        ciudad, pais = city_country_options[idx]
        info = CIUDADES[(ciudad, pais)]
        try:
            pos, pos_signos, asc, asc_signo, casas = calcular_carta(fecha, hora, info["lat"], info["lon"])
            interpretacion = ""
            if OPENAI_OK:
                interpretacion = interpretar_carta(pos_signos, asc, asc_signo, nombre)
            else:
                interpretacion = "⚠️ Interpretación no disponible (falta OPENAI_API_KEY)."

            st.session_state["carta"] = {
                "nombre": nombre.strip(),
                "fecha": fecha,
                "hora": hora,
                "ciudad": ciudad,
                "pais": pais,
                "pos": pos,
                "pos_signos": pos_signos,
                "asc": asc,
                "asc_signo": asc_signo,
                "casas": casas,
                "interpretacion": interpretacion,
            }
            st.session_state["oraculo_respuesta"] = ""
            st.session_state["share_text"] = ""
        except Exception as e:
            st.error(f"Error al calcular la carta natal: {e}")

# -------------------------------
# MOSTRAR CARTA + PDF + ORÁCULO
# -------------------------------
if st.session_state["carta"]:
    carta = st.session_state["carta"]

    st.subheader(f"Carta natal para {carta['nombre']}")
    st.write(f"**Fecha:** {carta['fecha']} | **Hora:** {carta['hora']} | **Ciudad:** {carta['ciudad']}, {carta['pais']}")
    st.write(f"**Ascendente:** {carta['asc_signo']} ({carta['asc']:.2f}°)")

    st.write("### Posiciones planetarias:")
    for planeta, signo in carta["pos_signos"].items():
        st.write(f"- {planeta} en **{signo}**")

    st.write("### Interpretación completa:")
    st.write(carta["interpretacion"])

    st.markdown("#### Exportar tu carta:")
    if st.button("📄 Generar PDF"):
        filename = exportar_pdf(
            carta["nombre"], carta["fecha"], carta["hora"], carta["ciudad"], carta["pais"],
            carta["pos_signos"], carta["asc"], carta["asc_signo"], carta["interpretacion"]
        )
        with open(filename, "rb") as f:
            st.download_button("⬇️ Descargar PDF", data=f, file_name=filename, mime="application/pdf")

    st.divider()

    # ✅ FORM del oráculo (persistente, no vuelve a la carta)
    st.markdown("## Preguntale algo a tu oráculo")
    with st.form("form_oraculo"):
        pregunta = st.text_area("Escribí tu pregunta", placeholder="Ej: ¿Voy a comprar una casa en 2026?", height=110)
        ask = st.form_submit_button("Consultar Oráculo")

    if ask:
        if not pregunta.strip():
            st.warning("Por favor, escribí tu pregunta.")
        elif not OPENAI_OK:
            st.error("Falta OPENAI_API_KEY para consultar al Oráculo.")
        else:
            try:
                resp = responder_oraculo(
                    pregunta=pregunta.strip(),
                    posiciones_signo=carta["pos_signos"],
                    asc=carta["asc"],
                    asc_signo=carta["asc_signo"],
                    nombre=carta["nombre"],
                )
                st.session_state["oraculo_respuesta"] = resp
                st.session_state["share_text"] = build_share_text(carta["nombre"], pregunta.strip(), resp)
            except Exception as e:
                st.error(f"Error consultando al oráculo: {e}")

    if st.session_state["oraculo_respuesta"]:
        st.success("Respuesta del oráculo:")
        st.write(st.session_state["oraculo_respuesta"])

        st.markdown("---")
        st.header("📲 Texto listo para compartir (WhatsApp)")
        st.text_area("Copiá y pegá esto en WhatsApp", value=st.session_state["share_text"], height=220)

        st.markdown(
            f"""
            <a href="{whatsapp_link(st.session_state["share_text"])}" target="_blank">
                <button style="
                    background-color:#25D366;
                    color:white;
                    border:none;
                    padding:12px 18px;
                    font-size:16px;
                    border-radius:10px;
                    cursor:pointer;">
                    💬 Compartir por WhatsApp
                </button>
            </a>
            """,
            unsafe_allow_html=True
        )

        st.divider()
        st.header("¿Querés un análisis más profundo y aplicado a tu caso?")
        st.write(
            "Podés acceder al **Asesoramiento Personalizado mediante IA**, "
            "con posibilidad de **coordinar una cita 1 a 1** si el caso lo requiere."
        )
        st.write('Escribí **"QUIERO ASESORAMIENTO"** y te explico cómo funciona.')

st.caption("Orientación simbólica/psicológica. No sustituye asesoramiento profesional.")
st.caption("App creada por Carlos Changala & IA")
