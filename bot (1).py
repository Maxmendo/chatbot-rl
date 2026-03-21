"""
QUILMES BOT — bot.py (versión sin Whisper)
Bot de reporte periodístico para Refugio Latinoamericano
Solo requiere TELEGRAM_BOT_TOKEN y ANTHROPIC_API_KEY
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
    PREGUNTA_QUE:      {"texto": "📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central. ¿Qué sucedió exactamente?", "clave": "que"},
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
1. TÍTULO: (máximo 12 palabras, sin punto final)
2. COPETE: (2-3 oraciones)
3. DESARROLLO: (3-5 párrafos)
4. CITA DIRECTA: (entre comillas)
5. CIERRE:
6. VERIFICACIÓN PENDIENTE: (con [VERIFICAR])
7. ETIQUETAS SUGERIDAS:
8. NOTAS PARA EL EDITOR/A:"""


async def generar_borrador(respuestas, nombre, fotos) -> str:
    etiquetas = {
        "que": "QUÉ", "quien": "QUIÉN", "cuando": "CUÁNDO",
        "donde": "DÓNDE", "como": "CÓMO", "por_que": "POR QUÉ", "para_que": "IMPACTO"
    }
    datos = "".join(f"{v}: {respuestas.get(k,'(no especificado)')}\n" for k, v in etiquetas.items())
    user_msg = (
        f"REPORTE:\n{datos}\n"
        f"Periodista: {nombre}\n"
        f"Fotos: {fotos}\n\n"
        "Redactá el borrador periodístico completo."
    )
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}]
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f"Error Claude: {e}")
        return "❌ Error al generar el borrador. Escribí /start para reintentar."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    nombre = update.effective_user.first_name or "periodista"
    context.user_data.update({"respuestas": {}, "fotos": 0, "nombre": nombre})
    await update.message.reply_text(
        f"¡Hola, {nombre}! 👋\n\n"
        "Soy *Quilmes Bot* de *Refugio Latinoamericano*.\n\n"
        "Voy a hacerte *7 preguntas* para estructurar tu nota desde el campo. "
        "Respondé cada una en texto con el mayor detalle posible.\n\n"
        "⚠️ _Ningún contenido se publica sin revisión editorial._\n\n"
        "¿Empezamos?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ Empezar"]],
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )
    return INICIO


async def handle_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Perfecto. Comenzamos:",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["estado_pregunta"] = PREGUNTA_QUE
    await update.message.reply_text(PREGUNTAS[PREGUNTA_QUE]["texto"], parse_mode="Markdown")
    return PREGUNTA_QUE


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Avisa que los audios no están disponibles en esta versión."""
    await update.message.reply_text(
        "🎙️ _Los audios se activarán en la próxima versión._\n\n"
        "Por ahora respondé la pregunta en texto, por favor.",
        parse_mode="Markdown"
    )
    return context.user_data.get("estado_pregunta", PREGUNTA_QUE)


async def handle_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    estado = context.user_data.get("estado_pregunta", PREGUNTA_QUE)
    texto = update.message.text

    # Respuesta muy corta — pedir más detalle
    if len(texto.split()) < 4:
        await update.message.reply_text(
            "📝 Podés ampliar un poco más. Más detalle ayuda a generar una mejor nota."
        )
        return estado

    context.user_data["respuestas"][PREGUNTAS[estado]["clave"]] = texto
    return await avanzar(update, context, estado)


async def avanzar(update, context, estado_actual) -> int:
    idx = ORDEN.index(estado_actual)
    await update.message.reply_text(f"✓ _{idx + 1}/7_", parse_mode="Markdown")

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
            "Cuando termines escribí */generar*",
            parse_mode="Markdown"
        )
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["fotos"] = context.user_data.get("fotos", 0) + 1
    n = context.user_data["fotos"]
    await update.message.reply_text(
        f"📷 Foto {n} recibida ✓\n_Más fotos o /generar para continuar._",
        parse_mode="Markdown"
    )
    return ESPERANDO_FOTOS


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fotos = context.user_data.get("fotos", 0)
    if fotos == 0:
        await update.message.reply_text("⚠️ Enviá al menos una foto antes de generar el borrador.")
        return ESPERANDO_FOTOS

    await update.message.reply_text(
        "⏳ *Generando borrador...*\n_Tarda entre 20 y 40 segundos._",
        parse_mode="Markdown"
    )

    borrador = await generar_borrador(
        context.user_data.get("respuestas", {}),
        context.user_data.get("nombre", "colaborador/a"),
        fotos
    )

    # Enviar en partes si supera el límite de Telegram
    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i + 4000])

    await update.message.reply_text(
        "✅ *Borrador generado.*\n\n"
        "El equipo editorial lo revisará antes de publicar.\n\n"
        "_Escribí /start para un nuevo reporte._",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Reporte cancelado. Escribí /start para comenzar de nuevo.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Comandos:*\n\n"
        "/start — Iniciar un nuevo reporte\n"
        "/generar — Generar el borrador (después de las fotos)\n"
        "/cancelar — Cancelar el reporte actual\n"
        "/ayuda — Ver este mensaje",
        parse_mode="Markdown"
    )


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta la variable TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()

    texto_h = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto)
    audio_h = MessageHandler(filters.VOICE | filters.AUDIO, handle_audio)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
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
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            CommandHandler("generar", cmd_generar),
        ],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("ayuda", ayuda))

    logger.info("Quilmes Bot corriendo (versión sin audio)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
