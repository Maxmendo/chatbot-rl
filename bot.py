"""
CHATBOT REFUGIO LATINOAMERICANO — Webhook (sin polling)
Versión con testimonios para reportaje, audios, fotos opcionales y prompt anti-alucinación.
"""

import os
import base64
import logging
import json
import requests
from urllib.parse import urlparse
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
import asyncio
import time
import threading
import urllib.request

# ========== CONFIGURACIÓN DE LOGGING ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== VARIABLES DE ENTORNO ==========
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Falta TELEGRAM_BOT_TOKEN")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    logger.error("RENDER_EXTERNAL_URL no configurada. El webhook no funcionará.")
PORT = int(os.getenv("PORT", 8080))

# ========== ESTADOS DEL CONVERSATIONHANDLER ==========
(AUTENTICACION, IDENTIFICACION, SELECCION_GENERO, INICIO_FLUJO,
 RESPONDIENDO_PREGUNTA, REVISION_RESUMEN, EDITANDO_RESPUESTA,
 ESPERANDO_FOTOS, CIERRE_ETICO, RECOLECTANDO_TESTIMONIOS) = range(10)

# ========== GÉNEROS PERIODÍSTICOS ==========
GENEROS = {
    "historia_vida": {
        "nombre": "Historia de vida",
        "descripcion": "Testimonio biográfico de una persona migrante",
        "fotos_min": 3,
        "estructura": "cronica",
    },
    "denuncia": {
        "nombre": "Denuncia",
        "descripcion": "Situación de vulneración de derechos",
        "fotos_min": 2,
        "estructura": "analisis",
    },
    "evento": {
        "nombre": "Evento",
        "descripcion": "Algo que pasó o va a pasar",
        "fotos_min": 2,
        "estructura": "noticia",
    },
    "agenda": {
        "nombre": "Agenda / Servicio",
        "descripcion": "Información útil para la comunidad",
        "fotos_min": 1,
        "estructura": "servicio",
    },
    "explicador": {
        "nombre": "Explicador",
        "descripcion": "Pedagogía sobre temas complejos",
        "fotos_min": 1,
        "estructura": "explicador",
    },
    "cultura": {
        "nombre": "Cultura",
        "descripcion": "Identidad, celebración, intercambio",
        "fotos_min": 2,
        "estructura": "cronica",
    },
    "reportaje": {
        "nombre": "Reportaje",
        "descripcion": "Análisis profundo de un fenómeno",
        "fotos_min": 0,
        "estructura": "reportaje",
    },
}

# ========== FLUJOS CONVERSACIONALES ==========
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
        {"clave": "origen", "texto": "🌍 *1. Origen*\n\n¿De dónde viene? ¿Cómo era su vida antes de migrar? ¿En qué año llegó al país y cuántos años tenía cuando dejó su lugar de origen? ¿Vino sola, o con familia, pareja y/o amigos?"},
        {"clave": "motivos", "texto": "🔄 *2. Motivos de movilidad*\n\n¿Qué razones le llevaron a emigrar? ¿Qué significó ese momento? ¿Cómo planificó su partida?"},
        {"clave": "transito", "texto": "🛤️ *3. Tránsito*\n\n¿Cómo fue el viaje? ¿Qué experiencias, obstáculos o emociones marcaron ese trayecto?"},
        {"clave": "llegada", "texto": "📍 *4. Llegada*\n\n¿Cuáles fueron sus primeras impresiones al llegar? ¿Qué situaciones o desafíos recuerda de esos primeros días? ¿Contaba con contactos previos con otros miembros de su comunidad?"},
        {"clave": "laboral", "texto": "💼 *5. Inserción laboral*\n\n¿Cómo fue su inserción laboral en el país? ¿Actualmente está trabajando? ¿Trabaja por su cuenta o en relación de dependencia?"},
        {"clave": "presente", "texto": "🏡 *6. Presente*\n\n¿Cómo es su vida hoy? ¿A qué se dedica, qué vínculos construyó? ¿Se relaciona con personas de su comunidad acá? ¿Qué cosas echa de menos de su lugar de origen? ¿Cuáles son sus proyectos aquí?"},
        {"clave": "horizonte", "texto": "🔮 *7. Horizonte*\n\n¿Cómo vive hoy su identidad y sentido de pertenencia? ¿Piensa en regresar a su tierra de origen o proyecta su futuro acá?"},
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
        {"clave": "naturaleza", "texto": "🔍 *1. Naturaleza del problema*\n\n¿Qué tipo de situación se vive? (vulneración de derechos, discriminación, obstáculos para acceder a servicios, abuso institucional, violencia, trámite irregular). ¿Es un hecho puntual o una situación sostenida? ¿Afecta a una o muchas personas en situación similar?"},
        {"clave": "personas_afectadas", "texto": "👥 *2. Personas afectadas*\n\n¿Quiénes son las personas afectadas? ¿Se trata de una persona, familia, comunidad? ¿De qué país o comunidad provienen? ¿Qué las llevó a dejar su lugar de origen? ¿Hace cuánto viven en el país donde ocurre la situación? ¿Cuál es su situación migratoria actual (con residencia, en trámite, solicitantes de refugio, situación irregular)? ¿Hay dimensiones específicas (niñez, personas mayores, embarazadas, mujeres víctimas de violencia de género, personas LGBTIQ+, personas con discapacidad)?"},
        {"clave": "identificacion_afectadas", "texto": "🔐 *3. Cómo quieren ser identificadas*\n\n¿Cómo les gustaría a las personas afectadas ser identificadas en la nota? ¿Con sus nombres completos, iniciales o seudónimo? _Si están en situación de solicitud de refugio o irregularidad, siempre es mejor proteger su identidad._"},
        {"clave": "responsables", "texto": "🏛️ *4. Responsables*\n\n¿Quiénes son los responsables? (autoridad estatal, institución pública, empresa, particular). ¿Nombre, cargo, dependencia concreta? ¿Existe un marco normativo que se está incumpliendo?"},
        {"clave": "lugar_momento", "texto": "📍 *5. Lugar y momento*\n\n¿Dónde ocurre? (país, provincia, ciudad, barrio, dirección). ¿Cuándo? ¿Hecho puntual o sostenido en el tiempo?"},
        {"clave": "gestiones", "texto": "📋 *6. Gestiones previas*\n\n¿Las personas afectadas ya hicieron denuncia formal? ¿Dónde? ¿Número de expediente o acta? ¿Contactaron algún organismo, ONG, consulado, defensoría? ¿Qué respuesta recibieron?"},
        {"clave": "testimonios", "texto": "📢 *7. Testimonios y pruebas*\n\n¿Hay otras personas que hayan vivido o visto lo mismo y puedan testimoniar? ¿Documentos, capturas, audios, comunicaciones oficiales, pruebas materiales? ¿Alguna fuente experta (organización, abogada, académica, referente) que pueda aportar contexto?"},
        {"clave": "impacto", "texto": "💥 *8. Impacto personal y comunitario*\n\n¿Cómo afecta la vida cotidiana de las personas? _(sin enfocar solo en el sufrimiento — también en cómo resisten, se organizan, se defienden)_ ¿Qué consecuencias tiene en la comunidad más amplia? ¿Se están organizando para responder?"},
        {"clave": "expectativas", "texto": "🎯 *9. Qué esperan*\n\n¿Qué esperan lograr al visibilizar esta situación? ¿Hay demanda específica hacia alguna autoridad?"},
        {"clave": "contraste", "texto": "⚖️ *10. Contraste editorial*\n\n¿Refugio debería buscar la palabra de la institución, funcionario o empresa señalada antes de publicar? ¿O se publica tal como llega y esperamos una eventual respuesta?"},
    ],
}

FLUJO_GENERICO_7W = {
    "entrada": "📰 *{nombre}*\n\nContame en tus propias palabras de qué se trata.",
    "preguntas": [
        {"clave": "que", "texto": "📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central."},
        {"clave": "quien", "texto": "👤 *¿QUIÉN/ES?*\n\nPersonas, organizaciones o comunidades involucradas."},
        {"clave": "cuando", "texto": "🕐 *¿CUÁNDO?*\n\nFecha, hora, contexto temporal."},
        {"clave": "donde", "texto": "📍 *¿DÓNDE?*\n\nPaís, ciudad, barrio, dirección."},
        {"clave": "como", "texto": "🔍 *¿CÓMO?*\n\nSecuencia de eventos, circunstancias."},
        {"clave": "por_que", "texto": "💡 *¿POR QUÉ?*\n\nCausas, contexto, antecedentes."},
        {"clave": "impacto", "texto": "🎯 *¿IMPACTO?*\n\nConsecuencias para la comunidad migrante."},
    ],
}

def obtener_flujo(genero_key: str) -> dict:
    if genero_key == "historia_vida":
        return FLUJO_HISTORIA_VIDA
    elif genero_key == "denuncia":
        return FLUJO_DENUNCIA
    else:
        flujo = dict(FLUJO_GENERICO_7W)
        flujo["entrada"] = FLUJO_GENERICO_7W["entrada"].format(nombre=GENEROS[genero_key]["nombre"])
        return flujo

# ========== PROMPTS EDITORIALES ==========
PROMPT_BASE = """Sos editor/a periodístico de Refugio Latinoamericano, medio digital especializado en periodismo de migraciones con perspectiva de derechos humanos e interculturalidad.

CRITERIOS EDITORIALES OBLIGATORIOS (Manual de Estilo RL + ACNUR + Mallette):

LENGUAJE:
- Nunca "ilegal", "clandestino", "indocumentado" — preferir "persona en situación migratoria irregular" solo si es estrictamente necesario
- Nunca "oleada", "avalancha", "aluvión", "asalto", "crisis", "invasión", "catástrofe", "fenómeno"
- Nunca "personas vulnerables" — usar "personas en situación de vulnerabilidad"
- Nunca masculino genérico — usar "personas refugiadas", "personas migrantes", "la población X", "la comunidad X"
- Diferenciar siempre migrante / refugiada / solicitante de asilo (la distinción jurídica protege derechos)
- Anteponer "persona" siempre: persona migrante, persona refugiada, persona solicitante

ENFOQUE:
- Persona migrante como sujeto de derechos y agente activo — no víctima, no héroe
- Responsabilizar a Estados e instituciones como titulares de obligaciones
- Contextualizar históricamente — las migraciones no son coyuntura sino proceso estructural
- Evitar lenguaje victimizante que desempodera la agencia

REDACCIÓN (Mallette):
- Voz activa, oraciones cortas (promedio menor a 18 palabras)
- Sustantivos y verbos con significado claro, parco en adjetivos
- Patrón oración declarativa simple: sujeto-verbo-predicado
- Evitar jerga, clichés, fórmulas burocráticas
- Detalles concretos, reveladores — transportar al lector a la escena
- Precisión, brevedad y claridad"""

PROMPT_HISTORIA_VIDA = PROMPT_BASE + """

ESTRUCTURA NARRATIVA — CRÓNICA (para historia de vida):
1. TÍTULO: máximo 12 palabras, sin punto final — debe explicitar eje de movilidad humana desde perspectiva DDHH e interculturalidad
2. BAJADA: 2-3 oraciones que complementan el título con el mismo eje editorial
3. APERTURA: premisa central — qué hace única a esta persona, qué aspecto de su historia la audiencia debe comprender
4. DESARROLLO CRONOLÓGICO: origen → motivos → tránsito → llegada → inserción laboral → presente → horizonte (con anécdotas, citas directas, descripciones sensoriales)
5. CITA DIRECTA DESTACADA: frase textual que sintetice la voz de la persona entrevistada
6. CIERRE: horizonte abierto que no cierre la historia ni la resuelva forzadamente
7. VERIFICACIÓN PENDIENTE: [VERIFICAR] dato 1, [VERIFICAR] dato 2
8. ETIQUETAS SUGERIDAS: 3-5 etiquetas
9. NOTAS PARA EL EDITOR/A: observaciones sobre consentimientos, protección de identidad, contraste necesario"""

PROMPT_DENUNCIA = PROMPT_BASE + """

ESTRUCTURA NARRATIVA — ANÁLISIS (para denuncia):
1. TÍTULO: máximo 12 palabras, sin punto final — debe explicitar eje de movilidad humana desde perspectiva DDHH
2. BAJADA: 2-3 oraciones que complementan el título con el eje editorial
3. APERTURA: descripción clara del problema y de las personas afectadas como sujetos de derechos
4. DESARROLLO ANALÍTICO:
   a) Descripción del problema (qué, dónde, cuándo, magnitud)
   b) Participantes y responsables (incluyendo marco normativo incumplido)
   c) Gestiones previas y respuestas recibidas
   d) Impacto personal y comunitario — mostrando también agencia y organización
   e) Repercusión y proyección
5. CITAS DIRECTAS: al menos una de las personas afectadas y si hay, una de fuente experta
6. ELEMENTOS DE EQUILIBRIO: si el corresponsal indicó que se busque contraste, señalarlo explícitamente
7. CIERRE: qué esperan las personas afectadas y qué debería ocurrir
8. VERIFICACIÓN PENDIENTE: [VERIFICAR] dato 1, [VERIFICAR] dato 2
9. ETIQUETAS SUGERIDAS: 3-5 etiquetas
10. NOTAS PARA EL EDITOR/A: protección de identidades (por defecto si hay solicitantes de refugio o situación irregular), riesgo de represalia, contraste pendiente, fuentes oficiales a consultar"""

PROMPT_GENERICO = PROMPT_BASE + """

ESTRUCTURA NARRATIVA:
1. TÍTULO: máximo 12 palabras, sin punto final
2. BAJADA: 2-3 oraciones
3. DESARROLLO: 3-5 párrafos siguiendo las 7W
4. CITA DIRECTA si hay testimonio
5. CIERRE
6. VERIFICACIÓN PENDIENTE
7. ETIQUETAS SUGERIDAS
8. NOTAS PARA EL EDITOR/A"""

def obtener_prompt(genero_key: str) -> str:
    if genero_key == "historia_vida":
        return PROMPT_HISTORIA_VIDA
    elif genero_key == "denuncia":
        return PROMPT_DENUNCIA
    else:
        return PROMPT_GENERICO

# ========== FUNCIÓN CENTRALIZADA GROQ ==========
def llamar_groq(messages: list, max_tokens: int = 400, temperature: float = 0.3,
                response_format: dict = None) -> str | None:
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
            json=payload,
            timeout=90
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        logger.error(f"Error Groq: {data.get('error', {}).get('message', 'desconocido')}")
        return None
    except Exception as e:
        logger.error(f"Error llamando a Groq: {e}")
        return None

# ========== MOTOR DE REPREGUNTAS ==========
PROMPT_ANALISTA = """Sos un editor/a periodístico analizando una respuesta de un corresponsal de campo.

Evaluá la respuesta siguiendo estos tres criterios:

1. PROFUNDIDAD: ¿Tiene menos de 15-20 palabras significativas o es muy vaga?
2. INCONSISTENCIA TEMPORAL: ¿Aparecen referencias contradictorias (ej: "ayer" y "el mes pasado" juntos)?
3. AMBIGÜEDAD: ¿Falta información clave que la pregunta requería?

IMPORTANTE: No repreguntés si la respuesta es clara, rica y completa, aunque sea breve.

Respondé SOLO con un JSON válido con esta estructura exacta:
{
  "necesita_repregunta": true/false,
  "tipo": "profundidad" | "inconsistencia" | "ambiguedad" | null,
  "repregunta": "texto de la repregunta con eco empático" | null
}"""

def analizar_respuesta_con_groq(pregunta: str, respuesta: str) -> dict:
    resultado = llamar_groq(
        messages=[
            {"role": "system", "content": PROMPT_ANALISTA},
            {"role": "user", "content": f"PREGUNTA:\n{pregunta}\n\nRESPUESTA DEL CORRESPONSAL:\n{respuesta}\n\nAnalizá y respondé SOLO con el JSON."}
        ],
        max_tokens=400,
        temperature=0.3,
        response_format={"type": "json_object"}
    )
    if resultado:
        try:
            return json.loads(resultado)
        except Exception as e:
            logger.error(f"Error parseando JSON repreguntas: {e}")
    return {"necesita_repregunta": False, "tipo": None, "repregunta": None}

# ========== TRANSCRIPCIÓN DE AUDIO ==========
async def transcribir_audio_groq(file_id: str, bot) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "[Error: falta GROQ_API_KEY]"
    try:
        file = await bot.get_file(file_id)
        audio_bytes = await file.download_as_bytearray()
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.ogg", bytes(audio_bytes), "audio/ogg")},
            data={
                "model": "whisper-large-v3",
                "language": "es",
                "prompt": "Entrevista periodística sobre migraciones en América Latina."
            },
            timeout=60
        )
        data = response.json()
        if "text" in data:
            return f"{data['text'].strip()} [transcripto de audio]"
        return "[No se pudo transcribir. Respondé en texto.]"
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[Error al transcribir. Respondé en texto.]"

# ========== GENERACIÓN DEL BORRADOR (versión mejorada) ==========
def generar_borrador(respuestas: dict, nombre: str, genero_key: str, fotos: int,
                     testimonios: list = None, ampliacion: str = "") -> str:
    prompt_sistema = obtener_prompt(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]
    datos = "\n".join(f"{k.upper()}: {v}" for k, v in respuestas.items())
    
    texto_testimonios = ""
    if testimonios:
        texto_testimonios = "\n\n=== TESTIMONIOS RECOLECTADOS ===\n"
        for i, t in enumerate(testimonios, 1):
            texto_testimonios += f"Testimonio {i}:\n"
            texto_testimonios += f"Nombre/Alias: {t.get('nombre', '')}\n"
            if t.get('organizacion'):
                texto_testimonios += f"Organización: {t['organizacion']}\n"
            texto_testimonios += f"Nacionalidad: {t.get('nacionalidad', '')}\n"
            if t.get('edad'):
                texto_testimonios += f"Edad: {t['edad']}\n"
            texto_testimonios += f"Relato: {t.get('texto', '')}\n\n"
    if ampliacion:
        texto_testimonios += f"\n=== AMPLIACIÓN DEL CORRESPONSAL ===\n{ampliacion}\n"
    
    instruccion_estricta = (
        "\n\nINSTRUCCIÓN ABSOLUTAMENTE OBLIGATORIA: "
        "Debes redactar la nota periodística utilizando ÚNICA Y EXCLUSIVAMENTE la información proporcionada en este mensaje. "
        "NO inventes datos, nombres, testimonios, fechas, lugares ni estadísticas que no aparezcan explícitamente en el reporte del corresponsal o en los testimonios. "
        "Si falta algún dato, simplemente omítelo o indica que no fue proporcionado. "
        "NO uses tu conocimiento previo. Los testimonios deben ser literalmente extraídos de la sección TESTIMONIOS RECOLECTADOS. "
        "La nota debe ser objetiva y respetar el manual de estilo de Refugio Latinoamericano."
    )
    
    resultado = llamar_groq(
        messages=[
            {"role": "system", "content": prompt_sistema + instruccion_estricta},
            {"role": "user", "content": (
                f"GÉNERO: {genero_nombre}\n\n"
                f"REPORTE DEL CORRESPONSAL:\n{datos}\n"
                f"{texto_testimonios}\n"
                f"Corresponsal: {nombre}\n"
                f"Fotos adjuntas: {fotos}\n\n"
                f"Redactá el borrador completo respetando TODOS los criterios editoriales. No agregues información externa."
            )}
        ],
        max_tokens=3000,
        temperature=0.7
    )
    return resultado if resultado else "❌ Error al generar el borrador."

# ========== RESUMEN EDITABLE ==========
def construir_resumen(respuestas: dict, flujo: dict) -> str:
    preguntas = flujo["preguntas"]
    lineas = ["📋 *Resumen de tu reporte*\n"]
    for i, pregunta in enumerate(preguntas):
        clave = pregunta["clave"]
        titulo = pregunta["texto"].split("\n")[0].replace("*", "").strip()
        respuesta = respuestas.get(clave, "_Sin respuesta_")
        if len(respuesta) > 200:
            respuesta = respuesta[:200] + "..."
        lineas.append(f"*{i+1}. {titulo}*\n{respuesta}")
    return "\n\n".join(lineas)

def teclado_resumen() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar una respuesta", callback_data="resumen:editar")],
        [InlineKeyboardButton("✅ Todo correcto, continuar", callback_data="resumen:confirmar")],
    ])

def teclado_numeros(flujo: dict) -> InlineKeyboardMarkup:
    preguntas = flujo["preguntas"]
    total = len(preguntas)
    botones = []
    fila = []
    for i in range(total):
        fila.append(InlineKeyboardButton(str(i + 1), callback_data=f"editar:{i}"))
        if len(fila) == 5:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    botones.append([InlineKeyboardButton("↩️ Volver al resumen", callback_data="resumen:volver")])
    return InlineKeyboardMarkup(botones)

# ========== MINI APP + MULTIMEDIA + EMAIL ==========
def get_mini_app_url(pregunta_texto: str, clave: str) -> str:
    base_url = os.getenv("MINI_APP_URL", "")
    if not base_url:
        return ""
    import urllib.parse
    texto_limpio = pregunta_texto.replace("*", "").replace("_", "")[:200]
    params = urllib.parse.urlencode({"label": clave.upper(), "texto": texto_limpio, "key": clave})
    return f"{base_url}?{params}"

def construir_teclado(pregunta_texto: str, clave: str):
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
    n_fotos = len(fotos_bytes) if fotos_bytes else 0
    tiene_video = "Sí" if video_bytes else "No"
    cuerpo = (
        f"BORRADOR PERIODÍSTICO — REFUGIO LATINOAMERICANO\n"
        f"Pendiente de revisión editorial.\n\n"
        f"Género: {genero_nombre}\n"
        f"Corresponsal: {nombre}\n"
        f"Fotos adjuntas: {n_fotos}\n"
        f"Video adjunto: {tiene_video}\n"
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
            json=payload,
            timeout=90
        )
        return r.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False

def extraer_titulo(borrador: str) -> str:
    for linea in borrador.split("\n"):
        linea = linea.strip()
        if linea.startswith("TÍTULO:") or linea.startswith("TITULO:"):
            return linea.split(":", 1)[1].strip()
    return "Borrador sin título"

# ========== FUNCIONES AUXILIARES PARA TESTIMONIOS ==========
def formatear_lista_preguntas(respuestas: dict, flujo: dict) -> str:
    if not flujo:
        return "No hay preguntas previas."
    lineas = []
    for i, p in enumerate(flujo["preguntas"]):
        clave = p["clave"]
        texto_corto = p["texto"].split("\n")[0].replace("*", "").strip()
        respuesta = respuestas.get(clave, "sin responder aún")
        lineas.append(f"{i+1}. {texto_corto}\n   → {respuesta[:120]}...")
    return "\n".join(lineas)

def teclado_testimonio_opciones() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar otro testimonio (máx 3)", callback_data="testimonio:agregar")],
        [InlineKeyboardButton("✅ Finalizar testimonios", callback_data="testimonio:finalizar")],
    ])

def teclado_consentimiento_fotos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Sí, tengo consentimiento", callback_data="fotos:si")],
        [InlineKeyboardButton("🚫 No, sin fotos", callback_data="fotos:no")],
    ])

# ========== NUEVOS HANDLERS PARA TESTIMONIOS ==========
async def iniciar_testimonios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["testimonios"] = []
    context.user_data["testimonio_actual"] = {}
    context.user_data["testimonio_paso"] = "nombre"
    context.user_data["fotos_testimonios"] = []
    context.user_data["consentimiento_fotos"] = None

    await update.message.reply_text(
        "📢 *Testimonios (Reportaje)*\n\n"
        "Para este género, necesitamos al menos *dos testimonios* de fuentes externas.\n"
        "Por cada persona que testifica, te pediré:\n"
        "• Nombre o alias (obligatorio, puede ser ficticio)\n"
        "• Organización (opcional, escribe '-' si no aplica)\n"
        "• Nacionalidad (obligatorio)\n"
        "• Edad (opcional, escribe '-' si no quieres decirla)\n"
        "• Luego el relato (mínimo 15 caracteres o puedes enviar un audio o archivo de audio de hasta 4 minutos).\n\n"
        "Empecemos con el *primer testimonio*.\n\n"
        "✏️ *Nombre o alias:*",
        parse_mode="Markdown"
    )
    return RECOLECTANDO_TESTIMONIOS

async def handle_testimonio_texto(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_alternativo: str = None) -> int:
    texto = texto_alternativo if texto_alternativo is not None else update.message.text.strip()
    paso = context.user_data.get("testimonio_paso", "nombre")
    actual = context.user_data.get("testimonio_actual", {})
    genero_key = context.user_data.get("genero")
    flujo = obtener_flujo(genero_key) if genero_key else None

    if paso == "nombre":
        if not texto:
            await update.message.reply_text("Necesito un nombre o alias. Escribí al menos un carácter.")
            return RECOLECTANDO_TESTIMONIOS
        actual["nombre"] = texto
        context.user_data["testimonio_paso"] = "organizacion"
        await update.message.reply_text(
            "📌 *Organización o institución* (opcional).\n"
            "Si no pertenece a ninguna, escribí un guión (-) para continuar.\n\n"
            "_Escribí la organización:_",
            parse_mode="Markdown"
        )
    elif paso == "organizacion":
        actual["organizacion"] = texto if texto != "-" else ""
        context.user_data["testimonio_paso"] = "nacionalidad"
        await update.message.reply_text("🌎 *Nacionalidad* (obligatorio):", parse_mode="Markdown")
    elif paso == "nacionalidad":
        if not texto:
            await update.message.reply_text("La nacionalidad es obligatoria. Escribí un país.")
            return RECOLECTANDO_TESTIMONIOS
        actual["nacionalidad"] = texto
        context.user_data["testimonio_paso"] = "edad"
        await update.message.reply_text(
            "🎂 *Edad* (opcional). Si no quieres decirla, escribí un guión (-).\n"
            "_Escribí la edad:_",
            parse_mode="Markdown"
        )
    elif paso == "edad":
        actual["edad"] = texto if texto != "-" else ""
        context.user_data["testimonio_paso"] = "texto"
        respuestas = context.user_data.get("respuestas", {})
        lista = formatear_lista_preguntas(respuestas, flujo) if flujo else "No hay preguntas previas."
        await update.message.reply_text(
            f"💬 *Relato / testimonio*\n\n"
            f"Pedile a la persona que relate su experiencia. Podés tomar como referencia alguna de estas preguntas:\n\n"
            f"{lista}\n\n"
            f"_Escribí el testimonio (mínimo 15 caracteres) o enviá un audio o archivo de audio:_",
            parse_mode="Markdown"
        )
    elif paso == "texto":
        if len(texto) < 15:
            await update.message.reply_text("El testimonio es muy corto. Por favor, desarrollá un poco más (mínimo 15 caracteres).")
            return RECOLECTANDO_TESTIMONIOS
        actual["texto"] = texto
        testimonios = context.user_data.get("testimonios", [])
        testimonios.append(actual)
        context.user_data["testimonios"] = testimonios
        cant = len(testimonios)

        if cant < 2:
            context.user_data["testimonio_actual"] = {}
            context.user_data["testimonio_paso"] = "nombre"
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado. Necesitamos al menos 2 testimonios.\n\n"
                "✏️ *Nombre o alias del siguiente testimonio:*",
                parse_mode="Markdown"
            )
            return RECOLECTANDO_TESTIMONIOS
        elif cant == 2:
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado. Ya tienes los 2 testimonios mínimos.\n\n"
                "¿Deseas agregar un testimonio más? (máximo 3)",
                reply_markup=teclado_testimonio_opciones()
            )
            return RECOLECTANDO_TESTIMONIOS
        elif cant == 3:
            await update.message.reply_text(
                f"✅ Testimonio #{cant} guardado. Has alcanzado el máximo de 3 testimonios.\n\n"
                "Ahora, ¿las personas que testimoniaron dieron su consentimiento para ser fotografiadas?",
                reply_markup=teclado_consentimiento_fotos()
            )
            return RECOLECTANDO_TESTIMONIOS
    return RECOLECTANDO_TESTIMONIOS

async def handle_testimonio_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja los audios (notas de voz o archivos de audio) para el relato del testimonio."""
    paso = context.user_data.get("testimonio_paso", "")
    if paso != "texto":
        await update.message.reply_text("⏳ En este momento no se espera un audio. Por favor, respondé las preguntas actuales con texto.")
        return RECOLECTANDO_TESTIMONIOS
    
    # Determinar si es nota de voz o archivo de audio
    voice = update.message.voice
    audio = update.message.audio
    if voice:
        file_id = voice.file_id
        duration = voice.duration
    elif audio:
        file_id = audio.file_id
        duration = audio.duration
    else:
        await update.message.reply_text("No se detectó un audio válido. Enviá una nota de voz o un archivo de audio (MP3, M4A, OGG).")
        return RECOLECTANDO_TESTIMONIOS
    
    # Verificar duración (máximo 4 minutos = 240 segundos)
    if duration and duration > 240:
        await update.message.reply_text("⏱️ El audio es demasiado largo (máximo 4 minutos). Por favor, enviá uno más corto o escribí el testimonio directamente.")
        return RECOLECTANDO_TESTIMONIOS
    
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    texto_transcrito = await transcribir_audio_groq(file_id, context.bot)
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto_transcrito}_", parse_mode="Markdown")
    
    return await handle_testimonio_texto(update, context, texto_transcrito)

async def handle_testimonio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "testimonio:agregar":
        context.user_data["testimonio_actual"] = {}
        context.user_data["testimonio_paso"] = "nombre"
        await query.message.reply_text(
            "✏️ *Nombre o alias del tercer testimonio:*",
            parse_mode="Markdown"
        )
        return RECOLECTANDO_TESTIMONIOS
    elif data == "testimonio:finalizar":
        await query.message.reply_text(
            "Ahora, ¿las personas que testimoniaron dieron su consentimiento para ser fotografiadas?",
            reply_markup=teclado_consentimiento_fotos()
        )
        return RECOLECTANDO_TESTIMONIOS
    elif data == "fotos:si":
        context.user_data["consentimiento_fotos"] = True
        num_testimonios = len(context.user_data.get("testimonios", []))
        await query.message.reply_text(
            f"📸 *Fotos de los testimonios*\n\n"
            f"Por favor, enviame una foto de cada una de las {num_testimonios} personas que brindaron testimonio.\n"
            f"Envía una foto por mensaje. Cuando hayas enviado todas, escribe /listo.\n\n"
            f"_Si alguna persona no quiere ser fotografiada, simplemente no envíes su foto._",
            parse_mode="Markdown"
        )
        context.user_data["esperando_fotos_testimonios"] = True
        return ESPERANDO_FOTOS
    elif data == "fotos:no":
        context.user_data["consentimiento_fotos"] = False
        await query.message.reply_text(
            "📝 *Información adicional*\n\n"
            "¿Hay algún dato o contexto relevante que quieras agregar al reportaje?\n"
            "Podés escribirlo ahora, o enviar '-' para saltar.",
            parse_mode="Markdown"
        )
        context.user_data["esperando_ampliacion"] = True
        return RECOLECTANDO_TESTIMONIOS
    return RECOLECTANDO_TESTIMONIOS
    
    # ========== HANDLERS PRINCIPALES (originales, con modificaciones) ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy el *Chatbot - Refugio Latinoamericano*.\n\n"
        "🔐 Ingresá la contraseña de acceso:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return AUTENTICACION

async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = context.user_data.get("nombre", "")
    context.user_data.clear()
    if nombre:
        context.user_data["nombre"] = nombre
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Historia de vida", callback_data="genero:historia_vida")],
        [InlineKeyboardButton("⚖️ Denuncia", callback_data="genero:denuncia")],
        [InlineKeyboardButton("📅 Evento", callback_data="genero:evento")],
        [InlineKeyboardButton("📌 Agenda / Servicio", callback_data="genero:agenda")],
        [InlineKeyboardButton("📚 Explicador", callback_data="genero:explicador")],
        [InlineKeyboardButton("🎭 Cultura", callback_data="genero:cultura")],
        [InlineKeyboardButton("📰 Reportaje", callback_data="genero:reportaje")],
    ])
    msg = (
        f"🔄 *Nuevo reporte*{f' — {nombre}' if nombre else ''}\n\n"
        "¿Qué tipo de nota vas a registrar?\n\n"
        "_Elegí el género periodístico que mejor se adapta._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=teclado)
    return SELECCION_GENERO

async def handle_autenticacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() == os.getenv("BOT_PASSWORD", ""):
        await update.message.reply_text(
            "✅ *Acceso autorizado.*\n\nIngresá tu *nombre y apellido completo*:",
            parse_mode="Markdown")
        return IDENTIFICACION
    await update.message.reply_text(
        "❌ *Contraseña incorrecta.*\n\nEscribí /start para intentar de nuevo.",
        parse_mode="Markdown")
    return ConversationHandler.END

async def handle_identificacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = update.message.text.strip()
    if len(nombre.split()) < 2:
        await update.message.reply_text("Ingresá tu *nombre y apellido completo*.", parse_mode="Markdown")
        return IDENTIFICACION
    context.user_data.update({"nombre": nombre, "respuestas": {}, "fotos": 0, "foto_ids": []})
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Historia de vida", callback_data="genero:historia_vida")],
        [InlineKeyboardButton("⚖️ Denuncia", callback_data="genero:denuncia")],
        [InlineKeyboardButton("📅 Evento", callback_data="genero:evento")],
        [InlineKeyboardButton("📌 Agenda / Servicio", callback_data="genero:agenda")],
        [InlineKeyboardButton("📚 Explicador", callback_data="genero:explicador")],
        [InlineKeyboardButton("🎭 Cultura", callback_data="genero:cultura")],
        [InlineKeyboardButton("📰 Reportaje", callback_data="genero:reportaje")],
    ])
    await update.message.reply_text(
        f"Perfecto, *{nombre}*.\n\n"
        "¿Qué tipo de nota vas a registrar?\n\n"
        "_Elegí el género periodístico que mejor se adapta._",
        parse_mode="Markdown", reply_markup=teclado
    )
    return SELECCION_GENERO

async def handle_seleccion_genero(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    genero_key = query.data.split(":")[1]
    if genero_key not in GENEROS:
        await query.edit_message_text("Género no válido.")
        return ConversationHandler.END
    context.user_data["genero"] = genero_key
    context.user_data["pregunta_idx"] = 0
    context.user_data["repregunta_activa"] = False
    context.user_data["respuestas"] = {}
    context.user_data["fotos"] = 0
    context.user_data["foto_ids"] = []
    flujo = obtener_flujo(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]
    await query.edit_message_text(f"✅ Seleccionaste: *{genero_nombre}*", parse_mode="Markdown")
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
    teclado = construir_teclado(pregunta["texto"], pregunta["clave"])
    texto = f"*Pregunta {idx+1}/{total}*\n\n{pregunta['texto']}\n\n_Escribí tu respuesta o usá el botón para grabar un audio._"
    await message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)

async def handle_respuesta_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await procesar_respuesta(update, context, update.message.text)

async def handle_respuesta_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
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
                r = requests.post(f"{mini_app_url}/transcribir",
                                  json={"audio_b64": audio_b64}, timeout=60)
                if r.status_code == 200:
                    texto = r.json().get("texto", "")
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
    if len(texto_respuesta.strip()) < 3:
        await update.message.reply_text("📝 Necesito más información para continuar.")
        return RESPONDIENDO_PREGUNTA
    if not context.user_data.get("repregunta_activa", False):
        await update.message.reply_text("🔎 _Analizando respuesta..._", parse_mode="Markdown")
        analisis = analizar_respuesta_con_groq(pregunta["texto"], texto_respuesta)
        if analisis.get("necesita_repregunta") and analisis.get("repregunta"):
            if pregunta["clave"] not in context.user_data["respuestas"]:
                context.user_data["respuestas"][pregunta["clave"]] = texto_respuesta
            else:
                context.user_data["respuestas"][pregunta["clave"]] += f"\n\n[Ampliación 1]: {texto_respuesta}"
            context.user_data["repregunta_activa"] = True
            await update.message.reply_text(f"💬 {analisis['repregunta']}", parse_mode="Markdown")
            return RESPONDIENDO_PREGUNTA
    if context.user_data.get("repregunta_activa", False):
        clave = pregunta["clave"]
        if clave in context.user_data["respuestas"]:
            context.user_data["respuestas"][clave] += f"\n\n[Ampliación 2]: {texto_respuesta}"
        else:
            context.user_data["respuestas"][clave] = texto_respuesta
    else:
        context.user_data["respuestas"][pregunta["clave"]] = texto_respuesta
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

async def mostrar_resumen(message, context) -> int:
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    resumen = construir_resumen(context.user_data["respuestas"], flujo)
    await message.reply_text(
        resumen + "\n\n_Revisá tus respuestas antes de continuar._",
        parse_mode="Markdown",
        reply_markup=teclado_resumen()
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
            f"🎥 Opcionalmente, podés adjuntar *un video de hasta 30 segundos*.\n\n"
            f"⚠️ _Videos más largos no serán procesados._\n\n"
            f"Cuando termines, escribí */generar*",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS
    elif accion == "editar":
        genero_key = context.user_data["genero"]
        flujo = obtener_flujo(genero_key)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "✏️ *¿Qué respuesta querés editar?*\n\n_Tocá el número correspondiente:_",
            parse_mode="Markdown",
            reply_markup=teclado_numeros(flujo)
        )
        return REVISION_RESUMEN
    elif accion == "volver":
        return await mostrar_resumen(query.message, context)
    return REVISION_RESUMEN

async def handle_seleccion_editar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    pregunta = flujo["preguntas"][idx]
    context.user_data["editando_idx"] = idx
    context.user_data["editando_clave"] = pregunta["clave"]
    respuesta_actual = context.user_data["respuestas"].get(pregunta["clave"], "_Sin respuesta_")
    if len(respuesta_actual) > 300:
        respuesta_actual = respuesta_actual[:300] + "..."
    teclado = construir_teclado(pregunta["texto"], pregunta["clave"])
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✏️ *Editando pregunta {idx+1}*\n\n"
        f"{pregunta['texto']}\n\n"
        f"_Respuesta actual:_\n{respuesta_actual}\n\n"
        f"_Escribí la nueva respuesta o grabá un audio:_",
        parse_mode="Markdown",
        reply_markup=teclado
    )
    return EDITANDO_RESPUESTA

async def handle_edicion_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await procesar_edicion(update, context, update.message.text)

async def handle_edicion_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await procesar_edicion(update, context, texto)

async def procesar_edicion(update, context, texto_nuevo: str) -> int:
    if len(texto_nuevo.strip()) < 3:
        await update.message.reply_text("📝 La respuesta es muy corta. Intentá de nuevo.")
        return EDITANDO_RESPUESTA
    clave = context.user_data.get("editando_clave")
    idx = context.user_data.get("editando_idx")
    if clave:
        context.user_data["respuestas"][clave] = texto_nuevo
        await update.message.reply_text(f"✅ *Respuesta {idx+1} actualizada.*", parse_mode="Markdown")
    context.user_data.pop("editando_idx", None)
    context.user_data.pop("editando_clave", None)
    return await mostrar_resumen(update.message, context)

async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get("esperando_fotos_testimonios"):
        foto = update.message.photo[-1]
        if "fotos_testimonios" not in context.user_data:
            context.user_data["fotos_testimonios"] = []
        context.user_data["fotos_testimonios"].append(foto.file_id)
        recibidas = len(context.user_data["fotos_testimonios"])
        necesarias = len(context.user_data.get("testimonios", []))
        if recibidas < necesarias:
            await update.message.reply_text(f"📸 Foto {recibidas} de {necesarias} recibida. Enviá la siguiente foto (o /listo si ya están todas).")
        else:
            await update.message.reply_text(f"✅ Recibidas las {necesarias} fotos. Ahora, ¿alguna información adicional?")
            await update.message.reply_text(
                "📝 *Información adicional*\n\n"
                "¿Hay algún dato o contexto relevante que quieras agregar al reportaje?\n"
                "Podés escribirlo ahora, o enviar '-' para saltar.",
                parse_mode="Markdown"
            )
            context.user_data["esperando_ampliacion"] = True
            context.user_data["esperando_fotos_testimonios"] = False
        return ESPERANDO_FOTOS
    else:
        foto = update.message.photo[-1]
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
            msg = f"📷 Foto {n} recibida ✓\n_Más fotos, un video, o /generar_"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return ESPERANDO_FOTOS

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    duracion = video.duration if video.duration else 0
    if duracion > 30:
        await update.message.reply_text(
            f"⚠️ *Video rechazado* — dura {duracion} segundos.\nSolo aceptamos videos de hasta 30 segundos.",
            parse_mode="Markdown")
        return ESPERANDO_FOTOS
    context.user_data["video_id"] = video.file_id
    context.user_data["video_duracion"] = duracion
    genero_key = context.user_data["genero"]
    fotos_min = GENEROS[genero_key]["fotos_min"]
    fotos = context.user_data.get("fotos", 0)
    if fotos < fotos_min:
        msg = f"🎥 Video recibido ✓ ({duracion}s)\n_Todavía necesitás {fotos_min-fotos} foto(s) más antes de /generar_"
    else:
        msg = f"🎥 Video recibido ✓ ({duracion}s)\n_Listo para generar con /generar_"
    await update.message.reply_text(msg, parse_mode="Markdown")
    return ESPERANDO_FOTOS

async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    genero_key = context.user_data.get("genero")
    if not genero_key:
        await update.message.reply_text("⚠️ No hay género seleccionado.")
        return ConversationHandler.END

    if genero_key == "reportaje":
        testimonios = context.user_data.get("testimonios", [])
        if len(testimonios) < 2:
            await update.message.reply_text("⚠️ Aún no has completado los testimonios mínimos (2). Vuelve al flujo anterior.")
            return ConversationHandler.END
        fotos_ids = context.user_data.get("fotos_testimonios", [])
        fotos_bytes = await descargar_fotos(fotos_ids, context.bot) if fotos_ids else []
        ampliacion = context.user_data.get("ampliacion_info", "")
        if context.user_data.get("esperando_ampliacion"):
            await update.message.reply_text("Primero respondé la pregunta de ampliación (o enviá '-').")
            return RECOLECTANDO_TESTIMONIOS
        await update.message.reply_text("⏳ *Generando borrador...*", parse_mode="Markdown")
        nombre = context.user_data.get("nombre", "corresponsal")
        genero_nombre = GENEROS[genero_key]["nombre"]
        borrador = generar_borrador(
            context.user_data.get("respuestas", {}),
            nombre,
            genero_key,
            len(fotos_bytes),
            testimonios=testimonios,
            ampliacion=ampliacion
        )
        titulo = extraer_titulo(borrador)
    else:
        fotos_min = GENEROS[genero_key]["fotos_min"]
        fotos = context.user_data.get("fotos", 0)
        if fotos < fotos_min:
            await update.message.reply_text(
                f"⚠️ Necesitás *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* antes de generar.\n"
                f"_Enviaste {fotos} hasta ahora._",
                parse_mode="Markdown")
            return ESPERANDO_FOTOS
        await update.message.reply_text("⏳ *Generando borrador...*", parse_mode="Markdown")
        nombre = context.user_data.get("nombre", "corresponsal")
        genero_nombre = GENEROS[genero_key]["nombre"]
        fotos_ids = context.user_data.get("foto_ids", [])
        fotos_bytes = await descargar_fotos(fotos_ids, context.bot)
        borrador = generar_borrador(
            context.user_data.get("respuestas", {}),
            nombre,
            genero_key,
            len(fotos_bytes),
            testimonios=None,
            ampliacion=""
        )
        titulo = extraer_titulo(borrador)

    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])

    await update.message.reply_text("📧 _Enviando al equipo editorial..._", parse_mode="Markdown")
    video_bytes = None
    video_id = context.user_data.get("video_id")
    if video_id and genero_key != "reportaje":
        await update.message.reply_text("🎥 _Descargando video..._", parse_mode="Markdown")
        video_bytes = await descargar_video(video_id, context.bot)
    enviado = enviar_con_resend(borrador, nombre, titulo, genero_nombre, fotos_bytes, video_bytes)
    if enviado:
        info = f"📎 Fotos: {len(fotos_bytes)}"
        if video_bytes:
            info += f"\n🎥 Video: {context.user_data.get('video_duracion', 0)}s"
        await update.message.reply_text(
            f"✅ *Borrador enviado.*\n"
            f"📰 Género: {genero_nombre}\n"
            f"👤 Corresponsal: {nombre}\n{info}\n\n"
            f"_Usá /reiniciar para un nuevo reporte_",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "✅ *Borrador generado.*\n_Usá /reiniciar para un nuevo reporte_",
            parse_mode="Markdown")
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado. /start para comenzar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ========== AUTO‑PING ==========
def start_self_pinger(port: int, interval_seconds: int = 240):
    def pinger():
        url = f"http://localhost:{port}/health"
        while True:
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    if response.status == 200:
                        logger.debug("Self-ping exitoso a /health")
                    else:
                        logger.warning(f"Self-ping respuesta inesperada: {response.status}")
            except Exception as e:
                logger.error(f"Error en self-ping: {e}")
            time.sleep(interval_seconds)
    thread = threading.Thread(target=pinger, daemon=True)
    thread.start()
    logger.info(f"Auto‑pinger iniciado (cada {interval_seconds}s en puerto {port})")

# ========== WEBHOOK Y SERVIDOR ==========
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})

async def webhook_endpoint(request: Request) -> PlainTextResponse:
    try:
        body = await request.json()
        update = Update.de_json(body, application.bot)
        await application.process_update(update)
        return PlainTextResponse("", status_code=200)
    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        return PlainTextResponse("", status_code=500)

async def set_webhook():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        logger.error("RENDER_EXTERNAL_URL no configurada. El webhook no se establecerá.")
        return
    webhook_url = f"{render_url}/webhook/{TOKEN}"
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logger.info(f"Webhook configurado en {webhook_url}")

# ========== PUNTO DE ENTRADA ==========
if __name__ == "__main__":
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("reiniciar", reiniciar)],
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
            CommandHandler("generar", cmd_generar),
        ],
    )
    application.add_handler(conv_handler)

    async def start_app():
        await application.initialize()
        await set_webhook()
        starlette_app = Starlette(routes=[
            Route("/health", health_check, methods=["GET"]),
            Route("/webhook/{token}", webhook_endpoint, methods=["POST"]),
        ])
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    start_self_pinger(PORT, interval_seconds=240)
    asyncio.run(start_app())
