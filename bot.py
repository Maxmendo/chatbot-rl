"""
QUILMES BOT — bot.py con Mini App para grabación de audio
Integra grabador de voz via Telegram Mini App + Groq Whisper
"""

import os
import base64
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

(AUTENTICACION, IDENTIFICACION, INICIO, PREGUNTA_QUE, PREGUNTA_QUIEN,
 PREGUNTA_CUANDO, PREGUNTA_DONDE, PREGUNTA_COMO, PREGUNTA_POR_QUE,
 PREGUNTA_PARA_QUE, ESPERANDO_FOTOS) = range(11)

PREGUNTAS = {
    PREGUNTA_QUE:      {"label": "¿QUÉ?",     "texto": "¿Qué ocurrió? Describí el hecho central.", "clave": "que"},
    PREGUNTA_QUIEN:    {"label": "¿QUIÉN?",    "texto": "¿Quiénes están involucrados?", "clave": "quien"},
    PREGUNTA_CUANDO:   {"label": "¿CUÁNDO?",   "texto": "¿Cuándo ocurrió? Fecha y hora.", "clave": "cuando"},
    PREGUNTA_DONDE:    {"label": "¿DÓNDE?",    "texto": "¿Dónde ocurrió? País, ciudad, barrio.", "clave": "donde"},
    PREGUNTA_COMO:     {"label": "¿CÓMO?",     "texto": "¿Cómo ocurrió? Secuencia de eventos.", "clave": "como"},
    PREGUNTA_POR_QUE:  {"label": "¿POR QUÉ?",  "texto": "¿Por qué ocurrió? Causas y contexto.", "clave": "por_que"},
    PREGUNTA_PARA_QUE: {"label": "¿IMPACTO?",  "texto": "¿Cuál es el impacto para las comunidades?", "clave": "para_que"},
}

ORDEN = [PREGUNTA_QUE, PREGUNTA_QUIEN, PREGUNTA_CUANDO, PREGUNTA_DONDE,
         PREGUNTA_COMO, PREGUNTA_POR_QUE, PREGUNTA_PARA_QUE]

SYSTEM_PROMPT = """Sos un editor/a periodístico de Refugio Latinoamericano, medio digital argentino especializado en periodismo de migraciones desde una perspectiva de derechos humanos e interculturalidad.

ESTILO EDITORIAL:
- Personas migrantes como sujetos de derecho, nunca "ilegales"
- Voz activa, párrafos cortos (máximo 4 oraciones)
- Títulos directos, sin clickbait
- Perspectiva de género cuando corresponda

ESTRUCTURA OBLIGATORIA:
1. TÍTULO: (máximo 12 palabras, sin punto final)
2. COPETE: (2-3 oraciones: qué, quién, dónde, relevancia)
3. DESARROLLO: (3-5 párrafos: cómo, por qué, contexto)
4. CITA DIRECTA: (entre comillas, extraída del reporte)
5. CIERRE: (impacto y qué sigue)
6. VERIFICACIÓN PENDIENTE:
   - [VERIFICAR] dato 1
7. ETIQUETAS SUGERIDAS: etiqueta1, etiqueta2, etiqueta3
8. NOTAS PARA EL EDITOR/A: observaciones sobre solidez del material"""


def get_mini_app_url(estado: int) -> str:
    """Construye la URL de la Mini App con los parámetros de la pregunta."""
    base_url = os.getenv("MINI_APP_URL", "")
    if not base_url:
        return ""
    p = PREGUNTAS[estado]
    import urllib.parse
    params = urllib.parse.urlencode({
        "label": p["label"],
        "texto": p["texto"],
        "key": p["clave"]
    })
    return f"{base_url}?{params}"


def generar_con_groq(respuestas: dict, nombre: str, fotos: int) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "❌ Falta GROQ_API_KEY."
    etiquetas = {"que":"QUÉ","quien":"QUIÉN","cuando":"CUÁNDO","donde":"DÓNDE",
                 "como":"CÓMO","por_que":"POR QUÉ","para_que":"IMPACTO"}
    datos = "".join(f"{v}: {respuestas.get(k,'(no especificado)')}\n" for k,v in etiquetas.items())
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json={"model":"llama-3.3-70b-versatile",
                  "messages":[{"role":"system","content":SYSTEM_PROMPT},
                               {"role":"user","content":f"REPORTE:\n{datos}\nPeriodista: {nombre}\nFotos: {fotos}\n\nRedactá el borrador completo."}],
                  "max_tokens":2500,"temperature":0.7},
            timeout=60)
        data = r.json()
        if "choices" in data: return data["choices"][0]["message"]["content"]
        return f"❌ Error: {data.get('error',{}).get('message','desconocido')}"
    except Exception as e:
        logger.error(f"Error Groq: {e}")
        return "❌ Error al conectar con la IA."


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
                  "prompt": "Entrevista periodística sobre migraciones en Argentina."},
            timeout=60
        )
        data = response.json()
        if "text" in data:
            return f"{data['text'].strip()} [transcripto de audio]"
        return "[No se pudo transcribir. Respondé en texto.]"
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[Error al transcribir. Respondé en texto.]"


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


def enviar_con_resend(borrador: str, nombre: str, titulo: str, fotos_bytes: list = None) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    editorial_email = os.getenv("EDITORIAL_EMAIL")
    if not api_key or not editorial_email:
        return False
    cuerpo = (f"BORRADOR PERIODÍSTICO — REFUGIO LATINOAMERICANO\n"
              f"Pendiente de revisión editorial.\n\nCorresponsal: {nombre}\n"
              f"Fotos: {len(fotos_bytes) if fotos_bytes else 0}\n{'─'*50}\n\n{borrador}\n\n{'─'*50}\n"
              f"Generado por Quilmes Bot.")
    payload = {"from":"Quilmes Bot <onboarding@resend.dev>","to":[editorial_email],
               "subject":f"[BORRADOR] {titulo} — {nombre}","text":cuerpo}
    if fotos_bytes:
        payload["attachments"] = [
            {"filename":f["nombre"],"content":base64.b64encode(f["datos"]).decode(),"type":"image/jpeg"}
            for f in fotos_bytes]
    try:
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json=payload, timeout=60)
        return r.status_code in [200,201]
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False


def extraer_titulo(borrador: str) -> str:
    for l in borrador.split("\n"):
        l = l.strip()
        if l.startswith("TÍTULO:"): return l.replace("TÍTULO:","").strip()
    return "Borrador sin título"


def construir_teclado_pregunta(estado: int):
    """Construye el teclado con botón de Mini App para grabar audio."""
    mini_app_url = get_mini_app_url(estado)
    if mini_app_url:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🎙️ Grabar respuesta en audio",
                web_app=WebAppInfo(url=mini_app_url)
            )
        ]])
    return None


# ── HANDLERS ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy *Quilmes Bot* de *Refugio Latinoamericano*.\n\n"
        "🔐 Ingresá la contraseña de acceso:",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
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
    await update.message.reply_text(
        f"Perfecto, *{nombre}*.\n\nVoy a hacerte *7 preguntas*. "
        "Podés responder en *texto* o usando el botón 🎙️ para grabar un *audio*.\n\n"
        "⚠️ _Ningún contenido se publica sin revisión editorial._\n\n¿Empezamos?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["✅ Empezar"]], one_time_keyboard=True, resize_keyboard=True))
    return INICIO


async def handle_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Comenzamos:", reply_markup=ReplyKeyboardRemove())
    context.user_data["estado_pregunta"] = PREGUNTA_QUE
    await enviar_pregunta(update, context, PREGUNTA_QUE)
    return PREGUNTA_QUE


async def enviar_pregunta(update, context, estado: int):
    """Envía la pregunta con el botón de Mini App."""
    p = PREGUNTAS[estado]
    teclado = construir_teclado_pregunta(estado)
    texto = f"*{p['label']}*\n\n{p['texto']}\n\n_Escribí tu respuesta o usá el botón para grabar un audio._"
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recibe el audio grabado desde la Mini App."""
    import json
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)

    try:
        data = json.loads(update.effective_message.web_app_data.data)
        if data.get("type") == "audio":
            await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")

            # Transcribir via servidor Mini App
            mini_app_url = os.getenv("MINI_APP_URL", "")
            audio_b64 = data.get("audio_b64", "")

            if mini_app_url and audio_b64:
                r = requests.post(f"{mini_app_url}/transcribir",
                    json={"audio_b64": audio_b64}, timeout=60)
                if r.status_code == 200:
                    texto = r.json().get("texto", "")
                    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
                    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = f"{texto} [audio]"
                    return await avanzar(update, context, estado)

            await update.message.reply_text("❌ No se pudo transcribir. Respondé en texto.")
            return estado

    except Exception as e:
        logger.error(f"Error web_app_data: {e}")
        await update.message.reply_text("❌ Error procesando el audio. Respondé en texto.")
        return estado


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Transcribe audios enviados directamente (reenviados)."""
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")
    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)
    await update.message.reply_text(f"📝 *Transcripción:*\n_{texto}_", parse_mode="Markdown")
    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    texto = update.message.text
    if len(texto.split()) < 4:
        await update.message.reply_text("📝 Podés ampliar un poco más.")
        return estado
    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def avanzar(update, context, estado_actual) -> int:
    idx = ORDEN.index(estado_actual)
    await update.message.reply_text(f"✓ _{idx+1}/7_", parse_mode="Markdown")
    if idx < len(ORDEN)-1:
        siguiente = ORDEN[idx+1]
        context.user_data["estado_pregunta"] = siguiente
        await enviar_pregunta(update, context, siguiente)
        return siguiente
    else:
        context.user_data["estado_pregunta"] = ESPERANDO_FOTOS
        await update.message.reply_text(
            "✅ *¡Las 7 preguntas completas!*\n\n📸 Enviame *al menos una foto*.\nCuando termines escribí */generar*",
            parse_mode="Markdown")
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    foto = update.message.photo[-1]
    if "foto_ids" not in context.user_data:
        context.user_data["foto_ids"] = []
    context.user_data["foto_ids"].append(foto.file_id)
    context.user_data["fotos"] = len(context.user_data["foto_ids"])
    await update.message.reply_text(
        f"📷 Foto {context.user_data['fotos']} recibida ✓\n_Más fotos o /generar_",
        parse_mode="Markdown")
    return ESPERANDO_FOTOS


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fotos = context.user_data.get("fotos", 0)
    if fotos == 0:
        await update.message.reply_text("⚠️ Enviá al menos una foto.")
        return ESPERANDO_FOTOS
    await update.message.reply_text("⏳ *Generando borrador...*", parse_mode="Markdown")
    nombre = context.user_data.get("nombre", "corresponsal")
    borrador = generar_con_groq(context.user_data.get("respuestas", {}), nombre, fotos)
    titulo = extraer_titulo(borrador)
    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])
    await update.message.reply_text("📧 _Enviando al equipo editorial..._", parse_mode="Markdown")
    fotos_bytes = await descargar_fotos(context.user_data.get("foto_ids", []), context.bot)
    enviado = enviar_con_resend(borrador, nombre, titulo, fotos_bytes)
    if enviado:
        await update.message.reply_text(
            f"✅ *Borrador enviado.*\n👤 Corresponsal: {nombre}\n📎 Fotos: {len(fotos_bytes)}\n\n_/start para nuevo reporte_",
            parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ *Borrador generado.*\n_/start para nuevo reporte_", parse_mode="Markdown")
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado. /start para comenzar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    texto_h = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto)
    audio_h = MessageHandler(filters.VOICE | filters.AUDIO, handle_audio)
    webapp_h = MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AUTENTICACION:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autenticacion)],
            IDENTIFICACION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_identificacion)],
            INICIO:           [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inicio)],
            PREGUNTA_QUE:     [texto_h, audio_h, webapp_h],
            PREGUNTA_QUIEN:   [texto_h, audio_h, webapp_h],
            PREGUNTA_CUANDO:  [texto_h, audio_h, webapp_h],
            PREGUNTA_DONDE:   [texto_h, audio_h, webapp_h],
            PREGUNTA_COMO:    [texto_h, audio_h, webapp_h],
            PREGUNTA_POR_QUE: [texto_h, audio_h, webapp_h],
            PREGUNTA_PARA_QUE:[texto_h, audio_h, webapp_h],
            ESPERANDO_FOTOS:  [
                MessageHandler(filters.PHOTO, handle_foto),
                CommandHandler("generar", cmd_generar)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar), CommandHandler("generar", cmd_generar)],
    )
    app.add_handler(conv)
    logger.info("Quilmes Bot con Mini App corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
