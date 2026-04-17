"""
QUILMES BOT — bot.py (versión con transcripción de audio via Groq Whisper)
Groq + Groq Whisper + Resend + Password + Identificación + Fotos adjuntas

Variables de entorno necesarias:
  TELEGRAM_BOT_TOKEN  → token de @BotFather
  GROQ_API_KEY        → API key de console.groq.com (para texto Y audio)
  RESEND_API_KEY      → API key de resend.com
  EDITORIAL_EMAIL     → email del equipo editorial
  BOT_PASSWORD        → contraseña de acceso para corresponsales
"""

import os
import base64
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
    PREGUNTA_QUE:      {"texto": "📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central. ¿Qué sucedió exactamente?\n\n_Podés responder en texto o mandame un audio._", "clave": "que"},
    PREGUNTA_QUIEN:    {"texto": "👤 *¿QUIÉNES están involucrados?*\n\nPersonas, organizaciones o comunidades protagonistas.\n\n_Texto o audio._", "clave": "quien"},
    PREGUNTA_CUANDO:   {"texto": "🕐 *¿CUÁNDO ocurrió?*\n\nFecha, hora, y si sigue en curso o terminó.\n\n_Texto o audio._", "clave": "cuando"},
    PREGUNTA_DONDE:    {"texto": "📍 *¿DÓNDE ocurrió?*\n\nPaís, ciudad, barrio, dirección.\n\n_Texto o audio._", "clave": "donde"},
    PREGUNTA_COMO:     {"texto": "🔍 *¿CÓMO ocurrió?*\n\nSecuencia de eventos y circunstancias.\n\n_Texto o audio._", "clave": "como"},
    PREGUNTA_POR_QUE:  {"texto": "💡 *¿POR QUÉ ocurrió?*\n\nCausas, contexto y antecedentes.\n\n_Texto o audio._", "clave": "por_que"},
    PREGUNTA_PARA_QUE: {"texto": "🎯 *¿Cuál es el IMPACTO?*\n\nConsecuencias y relevancia para comunidades migrantes.\n\n_Texto o audio._", "clave": "para_que"},
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


async def transcribir_audio_groq(file_id: str, bot) -> str:
    """Descarga el audio de Telegram y lo transcribe con Groq Whisper."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "[Error: falta GROQ_API_KEY]"

    try:
        # Descargar audio de Telegram
        file = await bot.get_file(file_id)
        audio_bytes = await file.download_as_bytearray()
        logger.info(f"Audio descargado: {len(audio_bytes)} bytes")

        # Enviar a Groq Whisper
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.ogg", bytes(audio_bytes), "audio/ogg")},
            data={
                "model": "whisper-large-v3",
                "language": "es",
                "prompt": "Entrevista periodística sobre migraciones en Argentina. Español rioplatense."
            },
            timeout=60
        )

        data = response.json()
        if "text" in data:
            texto = data["text"].strip()
            logger.info(f"Audio transcripto: {texto[:100]}...")
            return f"{texto} [transcripto de audio]"
        else:
            logger.error(f"Error Groq Whisper: {data}")
            return "[No se pudo transcribir el audio. Por favor respondé en texto.]"

    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[Error al transcribir. Por favor respondé en texto.]"


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
        logger.warning("Resend no configurado.")
        return False
    cuerpo = (
        f"BORRADOR PERIODÍSTICO — REFUGIO LATINOAMERICANO\n"
        f"Pendiente de revisión editorial antes de publicar.\n\n"
        f"Corresponsal: {nombre}\n"
        f"Fotos adjuntas: {len(fotos_bytes) if fotos_bytes else 0}\n"
        f"{'─'*50}\n\n{borrador}\n\n{'─'*50}\n"
        f"Generado por Quilmes Bot. No publicar sin revisión editorial."
    )
    payload = {
        "from": "Quilmes Bot <onboarding@resend.dev>",
        "to": [editorial_email],
        "subject": f"[BORRADOR] {titulo} — {nombre}",
        "text": cuerpo
    }
    if fotos_bytes:
        payload["attachments"] = [
            {"filename": f["nombre"], "content": base64.b64encode(f["datos"]).decode("utf-8"), "type": "image/jpeg"}
            for f in fotos_bytes
        ]
    try:
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json=payload, timeout=60)
        if r.status_code in [200,201]:
            logger.info(f"Email enviado a {editorial_email}")
            return True
        logger.error(f"Error Resend {r.status_code}: {r.text}")
        return False
    except Exception as e:
        logger.error(f"Error Resend: {e}")
        return False


def extraer_titulo(borrador: str) -> str:
    for l in borrador.split("\n"):
        l = l.strip()
        if l.startswith("TÍTULO:"): return l.replace("TÍTULO:","").strip()
    return "Borrador sin título"


# ── HANDLERS ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Hola. Soy *Quilmes Bot* de *Refugio Latinoamericano*.\n\n"
        "🔐 Para continuar ingresá la contraseña de acceso:",
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
    nombre_completo = update.message.text.strip()
    if len(nombre_completo.split()) < 2:
        await update.message.reply_text("Por favor ingresá tu *nombre y apellido completo*.", parse_mode="Markdown")
        return IDENTIFICACION
    context.user_data.update({"nombre": nombre_completo, "respuestas": {}, "fotos": 0, "foto_ids": []})
    await update.message.reply_text(
        f"Perfecto, *{nombre_completo}*.\n\nVoy a hacerte *7 preguntas*. "
        "Podés responder en *texto o audio* en cada una.\n\n"
        "⚠️ _Ningún contenido se publica sin revisión editorial._\n\n¿Empezamos?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["✅ Empezar"]], one_time_keyboard=True, resize_keyboard=True))
    return INICIO


async def handle_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Perfecto. Comenzamos:", reply_markup=ReplyKeyboardRemove())
    context.user_data["estado_pregunta"] = PREGUNTA_QUE
    await update.message.reply_text(PREGUNTAS[PREGUNTA_QUE]["texto"], parse_mode="Markdown")
    return PREGUNTA_QUE


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Transcribe el audio y lo procesa como respuesta de texto."""
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)

    await update.message.reply_text("🎙️ _Transcribiendo audio..._", parse_mode="Markdown")

    voice = update.message.voice or update.message.audio
    texto = await transcribir_audio_groq(voice.file_id, context.bot)

    await update.message.reply_text(
        f"📝 *Transcripción:*\n_{texto}_",
        parse_mode="Markdown")

    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    texto = update.message.text
    if len(texto.split()) < 4:
        await update.message.reply_text("📝 Podés ampliar un poco más. Más detalle mejora el borrador.")
        return estado
    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def avanzar(update, context, estado_actual) -> int:
    idx = ORDEN.index(estado_actual)
    await update.message.reply_text(f"✓ _{idx+1}/7_", parse_mode="Markdown")
    if idx < len(ORDEN)-1:
        siguiente = ORDEN[idx+1]
        context.user_data["estado_pregunta"] = siguiente
        await update.message.reply_text(PREGUNTAS[siguiente]["texto"], parse_mode="Markdown")
        return siguiente
    else:
        context.user_data["estado_pregunta"] = ESPERANDO_FOTOS
        await update.message.reply_text(
            "✅ *¡Las 7 preguntas completas!*\n\n"
            "📸 Enviame *al menos una foto* del hecho.\n"
            "Cuando termines escribí */generar*",
            parse_mode="Markdown")
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    foto = update.message.photo[-1]
    if "foto_ids" not in context.user_data:
        context.user_data["foto_ids"] = []
    context.user_data["foto_ids"].append(foto.file_id)
    context.user_data["fotos"] = len(context.user_data["foto_ids"])
    n = context.user_data["fotos"]
    await update.message.reply_text(
        f"📷 Foto {n} recibida ✓\n_Más fotos o /generar para continuar._",
        parse_mode="Markdown")
    return ESPERANDO_FOTOS


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fotos = context.user_data.get("fotos", 0)
    if fotos == 0:
        await update.message.reply_text("⚠️ Enviá al menos una foto antes de generar.")
        return ESPERANDO_FOTOS

    await update.message.reply_text("⏳ *Generando borrador...*\n_Tarda entre 10 y 20 segundos._", parse_mode="Markdown")

    nombre = context.user_data.get("nombre", "corresponsal")
    borrador = generar_con_groq(context.user_data.get("respuestas", {}), nombre, fotos)
    titulo = extraer_titulo(borrador)

    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])

    await update.message.reply_text(
        f"📧 _Descargando {fotos} foto(s) y enviando al equipo editorial..._",
        parse_mode="Markdown")

    foto_ids = context.user_data.get("foto_ids", [])
    fotos_bytes = await descargar_fotos(foto_ids, context.bot)
    enviado = enviar_con_resend(borrador, nombre, titulo, fotos_bytes)

    if enviado:
        await update.message.reply_text(
            f"✅ *Borrador enviado al equipo editorial.*\n\n"
            f"📎 Fotos adjuntas: {len(fotos_bytes)}\n"
            f"👤 Corresponsal: {nombre}\n\n"
            f"_El equipo lo revisará antes de publicar._\n\n"
            f"_Escribí /start para un nuevo reporte._",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "✅ *Borrador generado.*\n_El equipo editorial lo revisará antes de publicar._\n\n_/start para un nuevo reporte._",
            parse_mode="Markdown")

    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Reporte cancelado. Escribí /start para comenzar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Comandos:*\n\n/start — Iniciar reporte\n/generar — Generar borrador\n"
        "/cancelar — Cancelar\n/ayuda — Este mensaje",
        parse_mode="Markdown")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    texto_h = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto)
    audio_h = MessageHandler(filters.VOICE | filters.AUDIO, handle_audio)
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AUTENTICACION:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autenticacion)],
            IDENTIFICACION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_identificacion)],
            INICIO:           [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inicio)],
            PREGUNTA_QUE:     [texto_h, audio_h],
            PREGUNTA_QUIEN:   [texto_h, audio_h],
            PREGUNTA_CUANDO:  [texto_h, audio_h],
            PREGUNTA_DONDE:   [texto_h, audio_h],
            PREGUNTA_COMO:    [texto_h, audio_h],
            PREGUNTA_POR_QUE: [texto_h, audio_h],
            PREGUNTA_PARA_QUE:[texto_h, audio_h],
            ESPERANDO_FOTOS:  [
                MessageHandler(filters.PHOTO, handle_foto),
                CommandHandler("generar", cmd_generar)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar), CommandHandler("generar", cmd_generar)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("ayuda", ayuda))
    logger.info("Quilmes Bot corriendo con Groq Whisper + fotos adjuntas...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
