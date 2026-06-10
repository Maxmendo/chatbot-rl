"""
CHATBOT REFUGIO LATINOAMERICANO — bot.py v9
Géneros: Historia de vida, Denuncia, Reportaje
IA: Gemini (borrador final, modelo configurable) + Groq/Llama (repreguntas) + Groq/Whisper (transcripción)

Variables de entorno:
  TELEGRAM_BOT_TOKEN   → token del bot
  GEMINI_API_KEY       → generación de borradores
  GEMINI_MODEL         → ID del modelo Gemini (default: gemini-3.5-flash)
                         Para pasar a Pro cuando haya billing: gemini-3.1-pro-preview
  GROQ_API_KEY         → repreguntas (Llama 3.3) + transcripción audio (Whisper)
  RESEND_API_KEY       → envío de email al equipo editorial
  EDITORIAL_EMAIL      → destinatario del borrador
  BOT_PASSWORD         → contraseña de acceso
  RENDER_EXTERNAL_URL  → URL pública del servicio en Render (para webhook)
  MINI_APP_URL         → URL de la mini app de grabación (opcional)
"""

import os
import base64
import logging
import json
import requests
import re
import asyncio
import time
import threading
import urllib.request
from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Falta TELEGRAM_BOT_TOKEN")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    logger.error("RENDER_EXTERNAL_URL no configurada. El webhook no funcionará.")
PORT = int(os.getenv("PORT", 8080))

# Modelo Gemini configurable por entorno (free tier: gemini-3.5-flash; pago: gemini-3.1-pro-preview)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Mensaje de error estándar
MSG_ERROR_GENERAR = (
    "⚠️ *No pude generar el borrador en este momento.*\n\n"
    "Puede ser un problema temporal del servicio de IA. "
    "Tus respuestas no se perdieron — escribí /reiniciar para volver a empezar el flujo."
)

# Estados
(AUTENTICACION, IDENTIFICACION, SELECCION_GENERO,
 RESPONDIENDO_PREGUNTA, REVISION_RESUMEN, EDITANDO_RESPUESTA,
 ESPERANDO_FOTOS, RECOLECTANDO_TESTIMONIOS) = range(8)

# ═══════════════════════════════════════════════════════════════
# GÉNEROS — solo Historia de vida, Denuncia, Reportaje
# ═══════════════════════════════════════════════════════════════

GENEROS = {
    "historia_vida": {"nombre": "Historia de vida", "descripcion": "Testimonio biográfico de una persona migrante", "fotos_min": 3, "estructura": "cronica"},
    "denuncia":      {"nombre": "Denuncia",          "descripcion": "Situación de vulneración de derechos",          "fotos_min": 2, "estructura": "analisis"},
    "reportaje":     {"nombre": "Reportaje",         "descripcion": "Análisis profundo de un fenómeno",              "fotos_min": 2, "estructura": "reportaje"},
}

# ═══════════════════════════════════════════════════════════════
# FLUJOS CONVERSACIONALES
# ═══════════════════════════════════════════════════════════════

FLUJO_HISTORIA_VIDA = {
    "entrada": (
        "📖 *Historia de vida*\n\n"
        "Vas a registrar una historia de vida. Recordá: no es un interrogatorio sino una conversación empática. "
        "La persona entrevistada es sujeto de derechos y protagonista de su historia — no víctima ni héroe.\n\n"
        "💡 _Tip:_ Si la persona se tensiona con alguna pregunta, podés saltearla y volver después.\n\n"
        "Primero, contame brevemente *nombre completo, edad, ocupación y país de origen* de la persona que vas a entrevistar."
    ),
    "preguntas": [
        {"clave": "identificacion", "texto": "👤 *Datos de identificación*\n\nNombre completo, edad, ocupación y país de origen de la persona entrevistada."},
        {"clave": "origen",         "texto": "🌍 *1. Origen*\n\n¿De dónde viene? ¿Cómo era su vida antes de migrar? ¿En qué año llegó al país y cuántos años tenía cuando dejó su lugar de origen? ¿Vino sola, o con familia, pareja y/o amigos?"},
        {"clave": "motivos",        "texto": "🔄 *2. Motivos de movilidad*\n\n¿Qué razones le llevaron a emigrar? ¿Qué significó ese momento? ¿Cómo planificó su partida?"},
        {"clave": "transito",       "texto": "🛤️ *3. Tránsito*\n\n¿Cómo fue el viaje? ¿Qué experiencias, obstáculos o emociones marcaron ese trayecto?"},
        {"clave": "llegada",        "texto": "📍 *4. Llegada*\n\n¿Cuáles fueron sus primeras impresiones al llegar? ¿Qué situaciones o desafíos recuerda de esos primeros días? ¿Contaba con contactos previos con otros miembros de su comunidad?"},
        {"clave": "laboral",        "texto": "💼 *5. Inserción laboral*\n\n¿Cómo fue su inserción laboral en el país? ¿Actualmente está trabajando? ¿Trabaja por su cuenta o en relación de dependencia?"},
        {"clave": "presente",       "texto": "🏡 *6. Presente*\n\n¿Cómo es su vida hoy? ¿A qué se dedica, qué vínculos construyó? ¿Se relaciona con personas de su comunidad acá? ¿Qué cosas echa de menos de su lugar de origen? ¿Cuáles son sus proyectos aquí?"},
        {"clave": "horizonte",      "texto": "🔮 *7. Horizonte*\n\n¿Cómo vive hoy su identidad y sentido de pertenencia? ¿Piensa en regresar a su tierra de origen o proyecta su futuro acá?"},
    ],
}

FLUJO_DENUNCIA = {
    "entrada": (
        "⚖️ *Denuncia*\n\n"
        "Vas a registrar una denuncia. En Refugio abordamos las denuncias desde una perspectiva de derechos humanos — "
        "la persona migrante o refugiada como titular de derechos y agente activo. "
        "Evitamos lenguaje victimizante y responsabilizamos a las instituciones como titulares de obligaciones.\n\n"
        "Contame en tus propias palabras qué está ocurriendo."
    ),
    "preguntas": [
        {"clave": "naturaleza",              "texto": "🔍 *1. Naturaleza del problema*\n\n¿Qué tipo de situación se vive? (vulneración de derechos, discriminación, obstáculos para acceder a servicios, abuso institucional, violencia, trámite irregular). ¿Es un hecho puntual o una situación sostenida? ¿Afecta a una o muchas personas en situación similar?"},
        {"clave": "personas_afectadas",      "texto": "👥 *2. Personas afectadas*\n\n¿Quiénes son las personas afectadas? ¿Se trata de una persona, familia, comunidad? ¿De qué país o comunidad provienen? ¿Qué las llevó a dejar su lugar de origen? ¿Hace cuánto viven en el país donde ocurre la situación? ¿Cuál es su situación migratoria actual (con residencia, en trámite, solicitantes de refugio, situación irregular)? ¿Hay dimensiones específicas (niñez, personas mayores, embarazadas, mujeres víctimas de violencia de género, personas LGBTIQ+, personas con discapacidad)?"},
        {"clave": "identificacion_afectadas","texto": "🔐 *3. Cómo quieren ser identificadas*\n\n¿Cómo les gustaría a las personas afectadas ser identificadas en la nota? ¿Con sus nombres completos, iniciales o seudónimo? _Si están en situación de solicitud de refugio o irregularidad, siempre es mejor proteger su identidad._"},
        {"clave": "responsables",            "texto": "🏛️ *4. Responsables*\n\n¿Quiénes son los responsables? (autoridad estatal, institución pública, empresa, particular). ¿Nombre, cargo, dependencia concreta? ¿Existe un marco normativo que se está incumpliendo?"},
        {"clave": "lugar_momento",           "texto": "📍 *5. Lugar y momento*\n\n¿Dónde ocurre? (país, provincia, ciudad, barrio, dirección). ¿Cuándo? ¿Hecho puntual o sostenido en el tiempo?"},
        {"clave": "gestiones",               "texto": "📋 *6. Gestiones previas*\n\n¿Las personas afectadas ya hicieron denuncia formal? ¿Dónde? ¿Número de expediente o acta? ¿Contactaron algún organismo, ONG, consulado, defensoría? ¿Qué respuesta recibieron?"},
        {"clave": "testimonios",             "texto": "📢 *7. Testimonios y pruebas*\n\n¿Hay otras personas que hayan vivido o visto lo mismo y puedan testimoniar? ¿Documentos, capturas, audios, comunicaciones oficiales, pruebas materiales? ¿Alguna fuente experta (organización, abogada, académica, referente) que pueda aportar contexto?"},
        {"clave": "impacto",                 "texto": "💥 *8. Impacto personal y comunitario*\n\n¿Cómo afecta la vida cotidiana de las personas? _(sin enfocar solo en el sufrimiento — también en cómo resisten, se organizan, se defienden)_ ¿Qué consecuencias tiene en la comunidad más amplia? ¿Se están organizando para responder?"},
        {"clave": "expectativas",            "texto": "🎯 *9. Qué esperan*\n\n¿Qué esperan lograr al visibilizar esta situación? ¿Hay demanda específica hacia alguna autoridad?"},
        {"clave": "contraste",               "texto": "⚖️ *10. Contraste editorial*\n\n¿Refugio debería buscar la palabra de la institución, funcionario o empresa señalada antes de publicar? ¿O se publica tal como llega y esperamos una eventual respuesta?"},
    ],
}

FLUJO_REPORTAJE = {
    "entrada": (
        "📰 *Reportaje*\n\n"
        "Vas a registrar un reportaje — un análisis profundo de un fenómeno vinculado a las migraciones. "
        "Después de las preguntas base, vas a poder sumar testimonios de fuentes externas.\n\n"
        "Contame en tus propias palabras de qué se trata el fenómeno que querés abordar."
    ),
    "preguntas": [
        {"clave": "tema",        "texto": "📰 *1. Tema central*\n\n¿Cuál es el fenómeno o proceso que querés analizar? ¿Por qué es relevante hoy para la comunidad migrante?"},
        {"clave": "contexto",    "texto": "🗺️ *2. Contexto*\n\n¿Cuál es el trasfondo histórico, social o político? ¿Es algo nuevo o una situación estructural?"},
        {"clave": "protagonistas","texto": "👥 *3. Protagonistas*\n\n¿Qué personas, comunidades u organizaciones están involucradas? ¿Cómo viven o enfrentan este fenómeno?"},
        {"clave": "datos",       "texto": "📊 *4. Datos y evidencia*\n\n¿Qué datos, cifras, informes o fuentes documentales respaldan el reportaje? ¿De dónde provienen?"},
        {"clave": "tensiones",   "texto": "⚡ *5. Tensiones y conflictos*\n\n¿Qué intereses están en juego? ¿Hay responsables institucionales o disputas que el reportaje deba señalar?"},
        {"clave": "agencia",     "texto": "✊ *6. Agencia y respuestas*\n\n¿Cómo se organizan, resisten o responden las personas y comunidades afectadas?"},
        {"clave": "proyeccion",  "texto": "🔮 *7. Proyección*\n\n¿Hacia dónde va este fenómeno? ¿Qué debería cambiar y qué se espera a futuro?"},
    ],
}


def obtener_flujo(genero_key: str) -> dict:
    if genero_key == "historia_vida":
        return FLUJO_HISTORIA_VIDA
    elif genero_key == "denuncia":
        return FLUJO_DENUNCIA
    elif genero_key == "reportaje":
        return FLUJO_REPORTAJE
    return FLUJO_DENUNCIA


# ═══════════════════════════════════════════════════════════════
# PROMPTS EDITORIALES
# ═══════════════════════════════════════════════════════════════

PROMPT_BASE = """Sos editor/a periodístico de Refugio Latinoamericano, medio digital especializado en periodismo de migraciones con perspectiva de derechos humanos e interculturalidad.

CRITERIOS EDITORIALES OBLIGATORIOS (aplican a todo lo que escribas):
- Nunca: "ilegal", "clandestino", "indocumentado", "oleada", "avalancha", "aluvión", "asalto", "invasión", "catástrofe", "personas vulnerables"
- Siempre anteponer "persona": persona migrante, persona refugiada, persona solicitante
- Diferenciar migrante / refugiada / solicitante de asilo
- Nunca masculino genérico
- Persona migrante como sujeto de derechos y agente activo — no víctima, no héroe
- Responsabilizar a Estados e instituciones como titulares de obligaciones
- Voz activa, oraciones cortas (<18 palabras), sin adjetivos innecesarios
- Detalles concretos — transportar al lector a la escena

FORMATO DE SALIDA OBLIGATORIO — tu respuesta tiene DOS PARTES, en este orden exacto:

══════ PARTE 1: LA NOTA ══════

TÍTULO: (máximo 12 palabras, sin punto final, informativo)

BAJADA: (2-3 oraciones que complementan el título)

(Cuerpo de la nota: párrafos corridos, SIN subtítulos, SIN numeración, SIN rótulos.
Es una nota periodística lista para leer, no un documento por secciones.
- El primer párrafo (lead) responde: qué, quién, cuándo y dónde.
- El segundo párrafo completa: cómo y por qué.
- Después, desarrollo en orden de importancia decreciente (pirámide invertida).
- Las citas textuales van integradas en la narración, con atribución clara.
- Si un dato no está en el reporte del corresponsal, NO lo inventes: marcalo [VERIFICAR].)

══════ PARTE 2: DESGLOSE EDITORIAL ══════

(Esta parte es para el equipo editorial, no se publica. Incluye:)

CHECKLIST DE ELEMENTOS: la estructura del género desglosada por secciones rotuladas, indicando qué información del reporte cubre cada elemento y qué falta.

VERIFICACIÓN PENDIENTE: lista de datos a verificar, cada uno con [VERIFICAR].

ETIQUETAS SUGERIDAS: 3-5 etiquetas.

NOTAS PARA EL EDITOR/A: consentimientos, protección de identidades, contraste pendiente, riesgos."""

PROMPT_HISTORIA_VIDA = PROMPT_BASE + """

GÉNERO: HISTORIA DE VIDA (crónica biográfica)

Para LA NOTA (Parte 1):
- Extensión del cuerpo: 500-700 palabras.
- Excepción a la pirámide invertida: el cuerpo sigue un orden CRONOLÓGICO (origen → motivos → tránsito → llegada → inserción laboral → presente → horizonte), pero siempre en párrafos corridos, sin subtítulos.
- El lead presenta a la persona: quién es, de dónde viene, qué hace hoy — y el eje de su historia.
- Incluí al menos una cita textual de la persona entrevistada, integrada en la narración.
- El cierre queda abierto: la historia continúa, no la resuelvas artificialmente.

Para el DESGLOSE EDITORIAL (Parte 2), el checklist cubre: identificación, origen, motivos, tránsito, llegada, inserción laboral, presente, horizonte."""

PROMPT_DENUNCIA = PROMPT_BASE + """

GÉNERO: DENUNCIA (nota de actualidad con perspectiva de derechos)

Para LA NOTA (Parte 1):
- Extensión del cuerpo: 400-600 palabras.
- Pirámide invertida estricta: el lead responde qué situación se denuncia, quiénes la sufren, dónde y desde cuándo. El segundo párrafo: cómo ocurre y quiénes son los responsables.
- El desarrollo cubre, en párrafos corridos: responsables y marco normativo incumplido, gestiones previas y respuestas recibidas, impacto en la vida cotidiana mostrando también cómo las personas se organizan y responden, y qué esperan lograr.
- Incluí al menos una cita textual de las personas afectadas, integrada y atribuida según cómo pidieron ser identificadas.
- Si el corresponsal indicó que corresponde buscar contraste con la parte señalada, mencioná en la nota que se intentará obtener su palabra.

Para el DESGLOSE EDITORIAL (Parte 2), el checklist cubre: naturaleza del problema, personas afectadas, identificación elegida, responsables, lugar y momento, gestiones previas, testimonios y pruebas, impacto, expectativas, contraste editorial. Prestá especial atención en NOTAS PARA EL EDITOR/A a: protección de identidades (por defecto si hay solicitantes de refugio o situación irregular) y riesgo de represalia."""

PROMPT_REPORTAJE = PROMPT_BASE + """

GÉNERO: REPORTAJE (análisis en profundidad)

Para LA NOTA (Parte 1):
- Extensión del cuerpo: 500-700 palabras.
- El lead presenta el tema y su relevancia actual para la comunidad migrante (qué pasa, a quiénes afecta, dónde, desde cuándo).
- El desarrollo, en párrafos corridos: contexto histórico o estructural, cómo lo viven las personas y comunidades protagonistas, datos y evidencia con sus fuentes, tensiones y responsables institucionales, y cómo se organizan y responden las comunidades.
- Integrá los TESTIMONIOS de fuentes externas como citas textuales dentro de la narración, con nombre (o alias), nacionalidad y organización si la hay. Los testimonios son la voz central del reportaje: usalos todos.
- El cierre proyecta: hacia dónde va la situación y qué debería cambiar.

Para el DESGLOSE EDITORIAL (Parte 2), el checklist cubre: tema central, contexto, protagonistas, datos y evidencia, tensiones, agencia, proyección, y un listado de los testimonios incluidos con sus datos. En NOTAS PARA EL EDITOR/A: fuentes a contrastar, datos a verificar, protección de identidades."""


def obtener_prompt(genero_key: str) -> str:
    if genero_key == "historia_vida":
        return PROMPT_HISTORIA_VIDA
    elif genero_key == "denuncia":
        return PROMPT_DENUNCIA
    elif genero_key == "reportaje":
        return PROMPT_REPORTAJE
    return PROMPT_DENUNCIA


# ═══════════════════════════════════════════════════════════════
# GROQ — repreguntas + transcripción de audio
# ═══════════════════════════════════════════════════════════════

def llamar_groq(messages: list, max_tokens: int = 400, temperature: float = 0.3,
                response_format: dict = None):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("Falta GROQ_API_KEY")
        return None
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=90
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        logger.error(f"Error Groq: {data.get('error', {}).get('message', 'desconocido')}")
        return None
    except Exception as e:
        logger.error(f"Error llamando a Groq: {e}")
        return None


PROMPT_ANALISTA = """Sos editor/a de campo de Refugio Latinoamericano, un medio de periodismo de migraciones con perspectiva de derechos humanos. Estás acompañando a un corresponsal mientras completa un cuestionario, pregunta por pregunta.

Tu tarea: decidir si la respuesta del corresponsal a UNA pregunta específica necesita una repregunta para enriquecer el material periodístico.

Repreguntá SOLO si se cumple alguno de estos criterios:
1. PROFUNDIDAD: la respuesta es vaga, genérica o demasiado breve para sostener un párrafo periodístico (menos de ~15 palabras significativas).
2. INCONSISTENCIA TEMPORAL: hay referencias temporales contradictorias o confusas.
3. AMBIGÜEDAD: falta un dato clave que la pregunta pedía explícitamente.

NO repreguntés si:
- La respuesta es clara, concreta y específica, aunque sea breve.
- El corresponsal ya respondió lo esencial de la pregunta.
- Sería redundante con lo que ya dijo.

Si repreguntás, la repregunta debe:
- Empezar con un eco empático breve (parafraseo de lo que dijo el corresponsal).
- Pedir SOLO lo que falta de ESTA pregunta, sin adelantarte a preguntas futuras del cuestionario.
- Estar redactada en español rioplatense (voseo), tono cálido y profesional.
- Ser una sola repregunta concreta, no una lista.

Respondé EXCLUSIVAMENTE con un JSON válido, sin texto adicional:
{"necesita_repregunta": true/false, "tipo": "profundidad"|"inconsistencia"|"ambiguedad"|null, "repregunta": "texto"|null}"""


def analizar_respuesta_con_groq(pregunta: str, respuesta: str, genero_nombre: str = "") -> dict:
    pregunta_limpia = re.sub(r'[*_]', '', pregunta).strip()
    contexto = f"GÉNERO PERIODÍSTICO: {genero_nombre}\n\n" if genero_nombre else ""
    resultado = llamar_groq(
        messages=[
            {"role": "system", "content": PROMPT_ANALISTA},
            {"role": "user", "content": (
                f"{contexto}PREGUNTA DEL CUESTIONARIO:\n{pregunta_limpia}\n\n"
                f"RESPUESTA DEL CORRESPONSAL:\n{respuesta}\n\n"
                f"Analizá y respondé SOLO con el JSON."
            )}
        ],
        max_tokens=400, temperature=0.3,
        response_format={"type": "json_object"}
    )
    if resultado:
        try:
            parsed = json.loads(resultado)
            if not isinstance(parsed.get("necesita_repregunta"), bool):
                return {"necesita_repregunta": False, "tipo": None, "repregunta": None}
            if parsed["necesita_repregunta"] and not parsed.get("repregunta"):
                return {"necesita_repregunta": False, "tipo": None, "repregunta": None}
            return parsed
        except Exception as e:
            logger.error(f"Error parseando JSON repreguntas: {e}")
    return {"necesita_repregunta": False, "tipo": None, "repregunta": None}


# Marcador de transcripción fallida (centralizado para detección)
ERROR_TRANSCRIPCION = "[Error al transcribir. Respondé en texto.]"


async def transcribir_audio_groq(file_id: str, bot) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return ERROR_TRANSCRIPCION
    try:
        file = await bot.get_file(file_id)
        audio_bytes = await file.download_as_bytearray()
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.ogg", bytes(audio_bytes), "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "es",
                  "prompt": "Entrevista periodística sobre migraciones en América Latina."},
            timeout=60
        )
        data = response.json()
        if "text" in data and data["text"].strip():
            return f"{data['text'].strip()} [transcripto de audio]"
        return ERROR_TRANSCRIPCION
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return ERROR_TRANSCRIPCION


def transcripcion_fallida(texto: str) -> bool:
    """Detecta si una transcripción falló (para no guardarla como respuesta)."""
    return texto.strip() == ERROR_TRANSCRIPCION or texto.startswith("[Error")


# ═══════════════════════════════════════════════════════════════
# GEMINI — generación de borradores (robusto, anti-bloqueo)
# ═══════════════════════════════════════════════════════════════

GEMINI_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


def _intento_gemini(url: str, payload: dict):
    """
    Un único intento contra Gemini. Devuelve (texto, motivo_error, status_code).
    status_code permite a la capa superior decidir si reintenta (503) o no (429).
    """
    try:
        r = requests.post(url, json=payload, timeout=120)
        status = r.status_code
        try:
            data = r.json()
        except Exception:
            return None, f"Respuesta no-JSON de Gemini (HTTP {status})", status

        # Log de trazabilidad: qué modelo respondió realmente
        if "modelVersion" in data:
            logger.info(f"Gemini respondió con modelo: {data['modelVersion']}")

        # 1) Bloqueo del PROMPT DE ENTRADA
        prompt_feedback = data.get("promptFeedback", {})
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            return None, f"Prompt bloqueado por Gemini (blockReason={block_reason})", status

        # 2) Error explícito de la API
        if "error" in data:
            msg = data["error"].get("message", "desconocido")
            return None, f"Error API Gemini (HTTP {status}): {msg}", status

        # 3) Sin candidates
        candidates = data.get("candidates", [])
        if not candidates:
            return None, "Gemini no devolvió candidates", status

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        texto = parts[0].get("text", "").strip() if parts else ""

        # 4) Evaluar finishReason
        if finish_reason == "SAFETY":
            return None, "Respuesta bloqueada por filtros de seguridad (finishReason=SAFETY)", status
        if finish_reason == "RECITATION":
            return None, "Respuesta bloqueada por recitación (finishReason=RECITATION)", status
        if finish_reason == "MAX_TOKENS":
            # Texto cortado a mitad de camino: NO sirve aunque haya contenido parcial.
            # Mejor caer al fallback que entregar una nota incompleta.
            return None, "Salida truncada por límite de tokens (finishReason=MAX_TOKENS)", status

        if texto:
            return texto, None, status

        return None, f"Gemini devolvió texto vacío (finishReason={finish_reason or 'desconocido'})", status

    except requests.exceptions.Timeout:
        return None, "Timeout llamando a Gemini", 0
    except Exception as e:
        return None, f"Excepción llamando a Gemini: {e}", 0


def llamar_gemini(prompt_sistema: str, prompt_usuario: str, max_tokens: int = 24000):
    """
    Llama a Gemini (modelo configurable). Devuelve (texto, motivo_error).
    - max_tokens alto (24000): en Gemini 3 los tokens de RAZONAMIENTO (thinking)
      cuentan contra maxOutputTokens. Con thinkingLevel=high, el modelo puede usar
      miles de tokens pensando antes de escribir; un límite bajo trunca la nota.
      Solo se cobra lo efectivamente generado, así que el límite alto no cuesta más.
    - 503 ServiceUnavailable (sobrecarga temporal de Google): reintenta UNA vez tras una espera.
    - 429 TooManyRequests (límite de cuota): NO reintenta — sería gastar cuota en vano.
      Cae directo al fallback de Groq en la capa superior.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "Falta GEMINI_API_KEY"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    payload = {
        "system_instruction": {"parts": [{"text": prompt_sistema}]},
        "contents": [{"role": "user", "parts": [{"text": prompt_usuario}]}],
        "safetySettings": GEMINI_SAFETY_SETTINGS,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.6,
            "responseMimeType": "text/plain",
            # thinkingLevel alto: la doble salida (nota fluida + desglose editorial)
            # requiere que el modelo planifique la estructura narrativa antes de escribir.
            # Es una sola generación por reporte, el costo extra es mínimo.
            "thinkingConfig": {"thinkingLevel": "high"},
        },
    }

    # Primer intento
    texto, motivo, status = _intento_gemini(url, payload)
    if texto:
        return texto, None

    # 503: sobrecarga temporal de Google → reintento único con espera corta
    if status == 503:
        logger.warning("Gemini devolvió 503 (sobrecarga). Reintentando una vez en 4s...")
        time.sleep(4)
        texto, motivo, status = _intento_gemini(url, payload)
        if texto:
            return texto, None

    # 429 o cualquier otro fallo: no reintentar, devolver el motivo
    return None, motivo


def _construir_prompt_usuario(respuestas: dict, nombre: str, genero_nombre: str, fotos: int,
                              testimonios: list = None, ampliacion: str = "") -> str:
    datos = "\n".join(f"{k.upper()}: {v}" for k, v in respuestas.items())
    texto_extra = ""
    if testimonios:
        texto_extra = "\n\n=== TESTIMONIOS ===\n"
        for i, t in enumerate(testimonios, 1):
            texto_extra += f"Testimonio {i}:\nNombre: {t.get('nombre', '')}\n"
            if t.get("organizacion"):
                texto_extra += f"Organización: {t['organizacion']}\n"
            texto_extra += f"Nacionalidad: {t.get('nacionalidad', '')}\n"
            if t.get("edad"):
                texto_extra += f"Edad: {t['edad']}\n"
            texto_extra += f"Pregunta 1: {t.get('pregunta1', '')}\n"
            if t.get("pregunta2"):
                texto_extra += f"Pregunta 2: {t['pregunta2']}\n"
            texto_extra += f"Respuesta: {t.get('respuesta', '')}\n\n"
    if ampliacion:
        texto_extra += f"\n=== AMPLIACIÓN ===\n{ampliacion}\n"
    return (
        f"GÉNERO: {genero_nombre}\n"
        f"CORRESPONSAL: {nombre}\n"
        f"FOTOS ADJUNTAS: {fotos}\n\n"
        f"REPORTE DEL CORRESPONSAL:\n{datos}{texto_extra}\n\n"
        f"Redactá el borrador completo respetando TODOS los criterios editoriales."
    )


def generar_borrador(respuestas: dict, nombre: str, genero_key: str, fotos: int,
                     testimonios: list = None, ampliacion: str = ""):
    """
    Genera el borrador con Gemini. Si Gemini falla, cae a Groq.
    Devuelve (borrador, exito_bool).
    """
    prompt_sistema = obtener_prompt(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]
    instruccion = "\n\nUSÁ ÚNICAMENTE la información proporcionada. NO inventes datos, nombres, fechas ni estadísticas. Si falta un dato, marcalo con [VERIFICAR]."
    prompt_sistema_completo = prompt_sistema + instruccion
    prompt_usuario = _construir_prompt_usuario(respuestas, nombre, genero_nombre, fotos, testimonios, ampliacion)

    texto, motivo_error = llamar_gemini(prompt_sistema_completo, prompt_usuario, max_tokens=24000)
    if texto:
        return texto, True

    logger.warning(f"Gemini no generó borrador ({motivo_error}). Probando fallback Groq.")

    resultado_groq = llamar_groq(
        messages=[
            {"role": "system", "content": prompt_sistema_completo},
            {"role": "user", "content": prompt_usuario}
        ],
        max_tokens=6000, temperature=0.7
    )
    if resultado_groq:
        logger.info("Borrador generado con fallback Groq.")
        return resultado_groq, True

    logger.error("Falló Gemini y también el fallback Groq.")
    return None, False


# ═══════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════

def extraer_titulo(borrador: str) -> str:
    match = re.search(r'\*{0,2}T[IÍ]TULO\*{0,2}:\s*(.+)', borrador, re.IGNORECASE)
    if match:
        # Tomar solo la primera línea y limpiar markdown/divisores
        titulo = match.group(1).split("\n")[0].strip().strip("*").strip()
        titulo = titulo.replace("═", "").strip()
        if titulo:
            return titulo
    return "Borrador sin título"


def construir_resumen(respuestas: dict, flujo: dict, genero_key: str = None,
                      testimonios: list = None) -> str:
    preguntas = flujo["preguntas"]
    lineas = ["📋 *Resumen de tu reporte*\n"]
    for i, pregunta in enumerate(preguntas):
        clave = pregunta["clave"]
        titulo = pregunta["texto"].split("\n")[0].replace("*", "").strip()
        respuesta = respuestas.get(clave, "_Sin respuesta_")
        if len(respuesta) > 200:
            respuesta = respuesta[:200] + "..."
        lineas.append(f"*{i+1}. {titulo}*\n{respuesta}")
    if genero_key == "reportaje" and testimonios:
        lineas.append(f"\n📢 *Testimonios recolectados: {len(testimonios)}*")
        for i, t in enumerate(testimonios, 1):
            lineas.append(f"  {i}. {t.get('nombre', 'Sin nombre')} ({t.get('nacionalidad', '')})")
    return "\n\n".join(lineas)


def teclado_resumen() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar una respuesta", callback_data="resumen:editar")],
        [InlineKeyboardButton("✅ Todo correcto, continuar", callback_data="resumen:confirmar")],
    ])


def teclado_numeros(flujo: dict) -> InlineKeyboardMarkup:
    preguntas = flujo["preguntas"]
    botones, fila = [], []
    for i in range(len(preguntas)):
        fila.append(InlineKeyboardButton(str(i + 1), callback_data=f"editar:{i}"))
        if len(fila) == 5:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    botones.append([InlineKeyboardButton("↩️ Volver al resumen", callback_data="resumen:volver")])
    return InlineKeyboardMarkup(botones)


def teclado_generos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Historia de vida", callback_data="genero:historia_vida")],
        [InlineKeyboardButton("⚖️ Denuncia",         callback_data="genero:denuncia")],
        [InlineKeyboardButton("📰 Reportaje",         callback_data="genero:reportaje")],
    ])


def teclado_testimonio_opciones() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar otro testimonio (máx 3)", callback_data="testimonio:agregar")],
        [InlineKeyboardButton("✅ Finalizar testimonios",           callback_data="testimonio:finalizar")],
    ])


def teclado_consentimiento_fotos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Sí, tengo consentimiento", callback_data="fotos:si")],
        [InlineKeyboardButton("🚫 No, sin fotos",            callback_data="fotos:no")],
    ])


def get_mini_app_url(pregunta_texto: str, clave: str) -> str:
    base_url = os.getenv("MINI_APP_URL", "")
    if not base_url:
        return ""
    import urllib.parse
    texto_limpio = pregunta_texto.replace("*", "").replace("_", "")[:200]
    params = urllib.parse.urlencode({"label": clave.upper(), "texto": texto_limpio, "key": clave})
    return f"{base_url}?{params}"


def construir_teclado_miniapp(pregunta_texto: str, clave: str):
    url = get_mini_app_url(pregunta_texto, clave)
    if url:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🎙️ Grabar respuesta en audio", web_app=WebAppInfo(url=url))
        ]])
    return None


async def descargar_fotos(file_ids: list, bot) -> list:
    fotos_bytes = []
    for i, file_id in enumerate(file_ids):
        try:
            file = await bot.get_file(file_id)
            foto_bytes = await file.download_as_bytearray()
            fotos_bytes.append({"nombre": f"foto_{i+1}.jpg", "datos": bytes(foto_bytes)})
        except Exception as e:
            logger.error(f"Error descargando foto {i+1}: {e}")
    return fotos_bytes


async def descargar_video(video_id: str, bot) -> dict:
    try:
        file = await bot.get_file(video_id)
        video_bytes = await file.download_as_bytearray()
        return {"nombre": "video.mp4", "datos": bytes(video_bytes)}
    except Exception as e:
        logger.error(f"Error descargando video: {e}")
        return None


def enviar_con_resend(borrador: str, nombre: str, titulo: str, genero_nombre: str,
                      fotos_bytes: list = None, video_bytes: dict = None) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    editorial_email = os.getenv("EDITORIAL_EMAIL")
    if not api_key or not editorial_email:
        return False
    cuerpo = (
        f"BORRADOR — REFUGIO LATINOAMERICANO\nPendiente de revisión editorial.\n\n"
        f"Género: {genero_nombre}\nCorresponsal: {nombre}\n"
        f"Fotos: {len(fotos_bytes) if fotos_bytes else 0}\nVideo: {'Sí' if video_bytes else 'No'}\n"
        f"{'─'*50}\n\n{borrador}\n\n{'─'*50}\n"
        f"Generado por el Chatbot de Refugio Latinoamericano."
    )
    payload = {
        "from": "Chatbot Refugio Latinoamericano <onboarding@resend.dev>",
        "to": [editorial_email],
        "subject": f"[{genero_nombre.upper()}] {titulo} — {nombre}",
        "text": cuerpo
    }
    attachments = []
    if fotos_bytes:
        attachments.extend([
            {"filename": f["nombre"], "content": base64.b64encode(f["datos"]).decode(), "type": "image/jpeg"}
            for f in fotos_bytes
        ])
    if video_bytes:
        attachments.append({
            "filename": video_bytes["nombre"],
            "content": base64.b64encode(video_bytes["datos"]).decode(),
            "type": "video/mp4"
        })
    if attachments:
        payload["attachments"] = attachments
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=90
        )
        return r.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False


# Validaciones de testimonios
NEGACIONES = {"no", "no.", "no hay", "ninguno", "ninguna", "nada", "-", "n/a", "na"}


def es_negacion(texto: str) -> bool:
    return texto.strip().lower() in NEGACIONES


# ═══════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════

async def comenzar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy el *Chatbot - Refugio Latinoamericano*.\n\n🔐 Ingresá la contraseña de acceso:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return AUTENTICACION


async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = context.user_data.get("nombre", "")
    context.user_data.clear()
    if nombre:
        context.user_data["nombre"] = nombre
    msg = (
        f"🔄 *Nuevo reporte*{f' — {nombre}' if nombre else ''}\n\n"
        "¿Qué tipo de nota vas a registrar?\n\n_Elegí el género periodístico._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=teclado_generos())
    return SELECCION_GENERO


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Comandos disponibles:*\n\n"
        "/comenzar — Iniciar sesión\n"
        "/reiniciar — Nuevo reporte (mantiene tu nombre)\n"
        "/generar — Generar borrador y enviar al equipo\n"
        "/listo — Confirmar fotos de testimonios (solo reportajes)\n"
        "/cancelar — Cancelar el reporte actual\n"
        "/ayuda — Este mensaje",
        parse_mode="Markdown"
    )


async def handle_autenticacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() == os.getenv("BOT_PASSWORD", ""):
        await update.message.reply_text(
            "✅ *Acceso autorizado.*\n\nIngresá tu *nombre y apellido completo*:",
            parse_mode="Markdown")
        return IDENTIFICACION
    await update.message.reply_text(
        "❌ *Contraseña incorrecta.*\n\nEscribí /comenzar para intentar de nuevo.",
        parse_mode="Markdown")
    return ConversationHandler.END


async def handle_identificacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = update.message.text.strip()
    if len(nombre.split()) < 2:
        await update.message.reply_text("Ingresá tu *nombre y apellido completo*.", parse_mode="Markdown")
        return IDENTIFICACION
    context.user_data.update({"nombre": nombre, "respuestas": {}, "fotos": 0, "foto_ids": []})
    await update.message.reply_text(
        f"Perfecto, *{nombre}*.\n\n¿Qué tipo de nota vas a registrar?\n\n_Elegí el género periodístico._",
        parse_mode="Markdown", reply_markup=teclado_generos()
    )
    return SELECCION_GENERO


async def handle_seleccion_genero(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    genero_key = query.data.split(":")[1]
    if genero_key not in GENEROS:
        await query.edit_message_text("Género no válido. Escribí /reiniciar para volver a empezar.")
        return ConversationHandler.END
    context.user_data.update({
        "genero": genero_key, "pregunta_idx": 0, "repregunta_activa": False,
        "respuestas": {}, "fotos": 0, "foto_ids": []
    })
    flujo = obtener_flujo(genero_key)
    await query.edit_message_text(f"✅ Seleccionaste: *{GENEROS[genero_key]['nombre']}*", parse_mode="Markdown")
    await query.message.reply_text(
        flujo["entrada"] + "\n\n⚠️ _Ningún contenido se publica sin revisión editorial._",
        parse_mode="Markdown"
    )
    await enviar_pregunta_actual(query.message, context)
    return RESPONDIENDO_PREGUNTA


async def enviar_pregunta_actual(message, context):
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    idx = context.user_data["pregunta_idx"]
    if idx >= len(flujo["preguntas"]):
        return
    pregunta = flujo["preguntas"][idx]
    total = len(flujo["preguntas"])
    teclado = construir_teclado_miniapp(pregunta["texto"], pregunta["clave"])
    texto = f"*Pregunta {idx+1}/{total}*\n\n{pregunta['texto']}\n\n_Escribí tu respuesta o usá el botón para grabar un audio._"
    await message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)


async def handle_respuesta_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await procesar_respuesta(update, context, update.message.text)


async def handle_respuesta_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
    # Si falló la transcripción, NO guardar el error como respuesta
    if transcripcion_fallida(texto):
        await update.message.reply_text(
            "⚠️ No pude transcribir ese audio. Probá grabarlo de nuevo (hablá un poco más fuerte y claro) "
            "o escribí la respuesta en texto."
        )
        return RESPONDIENDO_PREGUNTA
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await procesar_respuesta(update, context, texto)


async def handle_respuesta_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        if data.get("type") == "audio":
            await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
            mini_app_url = os.getenv("MINI_APP_URL", "")
            audio_b64 = data.get("audio_b64", "")
            if mini_app_url and audio_b64:
                r = requests.post(f"{mini_app_url}/transcribir", json={"audio_b64": audio_b64}, timeout=60)
                if r.status_code == 200:
                    texto = r.json().get("texto", "")
                    if texto and texto.strip():
                        await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
                        return await procesar_respuesta(update, context, f"{texto} [audio]")
            await update.message.reply_text("❌ No se pudo transcribir. Respondé en texto.")
            return RESPONDIENDO_PREGUNTA
    except Exception as e:
        logger.error(f"Error web_app_data: {e}")
        await update.message.reply_text("❌ Error procesando el audio.")
        return RESPONDIENDO_PREGUNTA


async def procesar_respuesta(update, context, texto_respuesta: str) -> int:
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    idx = context.user_data["pregunta_idx"]
    pregunta = flujo["preguntas"][idx]
    clave = pregunta["clave"]
    genero_nombre = GENEROS[genero_key]["nombre"]

    if len(texto_respuesta.strip()) < 3:
        await update.message.reply_text("📝 Necesito más información para continuar.")
        return RESPONDIENDO_PREGUNTA

    # Primera vez en esta pregunta → evaluar repregunta
    if not context.user_data.get("repregunta_activa", False):
        await update.message.reply_text("🔎 _Analizando respuesta..._", parse_mode="Markdown")
        analisis = analizar_respuesta_con_groq(pregunta["texto"], texto_respuesta, genero_nombre)
        if analisis.get("necesita_repregunta") and analisis.get("repregunta"):
            context.user_data["respuestas"][clave] = texto_respuesta.strip()
            context.user_data["repregunta_activa"] = True
            await update.message.reply_text(f"💬 {analisis['repregunta']}", parse_mode="Markdown")
            return RESPONDIENDO_PREGUNTA
        else:
            context.user_data["respuestas"][clave] = texto_respuesta.strip()
    else:
        # Ampliación tras repregunta → concatenar legible
        respuesta_previa = context.user_data["respuestas"].get(clave, "").strip()
        context.user_data["respuestas"][clave] = (
            f"{respuesta_previa}\n\n[Ampliación]: {texto_respuesta.strip()}"
        )
        context.user_data["repregunta_activa"] = False

    context.user_data["repregunta_activa"] = False
    context.user_data["pregunta_idx"] += 1
    idx_nuevo = context.user_data["pregunta_idx"]

    if idx_nuevo < len(flujo["preguntas"]):
        await update.message.reply_text(f"✓ Respuesta registrada ({idx_nuevo}/{len(flujo['preguntas'])})")
        await enviar_pregunta_actual(update.message, context)
        return RESPONDIENDO_PREGUNTA
    else:
        if genero_key == "reportaje":
            return await iniciar_testimonios(update, context)
        else:
            return await mostrar_resumen(update.message, context)


async def iniciar_testimonios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.update({
        "testimonios": [], "testimonio_actual": {}, "testimonio_paso": "nombre",
        "fotos_testimonios": [], "consentimiento_fotos": None,
        "esperando_fotos_testimonios": False, "esperando_ampliacion": False
    })
    await update.message.reply_text(
        "📢 *Testimonios (Reportaje)*\n\n"
        "Necesitamos al menos *dos testimonios* de fuentes externas.\n"
        "Por cada persona: nombre/alias, organización (opcional), nacionalidad, edad (opcional), "
        "pregunta 1 (obligatoria), pregunta 2 (opcional), respuesta.\n\n"
        "Empecemos con el *primer testimonio*.\n\n✏️ *Nombre o alias:*",
        parse_mode="Markdown"
    )
    return RECOLECTANDO_TESTIMONIOS


async def handle_testimonio_texto(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   texto_alternativo: str = None) -> int:
    if context.user_data.get("esperando_ampliacion"):
        texto = texto_alternativo if texto_alternativo is not None else update.message.text.strip()
        context.user_data["ampliacion_info"] = "" if texto == "-" else texto
        context.user_data["esperando_ampliacion"] = False
        return await mostrar_resumen(update.message, context)

    texto = texto_alternativo if texto_alternativo is not None else update.message.text.strip()
    paso = context.user_data.get("testimonio_paso", "nombre")
    actual = context.user_data.get("testimonio_actual", {})

    if paso == "nombre":
        # Validación: el nombre no puede ser una negación ni demasiado corto
        if not texto or es_negacion(texto) or len(texto) < 2:
            await update.message.reply_text(
                "Necesito un nombre o alias válido para identificar a quien testimonia. "
                "Si querés proteger su identidad, podés usar un seudónimo (ej: 'Testigo 1', 'María')."
            )
            return RECOLECTANDO_TESTIMONIOS
        actual["nombre"] = texto
        context.user_data["testimonio_paso"] = "organizacion"
        await update.message.reply_text("📌 *Organización* (opcional — enviá '-' si no aplica):", parse_mode="Markdown")

    elif paso == "organizacion":
        actual["organizacion"] = "" if texto == "-" else texto
        context.user_data["testimonio_paso"] = "nacionalidad"
        await update.message.reply_text("🌎 *Nacionalidad* (obligatorio):", parse_mode="Markdown")

    elif paso == "nacionalidad":
        if not texto or es_negacion(texto):
            await update.message.reply_text("La nacionalidad es obligatoria. Indicá el país de origen de la persona.")
            return RECOLECTANDO_TESTIMONIOS
        actual["nacionalidad"] = texto
        context.user_data["testimonio_paso"] = "edad"
        await update.message.reply_text("🎂 *Edad* (opcional — enviá '-' si no querés decirla):", parse_mode="Markdown")

    elif paso == "edad":
        # Edad: vacía si es '-' o negación; si se da, debe contener un número
        if texto == "-" or es_negacion(texto):
            actual["edad"] = ""
        elif re.search(r'\d', texto):
            actual["edad"] = texto
        else:
            await update.message.reply_text("Si querés indicar la edad, ingresá un número (ej: 34). Si no, enviá '-'.")
            return RECOLECTANDO_TESTIMONIOS
        context.user_data["testimonio_paso"] = "pregunta1"
        await update.message.reply_text(
            "❓ *Primera pregunta (obligatoria)*\n\nFormulá la pregunta principal. Podés escribirla o enviar un audio.",
            parse_mode="Markdown"
        )

    elif paso == "pregunta1":
        if len(texto) < 5 or es_negacion(texto):
            await update.message.reply_text("La pregunta es muy corta o no es válida. Formulá una pregunta concreta.")
            return RECOLECTANDO_TESTIMONIOS
        actual["pregunta1"] = texto
        context.user_data["testimonio_paso"] = "pregunta2"
        await update.message.reply_text("❔ *Segunda pregunta (opcional — enviá '-' para saltar):*", parse_mode="Markdown")

    elif paso == "pregunta2":
        actual["pregunta2"] = "" if (texto == "-" or es_negacion(texto)) else texto
        context.user_data["testimonio_paso"] = "respuesta"
        await update.message.reply_text("💬 *Respuesta u opinión*\n\nMínimo 15 caracteres si es texto.", parse_mode="Markdown")

    elif paso == "respuesta":
        if len(texto) < 15:
            await update.message.reply_text("La respuesta es muy corta. Desarrollá más o enviá un audio.")
            return RECOLECTANDO_TESTIMONIOS
        actual["respuesta"] = texto
        testimonios = context.user_data.get("testimonios", [])
        testimonios.append({
            "nombre": actual.get("nombre"), "organizacion": actual.get("organizacion", ""),
            "nacionalidad": actual.get("nacionalidad"), "edad": actual.get("edad", ""),
            "pregunta1": actual.get("pregunta1"), "pregunta2": actual.get("pregunta2", ""),
            "respuesta": actual.get("respuesta"),
        })
        context.user_data["testimonios"] = testimonios
        context.user_data["testimonio_actual"] = {}
        cant = len(testimonios)
        if cant < 2:
            context.user_data["testimonio_paso"] = "nombre"
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado.\n\n✏️ *Nombre o alias del siguiente:*",
                parse_mode="Markdown"
            )
        elif cant == 2:
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado. Ya tenés los 2 mínimos.\n\n¿Querés agregar un tercero?",
                reply_markup=teclado_testimonio_opciones()
            )
        elif cant >= 3:
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado. Alcanzaste el máximo.\n\n¿Las personas dieron consentimiento para ser fotografiadas?",
                reply_markup=teclado_consentimiento_fotos()
            )

    context.user_data["testimonio_actual"] = actual
    return RECOLECTANDO_TESTIMONIOS


async def handle_testimonio_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    paso = context.user_data.get("testimonio_paso", "")
    if paso not in ["pregunta1", "pregunta2", "respuesta"]:
        await update.message.reply_text("En este momento no se espera un audio. Respondé con texto.")
        return RECOLECTANDO_TESTIMONIOS
    voice = update.message.voice
    audio = update.message.audio
    if voice:
        file_id, duration = voice.file_id, voice.duration
    elif audio:
        file_id, duration = audio.file_id, audio.duration
    else:
        await update.message.reply_text("No se detectó un audio válido.")
        return RECOLECTANDO_TESTIMONIOS
    if duration and duration > 240:
        await update.message.reply_text("El audio es demasiado largo (máx 4 minutos).")
        return RECOLECTANDO_TESTIMONIOS
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    texto = await transcribir_audio_groq(file_id, context.bot)
    if transcripcion_fallida(texto):
        await update.message.reply_text(
            "⚠️ No pude transcribir ese audio. Probá grabarlo de nuevo o escribí la respuesta en texto."
        )
        return RECOLECTANDO_TESTIMONIOS
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await handle_testimonio_texto(update, context, texto)


async def handle_testimonio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "testimonio:agregar":
        context.user_data["testimonio_actual"] = {}
        context.user_data["testimonio_paso"] = "nombre"
        await query.message.reply_text("✏️ *Nombre o alias del tercer testimonio:*", parse_mode="Markdown")
        return RECOLECTANDO_TESTIMONIOS

    elif data == "testimonio:finalizar":
        await query.message.reply_text(
            "¿Las personas dieron consentimiento para ser fotografiadas?",
            reply_markup=teclado_consentimiento_fotos()
        )
        return RECOLECTANDO_TESTIMONIOS

    elif data == "fotos:si":
        context.user_data["consentimiento_fotos"] = True
        context.user_data["esperando_fotos_testimonios"] = True
        context.user_data.pop("testimonio_paso", None)
        context.user_data.pop("testimonio_actual", None)
        num = len(context.user_data.get("testimonios", []))
        await query.message.reply_text(
            f"📸 Enviame una foto de cada una de las {num} personas que testimoniaron.\n"
            f"Cuando termines, escribí /listo.\n\n_Si alguien no quiere ser fotografiado, simplemente no envíes su foto._",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS

    elif data == "fotos:no":
        context.user_data["consentimiento_fotos"] = False
        context.user_data["esperando_ampliacion"] = True
        context.user_data.pop("testimonio_paso", None)
        context.user_data.pop("testimonio_actual", None)
        await query.message.reply_text(
            "📝 *Información adicional*\n\n¿Hay algún dato relevante que quieras agregar? Podés escribirlo ahora, o enviá '-' para saltar.",
            parse_mode="Markdown"
        )
        return RECOLECTANDO_TESTIMONIOS

    return RECOLECTANDO_TESTIMONIOS


async def mostrar_resumen(message, context) -> int:
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    testimonios = context.user_data.get("testimonios") if genero_key == "reportaje" else None
    resumen = construir_resumen(context.user_data["respuestas"], flujo, genero_key, testimonios)
    await message.reply_text(
        resumen + "\n\n_Revisá tus respuestas antes de continuar._",
        parse_mode="Markdown", reply_markup=teclado_resumen()
    )
    return REVISION_RESUMEN


async def handle_revision_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    accion = query.data.split(":")[1]

    if accion == "confirmar":
        genero_key = context.user_data["genero"]
        fotos_min = GENEROS[genero_key]["fotos_min"]
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"✅ *Reporte confirmado.*\n\n"
            f"📸 Enviame *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* del hecho.\n"
            f"🎥 Opcionalmente, un video de hasta 30 segundos.\n\nCuando termines, escribí */generar*",
            parse_mode="Markdown"
        )
        context.user_data["esperando_fotos_testimonios"] = False
        return ESPERANDO_FOTOS

    elif accion == "editar":
        flujo = obtener_flujo(context.user_data["genero"])
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "✏️ *¿Qué respuesta querés editar?*\n\n_Tocá el número:_",
            parse_mode="Markdown", reply_markup=teclado_numeros(flujo)
        )
        return REVISION_RESUMEN

    elif accion == "volver":
        return await mostrar_resumen(query.message, context)

    return REVISION_RESUMEN


async def handle_seleccion_editar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    flujo = obtener_flujo(context.user_data["genero"])
    pregunta = flujo["preguntas"][idx]
    context.user_data["editando_idx"] = idx
    context.user_data["editando_clave"] = pregunta["clave"]
    respuesta_actual = context.user_data["respuestas"].get(pregunta["clave"], "_Sin respuesta_")
    if len(respuesta_actual) > 300:
        respuesta_actual = respuesta_actual[:300] + "..."
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✏️ *Editando pregunta {idx+1}*\n\n{pregunta['texto']}\n\n"
        f"_Respuesta actual:_\n{respuesta_actual}\n\n_Escribí la nueva respuesta o grabá un audio:_",
        parse_mode="Markdown",
        reply_markup=construir_teclado_miniapp(pregunta["texto"], pregunta["clave"])
    )
    return EDITANDO_RESPUESTA


async def handle_edicion_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await procesar_edicion(update, context, update.message.text)


async def handle_edicion_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
    if transcripcion_fallida(texto):
        await update.message.reply_text(
            "⚠️ No pude transcribir ese audio. Probá grabarlo de nuevo o escribí la respuesta en texto."
        )
        return EDITANDO_RESPUESTA
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await procesar_edicion(update, context, texto)


async def procesar_edicion(update, context, texto_nuevo: str) -> int:
    if len(texto_nuevo.strip()) < 3:
        await update.message.reply_text("📝 La respuesta es muy corta.")
        return EDITANDO_RESPUESTA
    clave = context.user_data.get("editando_clave")
    idx = context.user_data.get("editando_idx")
    if clave:
        context.user_data["respuestas"][clave] = texto_nuevo.strip()
        await update.message.reply_text(f"✅ *Respuesta {idx+1} actualizada.*", parse_mode="Markdown")
    context.user_data.pop("editando_idx", None)
    context.user_data.pop("editando_clave", None)
    return await mostrar_resumen(update.message, context)


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    foto = update.message.photo[-1]
    if context.user_data.get("esperando_fotos_testimonios"):
        if "fotos_testimonios" not in context.user_data:
            context.user_data["fotos_testimonios"] = []
        context.user_data["fotos_testimonios"].append(foto.file_id)
        recibidas = len(context.user_data["fotos_testimonios"])
        necesarias = len(context.user_data.get("testimonios", []))
        if recibidas < necesarias:
            await update.message.reply_text(f"📸 Foto {recibidas} de {necesarias} recibida. Enviá la siguiente o /listo.")
            return ESPERANDO_FOTOS
        else:
            await update.message.reply_text(f"✅ Recibidas las {necesarias} fotos de testimonios.")
            context.user_data["esperando_fotos_testimonios"] = False
            context.user_data["esperando_ampliacion"] = True
            await update.message.reply_text(
                "📝 *Información adicional*\n\n¿Hay algún dato relevante que quieras agregar? Podés escribirlo, o enviá '-' para saltar.",
                parse_mode="Markdown"
            )
            return RECOLECTANDO_TESTIMONIOS
    else:
        if "foto_ids" not in context.user_data:
            context.user_data["foto_ids"] = []
        context.user_data["foto_ids"].append(foto.file_id)
        n = len(context.user_data["foto_ids"])
        context.user_data["fotos"] = n
        genero_key = context.user_data["genero"]
        fotos_min = GENEROS[genero_key]["fotos_min"]
        if n < fotos_min:
            msg = f"📷 Foto {n} recibida ✓\n_Falta {fotos_min-n} foto{'s' if fotos_min-n > 1 else ''} más._"
        elif n == fotos_min:
            msg = f"📷 Foto {n} recibida ✓\n_Mínimo cumplido. Podés enviar más, un video, o /generar_"
        else:
            msg = f"📷 Foto {n} recibida ✓\n_Más fotos, video, o /generar_"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return ESPERANDO_FOTOS


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    duracion = video.duration if video.duration else 0
    if duracion > 30:
        await update.message.reply_text(
            f"⚠️ *Video rechazado* — dura {duracion}s. Máximo 30 segundos.",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS
    context.user_data["video_id"] = video.file_id
    context.user_data["video_duracion"] = duracion
    fotos_min = GENEROS[context.user_data["genero"]]["fotos_min"]
    fotos = context.user_data.get("fotos", 0)
    msg = (
        f"🎥 Video recibido ✓ ({duracion}s)\n"
        f"_{'Todavía necesitás ' + str(fotos_min-fotos) + ' foto(s) más antes de /generar' if fotos < fotos_min else 'Listo para /generar'}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return ESPERANDO_FOTOS


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    genero_key = context.user_data.get("genero")
    if not genero_key:
        await update.message.reply_text("⚠️ No hay género seleccionado. Escribí /reiniciar para empezar.")
        return ConversationHandler.END

    fotos_hecho_ids = context.user_data.get("foto_ids", [])
    fotos_min = GENEROS[genero_key]["fotos_min"]
    if len(fotos_hecho_ids) < fotos_min:
        await update.message.reply_text(
            f"⚠️ Necesitás *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* del hecho. "
            f"Enviaste {len(fotos_hecho_ids)}.",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS

    if genero_key == "reportaje":
        testimonios = context.user_data.get("testimonios", [])
        if len(testimonios) < 2:
            await update.message.reply_text("⚠️ Necesitás completar al menos 2 testimonios. Escribí /reiniciar si querés empezar de nuevo.")
            return ConversationHandler.END
        fotos_test_ids = context.user_data.get("fotos_testimonios", [])
    else:
        testimonios = None
        fotos_test_ids = []

    await update.message.reply_text(
        "⏳ *Generando borrador...*\n_Esto puede tardar unos segundos._",
        parse_mode="Markdown"
    )

    nombre = context.user_data.get("nombre", "corresponsal")
    genero_nombre = GENEROS[genero_key]["nombre"]
    fotos_hecho_bytes = await descargar_fotos(fotos_hecho_ids, context.bot)
    fotos_test_bytes = await descargar_fotos(fotos_test_ids, context.bot) if fotos_test_ids else []
    todas_fotos = fotos_hecho_bytes + fotos_test_bytes
    ampliacion = context.user_data.get("ampliacion_info", "")

    borrador, exito = await asyncio.to_thread(
        generar_borrador,
        context.user_data.get("respuestas", {}), nombre, genero_key,
        len(todas_fotos), testimonios, ampliacion
    )

    if not exito or not borrador:
        await update.message.reply_text(MSG_ERROR_GENERAR, parse_mode="Markdown")
        return ConversationHandler.END

    titulo = extraer_titulo(borrador)
    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])

    await update.message.reply_text("📧 _Enviando al equipo editorial..._", parse_mode="Markdown")
    video_bytes = None
    video_id = context.user_data.get("video_id")
    if video_id:
        video_bytes = await descargar_video(video_id, context.bot)

    enviado = enviar_con_resend(borrador, nombre, titulo, genero_nombre, todas_fotos, video_bytes)
    info = f"📎 Fotos: {len(todas_fotos)}"
    if video_bytes:
        info += f"\n🎥 Video: {context.user_data.get('video_duracion', 0)}s"

    if enviado:
        await update.message.reply_text(
            f"✅ *Borrador enviado.*\n📰 {genero_nombre}\n👤 {nombre}\n{info}\n\n_Usá /reiniciar para un nuevo reporte_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"✅ *Borrador generado* (no se pudo enviar el email automáticamente).\n"
            f"📰 {genero_nombre}\n👤 {nombre}\n{info}\n\n"
            f"El texto del borrador está más arriba. _Usá /reiniciar para un nuevo reporte._",
            parse_mode="Markdown"
        )
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado. /comenzar para empezar de nuevo.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# MAIN — webhook + auto-pinger
# ═══════════════════════════════════════════════════════════════

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("comenzar", comenzar),
            CommandHandler("start", comenzar),       # respaldo silencioso del protocolo de Telegram
            CommandHandler("reiniciar", reiniciar),
        ],
        states={
            AUTENTICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autenticacion)],
            IDENTIFICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_identificacion)],
            SELECCION_GENERO: [CallbackQueryHandler(handle_seleccion_genero, pattern=r"^genero:")],
            RESPONDIENDO_PREGUNTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_respuesta_texto),
                MessageHandler(filters.VOICE | filters.AUDIO, handle_respuesta_audio),
                MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_respuesta_miniapp),
            ],
            REVISION_RESUMEN: [
                CallbackQueryHandler(handle_revision_resumen, pattern=r"^resumen:"),
                CallbackQueryHandler(handle_seleccion_editar, pattern=r"^editar:"),
            ],
            EDITANDO_RESPUESTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edicion_texto),
                MessageHandler(filters.VOICE | filters.AUDIO, handle_edicion_audio),
            ],
            ESPERANDO_FOTOS: [
                MessageHandler(filters.PHOTO, handle_foto),
                MessageHandler(filters.VIDEO, handle_video),
                CommandHandler("generar", cmd_generar),
                CommandHandler("listo", cmd_generar),
            ],
            RECOLECTANDO_TESTIMONIOS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_testimonio_texto),
                MessageHandler(filters.VOICE | filters.AUDIO, handle_testimonio_audio),
                CallbackQueryHandler(handle_testimonio_callback, pattern=r"^(testimonio:|fotos:)"),
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            CommandHandler("reiniciar", reiniciar),
            CommandHandler("comenzar", comenzar),
            CommandHandler("generar", cmd_generar),
        ],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("ayuda", ayuda))

    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "chatbot-rl", "modelo_gemini": GEMINI_MODEL})

    # Deduplicación: Telegram reenvía updates si el webhook no responde rápido.
    # Guardamos los últimos update_id procesados para descartar duplicados.
    updates_procesados = set()
    MAX_UPDATES_GUARDADOS = 1000

    async def procesar_update_background(update: Update):
        try:
            await application.process_update(update)
        except Exception as e:
            logger.error(f"Error procesando update en background: {e}")

    async def webhook_endpoint(request: Request) -> PlainTextResponse:
        try:
            body = await request.json()
            update = Update.de_json(body, application.bot)

            # Deduplicar: si Telegram reenvió este update, ignorarlo
            if update.update_id in updates_procesados:
                logger.warning(f"Update duplicado ignorado: {update.update_id}")
                return PlainTextResponse("", status_code=200)
            updates_procesados.add(update.update_id)
            if len(updates_procesados) > MAX_UPDATES_GUARDADOS:
                # Evitar crecimiento indefinido: limpiar los más viejos
                exceso = len(updates_procesados) - MAX_UPDATES_GUARDADOS
                for uid in sorted(updates_procesados)[:exceso]:
                    updates_procesados.discard(uid)

            # Responder 200 de inmediato y procesar en segundo plano,
            # para que Telegram no reenvíe el update por timeout.
            asyncio.create_task(procesar_update_background(update))
            return PlainTextResponse("", status_code=200)
        except Exception as e:
            logger.error(f"Error en webhook: {e}")
            return PlainTextResponse("", status_code=500)

    async def set_webhook():
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if not render_url:
            logger.error("RENDER_EXTERNAL_URL no configurada.")
            return
        webhook_url = f"{render_url}/webhook/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        token_oculto = TOKEN[:10] + "***" + TOKEN[-4:] if len(TOKEN) > 14 else "***"
        logger.info(f"Webhook configurado en {render_url}/webhook/{token_oculto}")
        logger.info(f"Modelo Gemini activo: {GEMINI_MODEL}")

    def start_self_pinger(port: int, interval_seconds: int = 240):
        def pinger():
            url = f"http://localhost:{port}/health"
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=10) as response:
                        if response.status != 200:
                            logger.warning(f"Self-ping respuesta inesperada: {response.status}")
                except Exception as e:
                    logger.error(f"Error en self-ping: {e}")
                time.sleep(interval_seconds)
        thread = threading.Thread(target=pinger, daemon=True)
        thread.start()
        logger.info(f"Auto-pinger iniciado (cada {interval_seconds}s en puerto {port})")

    async def start_app():
        await application.initialize()
        await set_webhook()
        webhook_path = f"/webhook/{TOKEN}"
        starlette_app = Starlette(routes=[
            Route("/health", health_check, methods=["GET"]),
            Route(webhook_path, webhook_endpoint, methods=["POST"]),
        ])
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    start_self_pinger(PORT, interval_seconds=240)
    asyncio.run(start_app())


if __name__ == "__main__":
    main()
