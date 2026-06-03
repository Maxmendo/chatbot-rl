"""
CHATBOT REFUGIO LATINOAMERICANO — Webhook
Corregido: handlers duplicados eliminados, route webhook fijo, handle_foto con returns explícitos,
paréntesis en log corregido, estados sin fantasmas.
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

# Estados — sin fantasmas
(AUTENTICACION, IDENTIFICACION, SELECCION_GENERO,
 RESPONDIENDO_PREGUNTA, REVISION_RESUMEN, EDITANDO_RESPUESTA,
 ESPERANDO_FOTOS, RECOLECTANDO_TESTIMONIOS) = range(8)

GENEROS = {
    "historia_vida": {"nombre":"Historia de vida","descripcion":"Testimonio biográfico de una persona migrante","fotos_min":3,"estructura":"cronica"},
    "denuncia": {"nombre":"Denuncia","descripcion":"Situación de vulneración de derechos","fotos_min":2,"estructura":"analisis"},
    "evento": {"nombre":"Evento","descripcion":"Algo que pasó o va a pasar","fotos_min":2,"estructura":"noticia"},
    "agenda": {"nombre":"Agenda / Servicio","descripcion":"Información útil para la comunidad","fotos_min":1,"estructura":"servicio"},
    "explicador": {"nombre":"Explicador","descripcion":"Pedagogía sobre temas complejos","fotos_min":1,"estructura":"explicador"},
    "cultura": {"nombre":"Cultura","descripcion":"Identidad, celebración, intercambio","fotos_min":2,"estructura":"cronica"},
    "reportaje": {"nombre":"Reportaje","descripcion":"Análisis profundo de un fenómeno","fotos_min":2,"estructura":"reportaje"},
}

FLUJO_HISTORIA_VIDA = {
    "entrada": (
        "📖 *Historia de vida*\n\n"
        "Vas a registrar una historia de vida. Recordá: no es un interrogatorio sino una conversación empática. "
        "La persona entrevistada es sujeto de derechos y protagonista de su historia — no víctima ni héroe.\n\n"
        "💡 _Tip:_ Si la persona se tensiona con alguna pregunta, podés saltearla y volver después.\n\n"
        "Primero, contame brevemente *nombre completo, edad, ocupación y país de origen* de la persona que vas a entrevistar."
    ),
    "preguntas": [
        {"clave":"identificacion","texto":"👤 *Datos de identificación*\n\nNombre completo, edad, ocupación y país de origen de la persona entrevistada."},
        {"clave":"origen","texto":"🌍 *1. Origen*\n\n¿De dónde viene? ¿Cómo era su vida antes de migrar? ¿En qué año llegó al país y cuántos años tenía cuando dejó su lugar de origen? ¿Vino sola, o con familia, pareja y/o amigos?"},
        {"clave":"motivos","texto":"🔄 *2. Motivos de movilidad*\n\n¿Qué razones le llevaron a emigrar? ¿Qué significó ese momento? ¿Cómo planificó su partida?"},
        {"clave":"transito","texto":"🛤️ *3. Tránsito*\n\n¿Cómo fue el viaje? ¿Qué experiencias, obstáculos o emociones marcaron ese trayecto?"},
        {"clave":"llegada","texto":"📍 *4. Llegada*\n\n¿Cuáles fueron sus primeras impresiones al llegar? ¿Qué situaciones o desafíos recuerda de esos primeros días? ¿Contaba con contactos previos con otros miembros de su comunidad?"},
        {"clave":"laboral","texto":"💼 *5. Inserción laboral*\n\n¿Cómo fue su inserción laboral en el país? ¿Actualmente está trabajando? ¿Trabaja por su cuenta o en relación de dependencia?"},
        {"clave":"presente","texto":"🏡 *6. Presente*\n\n¿Cómo es su vida hoy? ¿A qué se dedica, qué vínculos construyó? ¿Se relaciona con personas de su comunidad acá? ¿Qué cosas echa de menos de su lugar de origen? ¿Cuáles son sus proyectos aquí?"},
        {"clave":"horizonte","texto":"🔮 *7. Horizonte*\n\n¿Cómo vive hoy su identidad y sentido de pertenencia? ¿Piensa en regresar a su tierra de origen o proyecta su futuro acá?"},
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
        {"clave":"naturaleza","texto":"🔍 *1. Naturaleza del problema*\n\n¿Qué tipo de situación se vive? (vulneración de derechos, discriminación, obstáculos para acceder a servicios, abuso institucional, violencia, trámite irregular). ¿Es un hecho puntual o una situación sostenida? ¿Afecta a una o muchas personas en situación similar?"},
        {"clave":"personas_afectadas","texto":"👥 *2. Personas afectadas*\n\n¿Quiénes son las personas afectadas? ¿Se trata de una persona, familia, comunidad? ¿De qué país o comunidad provienen? ¿Qué las llevó a dejar su lugar de origen? ¿Hace cuánto viven en el país donde ocurre la situación? ¿Cuál es su situación migratoria actual (con residencia, en trámite, solicitantes de refugio, situación irregular)? ¿Hay dimensiones específicas (niñez, personas mayores, embarazadas, mujeres víctimas de violencia de género, personas LGBTIQ+, personas con discapacidad)?"},
        {"clave":"identificacion_afectadas","texto":"🔐 *3. Cómo quieren ser identificadas*\n\n¿Cómo les gustaría a las personas afectadas ser identificadas en la nota? ¿Con sus nombres completos, iniciales o seudónimo? _Si están en situación de solicitud de refugio o irregularidad, siempre es mejor proteger su identidad._"},
        {"clave":"responsables","texto":"🏛️ *4. Responsables*\n\n¿Quiénes son los responsables? (autoridad estatal, institución pública, empresa, particular). ¿Nombre, cargo, dependencia concreta? ¿Existe un marco normativo que se está incumpliendo?"},
        {"clave":"lugar_momento","texto":"📍 *5. Lugar y momento*\n\n¿Dónde ocurre? (país, provincia, ciudad, barrio, dirección). ¿Cuándo? ¿Hecho puntual o sostenido en el tiempo?"},
        {"clave":"gestiones","texto":"📋 *6. Gestiones previas*\n\n¿Las personas afectadas ya hicieron denuncia formal? ¿Dónde? ¿Número de expediente o acta? ¿Contactaron algún organismo, ONG, consulado, defensoría? ¿Qué respuesta recibieron?"},
        {"clave":"testimonios","texto":"📢 *7. Testimonios y pruebas*\n\n¿Hay otras personas que hayan vivido o visto lo mismo y puedan testimoniar? ¿Documentos, capturas, audios, comunicaciones oficiales, pruebas materiales? ¿Alguna fuente experta (organización, abogada, académica, referente) que pueda aportar contexto?"},
        {"clave":"impacto","texto":"💥 *8. Impacto personal y comunitario*\n\n¿Cómo afecta la vida cotidiana de las personas? _(sin enfocar solo en el sufrimiento — también en cómo resisten, se organizan, se defienden)_ ¿Qué consecuencias tiene en la comunidad más amplia? ¿Se están organizando para responder?"},
        {"clave":"expectativas","texto":"🎯 *9. Qué esperan*\n\n¿Qué esperan lograr al visibilizar esta situación? ¿Hay demanda específica hacia alguna autoridad?"},
        {"clave":"contraste","texto":"⚖️ *10. Contraste editorial*\n\n¿Refugio debería buscar la palabra de la institución, funcionario o empresa señalada antes de publicar? ¿O se publica tal como llega y esperamos una eventual respuesta?"},
    ],
}

FLUJO_GENERICO_7W = {
    "entrada": "📰 *{nombre}*\n\nContame en tus propias palabras de qué se trata.",
    "preguntas": [
        {"clave":"que","texto":"📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central."},
        {"clave":"quien","texto":"👤 *¿QUIÉN/ES?*\n\nPersonas, organizaciones o comunidades involucradas."},
        {"clave":"cuando","texto":"🕐 *¿CUÁNDO?*\n\nFecha, hora, contexto temporal."},
        {"clave":"donde","texto":"📍 *¿DÓNDE?*\n\nPaís, ciudad, barrio, dirección."},
        {"clave":"como","texto":"🔍 *¿CÓMO?*\n\nSecuencia de eventos, circunstancias."},
        {"clave":"por_que","texto":"💡 *¿POR QUÉ?*\n\nCausas, contexto, antecedentes."},
        {"clave":"impacto","texto":"🎯 *¿IMPACTO?*\n\nConsecuencias para la comunidad migrante."},
    ],
}

def obtener_flujo(genero_key: str) -> dict:
    if genero_key == "historia_vida": return FLUJO_HISTORIA_VIDA
    elif genero_key == "denuncia": return FLUJO_DENUNCIA
    else:
        flujo = dict(FLUJO_GENERICO_7W)
        flujo["entrada"] = FLUJO_GENERICO_7W["entrada"].format(nombre=GENEROS[genero_key]["nombre"])
        return flujo

PROMPT_BASE = """Sos editor/a periodístico de Refugio Latinoamericano, medio digital especializado en periodismo de migraciones con perspectiva de derechos humanos e interculturalidad.

CRITERIOS EDITORIALES OBLIGATORIOS:
- Nunca: "ilegal", "clandestino", "indocumentado", "oleada", "avalancha", "aluvión", "asalto", "invasión", "catástrofe", "fenómeno", "personas vulnerables"
- Siempre anteponer "persona": persona migrante, persona refugiada, persona solicitante
- Diferenciar migrante / refugiada / solicitante de asilo
- Nunca masculino genérico
- Persona migrante como sujeto de derechos y agente activo — no víctima, no héroe
- Responsabilizar a Estados e instituciones como titulares de obligaciones
- Voz activa, oraciones cortas (<18 palabras), sin adjetivos innecesarios
- Detalles concretos — transportar al lector a la escena"""

PROMPT_HISTORIA_VIDA = PROMPT_BASE + """

ESTRUCTURA — CRÓNICA:
1. TÍTULO: máximo 12 palabras, sin punto final, eje de movilidad humana + DDHH
2. BAJADA: 2-3 oraciones con el eje editorial
3. APERTURA: premisa central
4. DESARROLLO CRONOLÓGICO: origen → motivos → tránsito → llegada → inserción laboral → presente → horizonte
5. CITA DIRECTA DESTACADA
6. CIERRE: horizonte abierto
7. VERIFICACIÓN PENDIENTE: [VERIFICAR] dato
8. ETIQUETAS SUGERIDAS: 3-5 etiquetas
9. NOTAS PARA EL EDITOR/A: consentimientos, protección de identidad"""

PROMPT_DENUNCIA = PROMPT_BASE + """

ESTRUCTURA — ANÁLISIS:
1. TÍTULO: máximo 12 palabras, sin punto final
2. BAJADA: 2-3 oraciones con el eje editorial
3. APERTURA: descripción del problema y personas afectadas como sujetos de derechos
4. DESARROLLO: a) descripción b) responsables y marco normativo c) gestiones previas d) impacto y agencia e) repercusión
5. CITAS: personas afectadas + fuente experta si hay
6. CONTRASTE: señalar si corresponde buscar la palabra de la parte señalada
7. CIERRE: expectativas de las personas afectadas
8. VERIFICACIÓN PENDIENTE: [VERIFICAR] dato
9. ETIQUETAS SUGERIDAS
10. NOTAS PARA EL EDITOR/A: protección de identidades, riesgo de represalia"""

PROMPT_GENERICO = PROMPT_BASE + """

ESTRUCTURA:
1. TÍTULO: máximo 12 palabras
2. BAJADA: 2-3 oraciones
3. DESARROLLO: 3-5 párrafos con las 7W
4. CITA DIRECTA si hay testimonio
5. CIERRE
6. VERIFICACIÓN PENDIENTE
7. ETIQUETAS SUGERIDAS
8. NOTAS PARA EL EDITOR/A"""

def obtener_prompt(genero_key: str) -> str:
    if genero_key == "historia_vida": return PROMPT_HISTORIA_VIDA
    elif genero_key == "denuncia": return PROMPT_DENUNCIA
    else: return PROMPT_GENERICO

def llamar_groq(messages: list, max_tokens: int = 400, temperature: float = 0.3,
                response_format: dict = None):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("Falta GROQ_API_KEY")
        return None
    payload = {"model":"llama-3.3-70b-versatile","messages":messages,"max_tokens":max_tokens,"temperature":temperature}
    if response_format:
        payload["response_format"] = response_format
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json=payload, timeout=90
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        logger.error(f"Error Groq: {data.get('error',{}).get('message','desconocido')}")
        return None
    except Exception as e:
        logger.error(f"Error llamando a Groq: {e}")
        return None

PROMPT_ANALISTA = """Analizá la respuesta de un corresponsal de campo.
Criterios:
1. PROFUNDIDAD: menos de 15 palabras significativas o muy vaga
2. INCONSISTENCIA TEMPORAL: referencias contradictorias
3. AMBIGÜEDAD: falta información clave

IMPORTANTE: No repreguntés si la respuesta es clara y concreta, aunque sea breve.
Respondé SOLO con JSON: {"necesita_repregunta": true/false, "tipo": "profundidad"|"inconsistencia"|"ambiguedad"|null, "repregunta": "texto con eco empático"|null}"""

def analizar_respuesta_con_groq(pregunta: str, respuesta: str) -> dict:
    resultado = llamar_groq(
        messages=[
            {"role":"system","content":PROMPT_ANALISTA},
            {"role":"user","content":f"PREGUNTA:\n{pregunta}\n\nRESPUESTA:\n{respuesta}\n\nRespondé SOLO con el JSON."}
        ],
        max_tokens=400, temperature=0.3,
        response_format={"type":"json_object"}
    )
    if resultado:
        try:
            return json.loads(resultado)
        except Exception as e:
            logger.error(f"Error parseando JSON: {e}")
    return {"necesita_repregunta":False,"tipo":None,"repregunta":None}

async def transcribir_audio_groq(file_id: str, bot) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "[Error: falta GROQ_API_KEY]"
    try:
        file = await bot.get_file(file_id)
        audio_bytes = await file.download_as_bytearray()
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization":f"Bearer {api_key}"},
            files={"file":("audio.ogg",bytes(audio_bytes),"audio/ogg")},
            data={"model":"whisper-large-v3","language":"es","prompt":"Entrevista periodística sobre migraciones en América Latina."},
            timeout=60
        )
        data = response.json()
        if "text" in data:
            return f"{data['text'].strip()} [transcripto de audio]"
        return "[No se pudo transcribir. Respondé en texto.]"
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[Error al transcribir. Respondé en texto.]"

def extraer_titulo(borrador: str) -> str:
    match = re.search(r'\*{0,2}T[IÍ]TULO\*{0,2}:\s*(.+)', borrador, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("*")
    return "Borrador sin título"

def generar_borrador(respuestas: dict, nombre: str, genero_key: str, fotos: int,
                     testimonios: list = None, ampliacion: str = "") -> str:
    prompt_sistema = obtener_prompt(genero_key)
    genero_nombre = GENEROS[genero_key]["nombre"]
    datos = "\n".join(f"{k.upper()}: {v}" for k, v in respuestas.items())
    texto_extra = ""
    if testimonios:
        texto_extra = "\n\n=== TESTIMONIOS ===\n"
        for i, t in enumerate(testimonios, 1):
            texto_extra += f"Testimonio {i}:\nNombre: {t.get('nombre','')}\n"
            if t.get('organizacion'):
                texto_extra += f"Organización: {t['organizacion']}\n"
            texto_extra += f"Nacionalidad: {t.get('nacionalidad','')}\n"
            if t.get('edad'):
                texto_extra += f"Edad: {t['edad']}\n"
            texto_extra += f"Pregunta 1: {t.get('pregunta1','')}\n"
            if t.get('pregunta2'):
                texto_extra += f"Pregunta 2: {t['pregunta2']}\n"
            texto_extra += f"Respuesta: {t.get('respuesta','')}\n\n"
    if ampliacion:
        texto_extra += f"\n=== AMPLIACIÓN ===\n{ampliacion}\n"
    instruccion = "\n\nUSÁ ÚNICAMENTE la información proporcionada. NO inventes datos, nombres, fechas ni estadísticas."
    resultado = llamar_groq(
        messages=[
            {"role":"system","content":prompt_sistema + instruccion},
            {"role":"user","content":f"GÉNERO: {genero_nombre}\nCORRESPONSAL: {nombre}\nFOTOS: {fotos}\n\nREPORTE:\n{datos}{texto_extra}\n\nRedactá el borrador completo."}
        ],
        max_tokens=3000, temperature=0.7
    )
    return resultado if resultado else "❌ Error al generar el borrador."

def construir_resumen(respuestas: dict, flujo: dict, genero_key: str = None, testimonios: list = None) -> str:
    preguntas = flujo["preguntas"]
    lineas = ["📋 *Resumen de tu reporte*\n"]
    for i, pregunta in enumerate(preguntas):
        clave = pregunta["clave"]
        titulo = pregunta["texto"].split("\n")[0].replace("*","").strip()
        respuesta = respuestas.get(clave,"_Sin respuesta_")
        if len(respuesta) > 200:
            respuesta = respuesta[:200] + "..."
        lineas.append(f"*{i+1}. {titulo}*\n{respuesta}")
    if genero_key == "reportaje" and testimonios:
        lineas.append(f"\n📢 *Testimonios recolectados: {len(testimonios)}*")
        for i, t in enumerate(testimonios, 1):
            lineas.append(f"  {i}. {t.get('nombre','Sin nombre')} ({t.get('nacionalidad','')})")
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
        fila.append(InlineKeyboardButton(str(i+1), callback_data=f"editar:{i}"))
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
        [InlineKeyboardButton("⚖️ Denuncia", callback_data="genero:denuncia")],
        [InlineKeyboardButton("📅 Evento", callback_data="genero:evento")],
        [InlineKeyboardButton("📌 Agenda / Servicio", callback_data="genero:agenda")],
        [InlineKeyboardButton("📚 Explicador", callback_data="genero:explicador")],
        [InlineKeyboardButton("🎭 Cultura", callback_data="genero:cultura")],
        [InlineKeyboardButton("📰 Reportaje", callback_data="genero:reportaje")],
    ])

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

def get_mini_app_url(pregunta_texto: str, clave: str) -> str:
    base_url = os.getenv("MINI_APP_URL","")
    if not base_url:
        return ""
    import urllib.parse
    texto_limpio = pregunta_texto.replace("*","").replace("_","")[:200]
    params = urllib.parse.urlencode({"label":clave.upper(),"texto":texto_limpio,"key":clave})
    return f"{base_url}?{params}"

def construir_teclado_miniapp(pregunta_texto: str, clave: str):
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
            fotos_bytes.append({"nombre":f"foto_{i+1}.jpg","datos":bytes(foto_bytes)})
        except Exception as e:
            logger.error(f"Error descargando foto {i+1}: {e}")
    return fotos_bytes

async def descargar_video(video_id: str, bot) -> dict:
    try:
        file = await bot.get_file(video_id)
        video_bytes = await file.download_as_bytearray()
        return {"nombre":"video.mp4","datos":bytes(video_bytes)}
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
        f"{'─'*50}\n\n{borrador}\n\n{'─'*50}\nGenerado por el Chatbot de Refugio Latinoamericano."
    )
    payload = {
        "from":"Chatbot Refugio Latinoamericano <onboarding@resend.dev>",
        "to":[editorial_email],
        "subject":f"[{genero_nombre.upper()}] {titulo} — {nombre}",
        "text":cuerpo
    }
    attachments = []
    if fotos_bytes:
        attachments.extend([{"filename":f["nombre"],"content":base64.b64encode(f["datos"]).decode(),"type":"image/jpeg"} for f in fotos_bytes])
    if video_bytes:
        attachments.append({"filename":video_bytes["nombre"],"content":base64.b64encode(video_bytes["datos"]).decode(),"type":"video/mp4"})
    if attachments:
        payload["attachments"] = attachments
    try:
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json=payload, timeout=90)
        return r.status_code in [200,201]
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False

# ===== HANDLERS =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy el *Chatbot - Refugio Latinoamericano*.\n\n🔐 Ingresá la contraseña de acceso:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )
    return AUTENTICACION

async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = context.user_data.get("nombre","")
    context.user_data.clear()
    if nombre:
        context.user_data["nombre"] = nombre
    msg = f"🔄 *Nuevo reporte*{f' — {nombre}' if nombre else ''}\n\n¿Qué tipo de nota vas a registrar?\n\n_Elegí el género periodístico._"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=teclado_generos())
    return SELECCION_GENERO

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Comandos disponibles:*\n\n"
        "/start — Iniciar sesión\n"
        "/reiniciar — Nuevo reporte (mantiene tu nombre)\n"
        "/generar — Generar borrador y enviar al equipo\n"
        "/listo — Confirmar fotos de testimonios (solo reportajes)\n"
        "/cancelar — Cancelar el reporte actual\n"
        "/ayuda — Este mensaje",
        parse_mode="Markdown"
    )

async def handle_autenticacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() == os.getenv("BOT_PASSWORD",""):
        await update.message.reply_text("✅ *Acceso autorizado.*\n\nIngresá tu *nombre y apellido completo*:", parse_mode="Markdown")
        return IDENTIFICACION
    await update.message.reply_text("❌ *Contraseña incorrecta.*\n\nEscribí /start para intentar de nuevo.", parse_mode="Markdown")
    return ConversationHandler.END

async def handle_identificacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nombre = update.message.text.strip()
    if len(nombre.split()) < 2:
        await update.message.reply_text("Ingresá tu *nombre y apellido completo*.", parse_mode="Markdown")
        return IDENTIFICACION
    context.user_data.update({"nombre":nombre,"respuestas":{},"fotos":0,"foto_ids":[]})
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
        await query.edit_message_text("Género no válido.")
        return ConversationHandler.END
    context.user_data.update({
        "genero":genero_key,"pregunta_idx":0,"repregunta_activa":False,
        "respuestas":{},"fotos":0,"foto_ids":[]
    })
    flujo = obtener_flujo(genero_key)
    await query.edit_message_text(f"✅ Seleccionaste: *{GENEROS[genero_key]['nombre']}*", parse_mode="Markdown")
    await query.message.reply_text(flujo["entrada"] + "\n\n⚠️ _Ningún contenido se publica sin revisión editorial._", parse_mode="Markdown")
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
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    return await procesar_respuesta(update, context, texto)

async def handle_respuesta_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        data = json.loads(update.effective_message.web_app_data.data)
        if data.get("type") == "audio":
            await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
            mini_app_url = os.getenv("MINI_APP_URL","")
            audio_b64 = data.get("audio_b64","")
            if mini_app_url and audio_b64:
                r = requests.post(f"{mini_app_url}/transcribir", json={"audio_b64":audio_b64}, timeout=60)
                if r.status_code == 200:
                    texto = r.json().get("texto","")
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
            context.user_data["respuestas"][pregunta["clave"]] = context.user_data["respuestas"].get(pregunta["clave"], "") + texto_respuesta
            context.user_data["repregunta_activa"] = True
            await update.message.reply_text(f"💬 {analisis['repregunta']}", parse_mode="Markdown")
            return RESPONDIENDO_PREGUNTA
    if context.user_data.get("repregunta_activa", False):
        clave = pregunta["clave"]
        context.user_data["respuestas"][clave] = context.user_data["respuestas"].get(clave,"") + f"\n\n[Ampliación]: {texto_respuesta}"
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

async def iniciar_testimonios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.update({
        "testimonios":[],"testimonio_actual":{},"testimonio_paso":"nombre",
        "fotos_testimonios":[],"consentimiento_fotos":None,
        "esperando_fotos_testimonios":False,"esperando_ampliacion":False
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

async def handle_testimonio_texto(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_alternativo: str = None) -> int:
    if context.user_data.get("esperando_ampliacion"):
        texto = texto_alternativo if texto_alternativo is not None else update.message.text.strip()
        context.user_data["ampliacion_info"] = "" if texto == "-" else texto
        context.user_data["esperando_ampliacion"] = False
        return await mostrar_resumen(update.message, context)

    texto = texto_alternativo if texto_alternativo is not None else update.message.text.strip()
    paso = context.user_data.get("testimonio_paso","nombre")
    actual = context.user_data.get("testimonio_actual",{})

    if paso == "nombre":
        if not texto:
            await update.message.reply_text("Necesito un nombre o alias.")
            return RECOLECTANDO_TESTIMONIOS
        actual["nombre"] = texto
        context.user_data["testimonio_paso"] = "organizacion"
        await update.message.reply_text("📌 *Organización* (opcional — enviá '-' si no aplica):", parse_mode="Markdown")
    elif paso == "organizacion":
        actual["organizacion"] = "" if texto == "-" else texto
        context.user_data["testimonio_paso"] = "nacionalidad"
        await update.message.reply_text("🌎 *Nacionalidad* (obligatorio):", parse_mode="Markdown")
    elif paso == "nacionalidad":
        if not texto:
            await update.message.reply_text("La nacionalidad es obligatoria.")
            return RECOLECTANDO_TESTIMONIOS
        actual["nacionalidad"] = texto
        context.user_data["testimonio_paso"] = "edad"
        await update.message.reply_text("🎂 *Edad* (opcional — enviá '-' si no querés decirla):", parse_mode="Markdown")
    elif paso == "edad":
        actual["edad"] = "" if texto == "-" else texto
        context.user_data["testimonio_paso"] = "pregunta1"
        await update.message.reply_text("❓ *Primera pregunta (obligatoria)*\n\nFormulá la pregunta principal. Podés escribirla o enviar un audio.", parse_mode="Markdown")
    elif paso == "pregunta1":
        if len(texto) < 3:
            await update.message.reply_text("La pregunta es muy corta.")
            return RECOLECTANDO_TESTIMONIOS
        actual["pregunta1"] = texto
        context.user_data["testimonio_paso"] = "pregunta2"
        await update.message.reply_text("❔ *Segunda pregunta (opcional — enviá '-' para saltar):*", parse_mode="Markdown")
    elif paso == "pregunta2":
        actual["pregunta2"] = "" if texto == "-" else texto
        context.user_data["testimonio_paso"] = "respuesta"
        await update.message.reply_text("💬 *Respuesta u opinión*\n\nMínimo 15 caracteres si es texto.", parse_mode="Markdown")
    elif paso == "respuesta":
        if len(texto) < 15:
            await update.message.reply_text("La respuesta es muy corta. Desarrollá más o enviá un audio.")
            return RECOLECTANDO_TESTIMONIOS
        actual["respuesta"] = texto
        testimonios = context.user_data.get("testimonios",[])
        testimonios.append({
            "nombre":actual.get("nombre"),"organizacion":actual.get("organizacion",""),
            "nacionalidad":actual.get("nacionalidad"),"edad":actual.get("edad",""),
            "pregunta1":actual.get("pregunta1"),"pregunta2":actual.get("pregunta2",""),
            "respuesta":actual.get("respuesta"),
        })
        context.user_data["testimonios"] = testimonios
        context.user_data["testimonio_actual"] = {}
        cant = len(testimonios)
        if cant < 2:
            context.user_data["testimonio_paso"] = "nombre"
            await update.message.reply_text(f"✅ Testimonio #{cant} guardado.\n\n✏️ *Nombre o alias del siguiente:*", parse_mode="Markdown")
        elif cant == 2:
            await update.message.reply_text(f"✅ Testimonio #{cant} guardado. Ya tenés los 2 mínimos.\n\n¿Querés agregar un tercero?", reply_markup=teclado_testimonio_opciones())
        elif cant >= 3:
            await update.message.reply_text(f"✅ Testimonio #{cant} guardado. Alcanzaste el máximo.\n\n¿Las personas testimoniaron dieron consentimiento para ser fotografiadas?", reply_markup=teclado_consentimiento_fotos())
    context.user_data["testimonio_actual"] = actual
    return RECOLECTANDO_TESTIMONIOS

async def handle_testimonio_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    paso = context.user_data.get("testimonio_paso","")
    if paso not in ["pregunta1","pregunta2","respuesta"]:
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
        await query.message.reply_text("¿Las personas testimoniaron dieron consentimiento para ser fotografiadas?", reply_markup=teclado_consentimiento_fotos())
        return RECOLECTANDO_TESTIMONIOS
    elif data == "fotos:si":
        context.user_data["consentimiento_fotos"] = True
        context.user_data["esperando_fotos_testimonios"] = True
        context.user_data.pop("testimonio_paso", None)
        context.user_data.pop("testimonio_actual", None)
        num = len(context.user_data.get("testimonios",[]))
        await query.message.reply_text(
            f"📸 Enviame una foto de cada una de las {num} personas que testimoniaron.\nCuando termines, escribí /listo.\n\n_Si alguien no quiere ser fotografiado, simplemente no envíes su foto._",
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
    await message.reply_text(resumen + "\n\n_Revisá tus respuestas antes de continuar._", parse_mode="Markdown", reply_markup=teclado_resumen())
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
            f"✅ *Reporte confirmado.*\n\n📸 Enviame *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* del hecho.\n"
            f"🎥 Opcionalmente, un video de hasta 30 segundos.\n\nCuando termines, escribí */generar*",
            parse_mode="Markdown"
        )
        context.user_data["esperando_fotos_testimonios"] = False
        return ESPERANDO_FOTOS
    elif accion == "editar":
        flujo = obtener_flujo(context.user_data["genero"])
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✏️ *¿Qué respuesta querés editar?*\n\n_Tocá el número:_", parse_mode="Markdown", reply_markup=teclado_numeros(flujo))
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
    respuesta_actual = context.user_data["respuestas"].get(pregunta["clave"],"_Sin respuesta_")
    if len(respuesta_actual) > 300:
        respuesta_actual = respuesta_actual[:300] + "..."
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✏️ *Editando pregunta {idx+1}*\n\n{pregunta['texto']}\n\n_Respuesta actual:_\n{respuesta_actual}\n\n_Escribí la nueva respuesta o grabá un audio:_",
        parse_mode="Markdown", reply_markup=construir_teclado_miniapp(pregunta["texto"], pregunta["clave"])
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
        await update.message.reply_text("📝 La respuesta es muy corta.")
        return EDITANDO_RESPUESTA
    clave = context.user_data.get("editando_clave")
    idx = context.user_data.get("editando_idx")
    if clave:
        context.user_data["respuestas"][clave] = texto_nuevo
        await update.message.reply_text(f"✅ *Respuesta {idx+1} actualizada.*", parse_mode="Markdown")
    context.user_data.pop("editando_idx", None)
    context.user_data.pop("editando_clave", None)
    return await mostrar_resumen(update.message, context)

# FIX: handle_foto con returns explícitos en todas las ramas
async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    foto = update.message.photo[-1]
    if context.user_data.get("esperando_fotos_testimonios"):
        if "fotos_testimonios" not in context.user_data:
            context.user_data["fotos_testimonios"] = []
        context.user_data["fotos_testimonios"].append(foto.file_id)
        recibidas = len(context.user_data["fotos_testimonios"])
        necesarias = len(context.user_data.get("testimonios",[]))
        if recibidas < necesarias:
            await update.message.reply_text(f"📸 Foto {recibidas} de {necesarias} recibida. Enviá la siguiente o /listo.")
            return ESPERANDO_FOTOS  # explícito
        else:
            await update.message.reply_text(f"✅ Recibidas las {necesarias} fotos de testimonios.")
            context.user_data["esperando_fotos_testimonios"] = False
            context.user_data["esperando_ampliacion"] = True
            await update.message.reply_text(
                "📝 *Información adicional*\n\n¿Hay algún dato relevante que quieras agregar? Podés escribirlo, o enviá '-' para saltar.",
                parse_mode="Markdown"
            )
            return RECOLECTANDO_TESTIMONIOS  # FIX: era ESPERANDO_FOTOS, ahora correcto
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
        return ESPERANDO_FOTOS  # explícito

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    video = update.message.video
    duracion = video.duration if video.duration else 0
    if duracion > 30:
        await update.message.reply_text(f"⚠️ *Video rechazado* — dura {duracion}s. Máximo 30 segundos.", parse_mode="Markdown")
        return ESPERANDO_FOTOS
    context.user_data["video_id"] = video.file_id
    context.user_data["video_duracion"] = duracion
    fotos_min = GENEROS[context.user_data["genero"]]["fotos_min"]
    fotos = context.user_data.get("fotos",0)
    msg = f"🎥 Video recibido ✓ ({duracion}s)\n_{'Todavía necesitás ' + str(fotos_min-fotos) + ' foto(s) más antes de /generar' if fotos < fotos_min else 'Listo para /generar'}_"
    await update.message.reply_text(msg, parse_mode="Markdown")
    return ESPERANDO_FOTOS

async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    genero_key = context.user_data.get("genero")
    if not genero_key:
        await update.message.reply_text("⚠️ No hay género seleccionado.")
        return ConversationHandler.END
    fotos_hecho_ids = context.user_data.get("foto_ids",[])
    fotos_min = GENEROS[genero_key]["fotos_min"]
    if len(fotos_hecho_ids) < fotos_min:
        await update.message.reply_text(f"⚠️ Necesitás *al menos {fotos_min} foto{'s' if fotos_min > 1 else ''}* del hecho. Enviaste {len(fotos_hecho_ids)}.", parse_mode="Markdown")
        return ESPERANDO_FOTOS
    if genero_key == "reportaje":
        testimonios = context.user_data.get("testimonios",[])
        if len(testimonios) < 2:
            await update.message.reply_text("⚠️ Necesitás completar al menos 2 testimonios.")
            return ConversationHandler.END
        fotos_test_ids = context.user_data.get("fotos_testimonios",[])
    else:
        testimonios = None
        fotos_test_ids = []
    await update.message.reply_text("⏳ *Generando borrador...*", parse_mode="Markdown")
    nombre = context.user_data.get("nombre","corresponsal")
    genero_nombre = GENEROS[genero_key]["nombre"]
    fotos_hecho_bytes = await descargar_fotos(fotos_hecho_ids, context.bot)
    fotos_test_bytes = await descargar_fotos(fotos_test_ids, context.bot) if fotos_test_ids else []
    todas_fotos = fotos_hecho_bytes + fotos_test_bytes
    ampliacion = context.user_data.get("ampliacion_info","")
    borrador = generar_borrador(context.user_data.get("respuestas",{}), nombre, genero_key, len(todas_fotos), testimonios=testimonios, ampliacion=ampliacion)
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
        info += f"\n🎥 Video: {context.user_data.get('video_duracion',0)}s"
    await update.message.reply_text(
        f"✅ *{'Borrador enviado' if enviado else 'Borrador generado'}.*\n📰 {genero_nombre}\n👤 {nombre}\n{info}\n\n_Usá /reiniciar para un nuevo reporte_",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado. /start para comenzar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ===== MAIN =====

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
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
            CommandHandler("generar", cmd_generar),
        ],
    )

    # FIX: solo conv_handler + ayuda — sin duplicar CommandHandlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("ayuda", ayuda))

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
            logger.error("RENDER_EXTERNAL_URL no configurada.")
            return
        webhook_url = f"{render_url}/webhook/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        token_oculto = TOKEN[:10] + "***" + TOKEN[-4:] if len(TOKEN) > 14 else "***"
        logger.info(f"Webhook configurado en {render_url}/webhook/{token_oculto}")

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
        logger.info(f"Auto-pinger iniciado (cada {interval_seconds}s en puerto {port})")  # FIX: paréntesis cerrado

    async def start_app():
        await application.initialize()
        await set_webhook()
        # FIX: ruta webhook con token literal, no como parámetro de path
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
