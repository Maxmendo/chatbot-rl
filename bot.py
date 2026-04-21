"""
CHATBOT REFUGIO LATINOAMERICANO — bot.py v3
Incorpora: selección de género periodístico + flujos diferenciados + motor de repreguntas

Variables de entorno:
  TELEGRAM_BOT_TOKEN, GROQ_API_KEY, RESEND_API_KEY, EDITORIAL_EMAIL, BOT_PASSWORD, MINI_APP_URL
"""

import os
import base64
import logging
import json
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Estados del ConversationHandler
(AUTENTICACION, IDENTIFICACION, SELECCION_GENERO, INICIO_FLUJO,
 RESPONDIENDO_PREGUNTA, ESPERANDO_FOTOS, CIERRE_ETICO) = range(7)

# ═══════════════════════════════════════════════════════════════
# GÉNEROS PERIODÍSTICOS
# ═══════════════════════════════════════════════════════════════

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
        "fotos_min": 2,
        "estructura": "reportaje",
    },
}

# ═══════════════════════════════════════════════════════════════
# FLUJOS CONVERSACIONALES POR GÉNERO
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
        {
            "clave": "identificacion",
            "texto": "👤 *Datos de identificación*\n\nNombre completo, edad, ocupación y país de origen de la persona entrevistada.",
        },
        {
            "clave": "origen",
            "texto": "🌍 *1. Origen*\n\n¿De dónde viene? ¿Cómo era su vida antes de migrar? ¿En qué año llegó al país y cuántos años tenía cuando dejó su lugar de origen? ¿Vino sola, o con familia, pareja y/o amigos?",
        },
        {
            "clave": "motivos",
            "texto": "🔄 *2. Motivos de movilidad*\n\n¿Qué razones le llevaron a emigrar? ¿Qué significó ese momento? ¿Cómo planificó su partida?",
        },
        {
            "clave": "transito",
            "texto": "🛤️ *3. Tránsito*\n\n¿Cómo fue el viaje? ¿Qué experiencias, obstáculos o emociones marcaron ese trayecto?",
        },
        {
            "clave": "llegada",
            "texto": "📍 *4. Llegada*\n\n¿Cuáles fueron sus primeras impresiones al llegar? ¿Qué situaciones o desafíos recuerda de esos primeros días? ¿Contaba con contactos previos con otros miembros de su comunidad?",
        },
        {
            "clave": "laboral",
            "texto": "💼 *5. Inserción laboral*\n\n¿Cómo fue su inserción laboral en el país? ¿Actualmente está trabajando? ¿Trabaja por su cuenta o en relación de dependencia?",
        },
        {
            "clave": "presente",
            "texto": "🏡 *6. Presente*\n\n¿Cómo es su vida hoy? ¿A qué se dedica, qué vínculos construyó? ¿Se relaciona con personas de su comunidad acá? ¿Qué cosas echa de menos de su lugar de origen? ¿Cuáles son sus proyectos aquí?",
        },
        {
            "clave": "horizonte",
            "texto": "🔮 *7. Horizonte*\n\n¿Cómo vive hoy su identidad y sentido de pertenencia? ¿Piensa en regresar a su tierra de origen o proyecta su futuro acá?",
        },
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
        {
            "clave": "naturaleza",
            "texto": "🔍 *1. Naturaleza del problema*\n\n¿Qué tipo de situación se vive? (vulneración de derechos, discriminación, obstáculos para acceder a servicios, abuso institucional, violencia, trámite irregular). ¿Es un hecho puntual o una situación sostenida? ¿Afecta a una o muchas personas en situación similar?",
        },
        {
            "clave": "personas_afectadas",
            "texto": "👥 *2. Personas afectadas*\n\n¿Quiénes son las personas afectadas? ¿Se trata de una persona, familia, comunidad? ¿De qué país o comunidad provienen? ¿Qué las llevó a dejar su lugar de origen? ¿Hace cuánto viven en el país donde ocurre la situación? ¿Cuál es su situación migratoria actual (con residencia, en trámite, solicitantes de refugio, situación irregular)? ¿Hay dimensiones específicas (niñez, personas mayores, embarazadas, mujeres víctimas de violencia de género, personas LGBTIQ+, personas con discapacidad)?",
        },
        {
            "clave": "identificacion_afectadas",
            "texto": "🔐 *3. Cómo quieren ser identificadas*\n\n¿Cómo les gustaría a las personas afectadas ser identificadas en la nota? ¿Con sus nombres completos, iniciales o seudónimo? _Si están en situación de solicitud de refugio o irregularidad, siempre es mejor proteger su identidad._",
        },
        {
            "clave": "responsables",
            "texto": "🏛️ *4. Responsables*\n\n¿Quiénes son los responsables? (autoridad estatal, institución pública, empresa, particular). ¿Nombre, cargo, dependencia concreta? ¿Existe un marco normativo que se está incumpliendo?",
        },
        {
            "clave": "lugar_momento",
            "texto": "📍 *5. Lugar y momento*\n\n¿Dónde ocurre? (país, provincia, ciudad, barrio, dirección). ¿Cuándo? ¿Hecho puntual o sostenido en el tiempo?",
        },
        {
            "clave": "gestiones",
            "texto": "📋 *6. Gestiones previas*\n\n¿Las personas afectadas ya hicieron denuncia formal? ¿Dónde? ¿Número de expediente o acta? ¿Contactaron algún organismo, ONG, consulado, defensoría? ¿Qué respuesta recibieron?",
        },
        {
            "clave": "testimonios",
            "texto": "📢 *7. Testimonios y pruebas*\n\n¿Hay otras personas que hayan vivido o visto lo mismo y puedan testimoniar? ¿Documentos, capturas, audios, comunicaciones oficiales, pruebas materiales? ¿Alguna fuente experta (organización, abogada, académica, referente) que pueda aportar contexto?",
        },
        {
            "clave": "impacto",
            "texto": "💥 *8. Impacto personal y comunitario*\n\n¿Cómo afecta la vida cotidiana de las personas? _(sin enfocar solo en el sufrimiento — también en cómo resisten, se organizan, se defienden)_ ¿Qué consecuencias tiene en la comunidad más amplia? ¿Se están organizando para responder?",
        },
        {
            "clave": "expectativas",
            "texto": "🎯 *9. Qué esperan*\n\n¿Qué esperan lograr al visibilizar esta situación? ¿Hay demanda específica hacia alguna autoridad?",
        },
        {
            "clave": "contraste",
            "texto": "⚖️ *10. Contraste editorial*\n\n¿Refugio debería buscar la palabra de la institución, funcionario o empresa señalada antes de publicar? ¿O se publica tal como llega y esperamos una eventual respuesta?",
        },
    ],
}

# Flujos placeholder para los 5 géneros pendientes de afinar
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
    """Retorna el flujo conversacional para el género dado."""
    if genero_key == "historia_vida":
        return FLUJO_HISTORIA_VIDA
    elif genero_key == "denuncia":
        return FLUJO_DENUNCIA
    else:
        # Placeholder para los otros 5 géneros
        flujo = dict(FLUJO_GENERICO_7W)
        flujo["entrada"] = FLUJO_GENERICO_7W["entrada"].format(nombre=GENEROS[genero_key]["nombre"])
        return flujo


# ═══════════════════════════════════════════════════════════════
# PROMPTS EDITORIALES POR GÉNERO
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# MOTOR DE REPREGUNTAS
# ═══════════════════════════════════════════════════════════════

PROMPT_ANALISTA = """Sos un editor/a periodístico analizando una respuesta de un corresponsal de campo.

Evaluá la respuesta siguiendo estos tres criterios:

1. PROFUNDIDAD: ¿Tiene menos de 15-20 palabras significativas o es muy vaga?
2. INCONSISTENCIA TEMPORAL: ¿Aparecen referencias contradictorias (ej: "ayer" y "el mes pasado" juntos)?
3. AMBIGÜEDAD: ¿Falta información clave que la pregunta requería?

IMPORTANTE: No repreguntés si la respuesta es clara, rica y completa, aunque sea breve. Una respuesta de 10 palabras puede ser perfectamente válida si es concreta y específica.

Respondé SOLO con un JSON válido con esta estructura exacta:
{
  "necesita_repregunta": true/false,
  "tipo": "profundidad" | "inconsistencia" | "ambiguedad" | null,
  "repregunta": "texto de la repregunta con eco empático (parafraseo breve + nueva pregunta)" | null
}

Si necesita_repregunta es false, los otros campos son null.

EJEMPLOS DE REPREGUNTAS CON ECO EMPÁTICO:

Ejemplo profundidad:
Pregunta: "¿Qué ocurrió?"
Respuesta corta: "Hubo un allanamiento."
JSON: {"necesita_repregunta": true, "tipo": "profundidad", "repregunta": "Entiendo que hubo un allanamiento. Para poder contarlo bien, ¿podés darme más contexto? ¿Quiénes participaron, qué buscaban, cómo fue el momento?"}

Ejemplo inconsistencia:
Pregunta: "¿Cuándo ocurrió?"
Respuesta: "Fue ayer, pero viene pasando desde el mes pasado."
JSON: {"necesita_repregunta": true, "tipo": "inconsistencia", "repregunta": "Noté que mencionaste 'ayer' y también 'el mes pasado'. ¿Podés aclararme las fechas para no confundir a los lectores? ¿Cuándo fue el hecho puntual y cuándo arrancó la situación?"}

Ejemplo respuesta completa (NO repregunta):
Pregunta: "¿Dónde ocurrió?"
Respuesta: "En el barrio La Cárcova, en José León Suárez, partido de San Martín."
JSON: {"necesita_repregunta": false, "tipo": null, "repregunta": null}"""


def analizar_respuesta_con_groq(pregunta: str, respuesta: str) -> dict:
    """Analiza la respuesta y determina si requiere repregunta."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"necesita_repregunta": False, "tipo": None, "repregunta": None}

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": PROMPT_ANALISTA},
                    {"role": "user", "content": f"PREGUNTA:\n{pregunta}\n\nRESPUESTA DEL CORRESPONSAL:\n{respuesta}\n\nAnalizá y respondé SOLO con el JSON."}
                ],
                "max_tokens": 400,
                "temperature": 0.3,
                "response_format": {"type": "json_object"}
            },
            timeout=30
        )
        data = r.json()
        if "choices" in data:
            contenido = data["choices"][0]["message"]["content"]
            resultado = json.loads(contenido)
            return resultado
    except Exception as e:
        logger.error(f"Error en análisis: {e}")

    return {"necesita_repregunta": False, "tipo": None, "repregunta": None}


# ═══════════════════════════════════════════════════════════════
# TRANSCRIPCIÓN DE AUDIO
# ═══════════════════════════════════════════════════════════════

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
            data={"model": "whisper-large-v3", "language": "es",
                  "prompt": "Entrevista periodística sobre migraciones en América Latina."},
            timeout=60
        )
        data = response.json()
        if "text" in data:
            return f"{data['text'].strip()} [transcripto de audio]"
        return "[No se pudo transcribir. Respondé en texto.]"
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[Error al transcribir. Respondé en texto.]"


# ═══════════════════════════════════════════════════════════════
# GENERACIÓN DEL BORRADOR
# ═══════════════════════════════════════════════════════════════

def generar_con_groq(respuestas: dict, nombre: str, genero_key: str, fotos: int) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "❌ Falta GROQ_API_KEY."

    prompt_sistema = obtener_prompt(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]

    datos = "\n".join(f"{k.upper()}: {v}" for k, v in respuestas.items())

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": f"GÉNERO: {genero_nombre}\n\nREPORTE DEL CORRESPONSAL:\n{datos}\n\nCorresponsal: {nombre}\nFotos adjuntas: {fotos}\n\nRedactá el borrador completo respetando TODOS los criterios editoriales."}
                ],
                "max_tokens": 3000,
                "temperature": 0.7
            },
            timeout=90
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        return f"❌ Error: {data.get('error', {}).get('message', 'desconocido')}"
    except Exception as e:
        logger.error(f"Error Groq: {e}")
        return "❌ Error al conectar con la IA."


# ═══════════════════════════════════════════════════════════════
# MINI APP + MULTIMEDIA + EMAIL
# ═══════════════════════════════════════════════════════════════

def get_mini_app_url(pregunta_texto: str, clave: str) -> str:
    base_url = os.getenv("MINI_APP_URL", "")
    if not base_url:
        return ""
    import urllib.parse
    # Limpiar texto para URL
    texto_limpio = pregunta_texto.replace("*", "").replace("_", "")[:200]
    params = urllib.parse.urlencode({"label": clave.upper(), "texto": texto_limpio, "key": clave})
    return f"{base_url}?{params}"


def construir_teclado(pregunta_texto: str, clave: str):
    url = get_mini_app_url(pregunta_texto, clave)
    if url:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🎙️ Grabar respuesta en audio", web_app=WebAppInfo(url=url))]])
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
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=90)
        return r.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False


def extraer_titulo(borrador: str) -> str:
    for l in borrador.split("\n"):
        l = l.strip()
        if l.startswith("TÍTULO:") or l.startswith("TITULO:"):
            return l.split(":", 1)[1].strip()
    return "Borrador sin título"


# ═══════════════════════════════════════════════════════════════
# HANDLERS DEL FLUJO
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy el *Chatbot - Refugio Latinoamericano*.\n\n"
        "🔐 Ingresá la contraseña de acceso:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return AUTENTICACION


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

    # Mostrar menú de géneros con botones inline
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

    flujo = obtener_flujo(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]

    await query.edit_message_text(f"✅ Seleccionaste: *{genero_nombre}*", parse_mode="Markdown")

    # Enviar mensaje de entrada del flujo
    await query.message.reply_text(
        flujo["entrada"] + "\n\n⚠️ _Ningún contenido se publica sin revisión editorial._",
        parse_mode="Markdown"
    )

    # Enviar primera pregunta
    await enviar_pregunta_actual(query.message, context)
    return RESPONDIENDO_PREGUNTA


async def enviar_pregunta_actual(message, context):
    """Envía la pregunta actual con botón de Mini App."""
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
    """Recibe audio directo (reenviado) y lo transcribe."""
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await procesar_respuesta(update, context, texto)


async def handle_respuesta_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recibe el audio grabado desde la Mini App."""
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
    """Procesa una respuesta (texto o audio transcripto) aplicando el motor de repreguntas."""
    genero_key = context.user_data["genero"]
    flujo = obtener_flujo(genero_key)
    idx = context.user_data["pregunta_idx"]
    pregunta = flujo["preguntas"][idx]

    # Validación mínima: respuesta no vacía
    if len(texto_respuesta.strip()) < 3:
        await update.message.reply_text("📝 Necesito más información para continuar.")
        return RESPONDIENDO_PREGUNTA

    # MOTOR DE REPREGUNTAS (solo si no se hizo ya una repregunta en esta pregunta)
    if not context.user_data.get("repregunta_activa", False):
        # Mostrar indicador
        await update.message.reply_text("🔎 _Analizando respuesta..._", parse_mode="Markdown")
        analisis = analizar_respuesta_con_groq(pregunta["texto"], texto_respuesta)

        if analisis.get("necesita_repregunta") and analisis.get("repregunta"):
            # Guardamos la respuesta inicial
            if pregunta["clave"] not in context.user_data["respuestas"]:
                context.user_data["respuestas"][pregunta["clave"]] = texto_respuesta
            else:
                context.user_data["respuestas"][pregunta["clave"]] += f"\n\n[Ampliación 1]: {texto_respuesta}"

            # Marcamos que ya hicimos una repregunta
            context.user_data["repregunta_activa"] = True

            await update.message.reply_text(
                f"💬 {analisis['repregunta']}",
                parse_mode="Markdown"
            )
            return RESPONDIENDO_PREGUNTA

    # Si ya hubo repregunta, concatenamos la ampliación
    if context.user_data.get("repregunta_activa", False):
        clave = pregunta["clave"]
        if clave in context.user_data["respuestas"]:
            context.user_data["respuestas"][clave] += f"\n\n[Ampliación 2]: {texto_respuesta}"
        else:
            context.user_data["respuestas"][clave] = texto_respuesta
    else:
        context.user_data["respuestas"][pregunta["clave"]] = texto_respuesta

    # Reseteamos flag para la próxima pregunta
    context.user_data["repregunta_activa"] = False

    # Avanzar a la siguiente pregunta
    context.user_data["pregunta_idx"] += 1
    idx_nuevo = context.user_data["pregunta_idx"]

    if idx_nuevo < len(flujo["preguntas"]):
        await update.message.reply_text(f"✓ Respuesta registrada ({idx_nuevo}/{len(flujo['preguntas'])})")
        await enviar_pregunta_actual(update.message, context)
        return RESPONDIENDO_PREGUNTA
    else:
        # Terminaron las preguntas — pasar a fotos
        fotos_min = GENEROS[genero_key]["fotos_min"]
        await update.message.reply_text(
            f"✅ *¡Todas las preguntas completas!*\n\n"
            f"📸 Enviame *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* del hecho.\n"
            f"🎥 Opcionalmente, podés adjuntar *un video de hasta 30 segundos*.\n\n"
            f"⚠️ _Videos más largos no serán procesados._\n\n"
            f"Cuando termines, escribí */generar*",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

    borrador = generar_con_groq(context.user_data.get("respuestas", {}), nombre, genero_key, fotos)
    titulo = extraer_titulo(borrador)

    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])

    await update.message.reply_text("📧 _Enviando al equipo editorial..._", parse_mode="Markdown")
    fotos_bytes = await descargar_fotos(context.user_data.get("foto_ids", []), context.bot)
    video_bytes = None
    video_id = context.user_data.get("video_id")
    if video_id:
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
            f"👤 Corresponsal: {nombre}\n{info}\n\n_/start para nuevo reporte_",
            parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ *Borrador generado.*\n_/start para nuevo reporte_", parse_mode="Markdown")
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado. /start para comenzar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AUTENTICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autenticacion)],
            IDENTIFICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_identificacion)],
            SELECCION_GENERO: [CallbackQueryHandler(handle_seleccion_genero, pattern=r"^genero:")],
            RESPONDIENDO_PREGUNTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_respuesta_texto),
                MessageHandler(filters.VOICE | filters.AUDIO, handle_respuesta_audio),
                MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_respuesta_miniapp),
            ],
            ESPERANDO_FOTOS: [
                MessageHandler(filters.PHOTO, handle_foto),
                MessageHandler(filters.VIDEO, handle_video),
                CommandHandler("generar", cmd_generar)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar), CommandHandler("generar", cmd_generar)],
    )
    app.add_handler(conv)
    logger.info("Chatbot Refugio Latinoamericano v3 corriendo — motor de repreguntas activo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
