"""
QUILMES BOT — bot.py
Bot de reporte periodístico para Refugio Latinoamericano
Listo para desplegar en Railway.app
"""

import os
import logging
import anthropic
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estados
(INICIO, PREGUNTA_QUE, PREGUNTA_QUIEN, PREGUNTA_CUANDO,
 PREGUNTA_DONDE, PREGUNTA_COMO, PREGUNTA_POR_QUE,
 PREGUNTA_PARA_QUE, ESPERANDO_FOTOS, ESPERANDO_VIDEO) = range(10)

PREGUNTAS = {
    PREGUNTA_QUE:      {"texto": "📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central. ¿Qué sucedió exactamente?\n\n_Texto o audio._", "clave": "que"},
    PREGUNTA_QUIEN:    {"texto": "👤 *¿QUIÉNES están involucrados?*\n\nPersonas, organizaciones o comunidades protagonistas.", "clave": "quien"},
    PREGUNTA_CUANDO:   {"texto": "🕐 *¿CUÁNDO ocurrió?*\n\nFecha, hora, y si sigue en curso o terminó.", "clave": "cuando"},
    PREGUNTA_DONDE:    {"texto": "📍 *¿DÓNDE ocurrió?*\n\nPaís, ciudad, barrio, dirección.", "clave": "donde"},
    PREGUNTA_COMO:     {"texto": "🔍 *¿CÓMO ocurrió?*\n\nSecuencia de eventos y circunstancias.", "clave": "como"},
    PREGUNTA_POR_QUE:  {"texto": "💡 *¿POR QUÉ ocurrió?*\n\nCausas, contexto y antecedentes.", "clave": "por_que"},
    PREGUNTA_PARA_QUE: {"texto": "🎯 *¿Cuál es el IMPACTO?*\n\nConsecuencias y relevancia para comunidades migrantes.", "clave": "para_que"},
}

ORDEN = [PREGUNTA_QUE, PREGUNTA_QUIEN, PREGUNTA_CUANDO,
         PREGUNTA_DONDE, PREGUNTA_COMO, PREGUNTA_POR_QUE, PREGUNTA_PARA_QUE]

SYSTEM_PROMPT = """Sos un editor/a periodístico de Refugio Latinoamericano, medio digital argentino especializado en periodismo de migraciones desde una perspectiva de derechos humanos.

ESTILO: personas migrantes como sujetos de derecho, nunca "ilegales", voz activa, párrafos cortos.

ESTRUCTURA OBLIGATORIA:
1. TÍTULO: (máximo 12 palabras)
2. COPETE: (2-3 oraciones)
3. DESARROLLO: (3-5 párrafos)
4. CITA DIRECTA: (entre comillas)
5. CIERRE:
6. VERIFICACIÓN PENDIENTE: (con [VERIFICAR])
7. ETIQUETAS SUGERIDAS:
8. NOTAS PARA EL EDITOR/A:"""


async def transcribir_audio(path: str) -> str:
    try:
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(path, "rb") as f:
            r = client.audio.transcriptions.create(
                model="whisper-1", file=f, language="es",
                prompt="Entrevista periodística sobre migraciones en Argentina"
            )
        return f"{r.text} [transcripto de audio]"
    except Exception as e:
        logger.error(f"Error transcripción: {e}")
        return "[No se pudo transcribir. Respondé en texto por favor.]"


async def generar_borrador(respuestas, nombre, fotos, video) -> str:
    etiquetas = {"que":"QUÉ","quien":"QUIÉN","cuando":"CUÁNDO",
                 "donde":"DÓNDE","como":"CÓMO","por_que":"POR QUÉ","para_que":"IMPACTO"}
    datos = "".join(f"{v}: {respuestas.get(k,'(no especificado)')}\n" for k,v in etiquetas.items())
    user_msg = f"REPORTE:\n{datos}\nPeriodista: {nombre}\nFotos: {fotos}\nVideo: {'Sí' if video else 'No'}\n\nRedactá el borrador completo."
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role":"user","content":user_msg}]
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f"Error Claude: {e}")
        return "❌ Error al generar. Escribí /start para reintentar."


def publicar_wordpress(borrador, periodista) -> str:
    wp_url = os.getenv("WP_URL")
    wp_user = os.getenv("WP_USER")
    wp_pass = os.getenv("WP_APP_PASSWORD")
    if not all([wp_url, wp_user, wp_pass]):
        return None
    try:
        import requests
        from base64 import b64encode
        creds = b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
        titulo = next((l.replace("TÍTULO:","").strip() for l in borrador.split("\n") if l.startswith("TÍTULO:")), "Borrador sin título")
        resp = requests.post(
            f"{wp_url}/wp-json/wp/v2/posts",
            headers={"Authorization":f"Basic {creds}","Content-Type":"application/json"},
            json={"title":titulo,"content":borrador.replace("\n","<br>"),"status":"draft",
                  "meta":{"_quilmes_periodista":periodista,"_quilmes_verificacion":"pendiente"}}
        )
        return resp.json().get("link") if resp.status_code in [200,201] else None
    except Exception as e:
        logger.error(f"Error WP: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data.update({"respuestas":{},"fotos":0,"video":False,
                               "nombre": update.effective_user.first_name or "periodista"})
    await update.message.reply_text(
        f"¡Hola, {context.user_data['nombre']}! 👋\n\n"
        "Soy *Quilmes Bot* de *Refugio Latinoamericano*.\n\n"
        "Voy a hacerte *7 preguntas* para estructurar tu nota. "
        "Podés responder en texto o con audios.\n\n"
        "⚠️ _Ningún contenido se publica sin revisión editorial._\n\n"
        "¿Empezamos?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["✅ Empezar"]], one_time_keyboard=True, resize_keyboard=True)
    )
    return INICIO


async def handle_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Perfecto. Primera pregunta:", reply_markup=ReplyKeyboardRemove())
    context.user_data["estado_pregunta"] = PREGUNTA_QUE
    await update.message.reply_text(PREGUNTAS[PREGUNTA_QUE]["texto"], parse_mode="Markdown")
    return PREGUNTA_QUE


async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = update.message.text
    return await avanzar(update, context, estado)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    await update.message.reply_text("🎙️ Transcribiendo audio...")
    voice = update.message.voice or update.message.audio
    file = await voice.get_file()
    path = f"/tmp/audio_{update.effective_user.id}.ogg"
    await file.download_to_drive(path)
    texto = await transcribir_audio(path)
    try: os.remove(path)
    except: pass
    await update.message.reply_text(f"📝 _{texto}_", parse_mode="Markdown")
    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def avanzar(update, context, estado_actual) -> int:
    idx = ORDEN.index(estado_actual)
    await update.message.reply_text(f"_{idx+1}/7 ✓_", parse_mode="Markdown")
    if idx < len(ORDEN) - 1:
        siguiente = ORDEN[idx + 1]
        context.user_data["estado_pregunta"] = siguiente
        await update.message.reply_text(PREGUNTAS[siguiente]["texto"], parse_mode="Markdown")
        return siguiente
    else:
        context.user_data["estado_pregunta"] = ESPERANDO_FOTOS
        await update.message.reply_text(
            "✅ *¡Las 7 preguntas completas!*\n\n"
            "📸 Enviame *al menos una foto* del hecho.\n"
            "Cuando termines escribí */siguiente*",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["fotos"] = context.user_data.get("fotos", 0) + 1
    n = context.user_data["fotos"]
    await update.message.reply_text(f"📷 Foto {n} recibida ✓  —  _Más fotos o /siguiente_", parse_mode="Markdown")
    return ESPERANDO_FOTOS


async def cmd_siguiente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get("fotos", 0) == 0:
        await update.message.reply_text("⚠️ Enviá al menos una foto antes de continuar.")
        return ESPERANDO_FOTOS
    await update.message.reply_text(
        "🎥 *¿Tenés video?* (opcional)\n\nEnvialo ahora o escribí */generar* para crear el borrador.",
        parse_mode="Markdown"
    )
    return ESPERANDO_VIDEO


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["video"] = True
    await update.message.reply_text("🎥 Video recibido ✓\n\nEscribí */generar* cuando estés listo/a.", parse_mode="Markdown")
    return ESPERANDO_VIDEO


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("⏳ *Generando borrador...*\n_Tarda entre 20 y 40 segundos._", parse_mode="Markdown")
    borrador = await generar_borrador(
        context.user_data.get("respuestas", {}),
        context.user_data.get("nombre", "colaborador/a"),
        context.user_data.get("fotos", 0),
        context.user_data.get("video", False)
    )
    # Enviar en partes si es largo
    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])

    wp_link = publicar_wordpress(borrador, context.user_data.get("nombre",""))
    if wp_link:
        await update.message.reply_text(f"✅ *Borrador en WordPress:*\n{wp_link}", parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ *Borrador generado.*\nEl equipo editorial lo revisará antes de publicar.\n\n_/start para un nuevo reporte_", parse_mode="Markdown")

    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Reporte cancelado. Escribí /start para comenzar de nuevo.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()

    texto_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto)
    audio_handler = MessageHandler(filters.VOICE | filters.AUDIO, handle_audio)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            INICIO:          [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inicio)],
            PREGUNTA_QUE:    [texto_handler, audio_handler],
            PREGUNTA_QUIEN:  [texto_handler, audio_handler],
            PREGUNTA_CUANDO: [texto_handler, audio_handler],
            PREGUNTA_DONDE:  [texto_handler, audio_handler],
            PREGUNTA_COMO:   [texto_handler, audio_handler],
            PREGUNTA_POR_QUE:[texto_handler, audio_handler],
            PREGUNTA_PARA_QUE:[texto_handler, audio_handler],
            ESPERANDO_FOTOS: [MessageHandler(filters.PHOTO, handle_foto), CommandHandler("siguiente", cmd_siguiente)],
            ESPERANDO_VIDEO: [MessageHandler(filters.VIDEO, handle_video), CommandHandler("generar", cmd_generar)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar), CommandHandler("generar", cmd_generar)],
    )

    app.add_handler(conv)
    logger.info("Quilmes Bot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
