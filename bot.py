# chatbot RL con webhook - versión para producción en Render

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

# ========== CONFIGURACIÓN ==========
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Falta TELEGRAM_BOT_TOKEN")

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    logger.warning("RENDER_EXTERNAL_URL no configurada. El webhook podría fallar.")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_PATH = f"/webhook/{TOKEN}"

# ========== AQUÍ VA TODA TU LÓGICA DE NEGOCIO (GÉNEROS, FLUJOS, GROQ, HANDLERS) ==========
# Copia y pega desde tu archivo actual TODO lo que está entre "imports" y el "if __name__ == '__main__'".
# Incluye: estados, GENEROS, FLUJOS, funciones llamar_groq, analizar_respuesta_con_groq,
# transcribir_audio_groq, generar_borrador, construir_resumen, teclado_resumen, teclado_numeros,
# get_mini_app_url, construir_teclado, descargar_fotos, descargar_video, enviar_con_resend,
# extraer_titulo, y todos los async handlers (start, reiniciar, handle_autenticacion, etc.)
# hasta cancelar, handle_foto, handle_video, cmd_generar.

# No olvides incluir el ConversationHandler y agregarlo a application.

# ========== EJEMPLO DE CÓMO AGREGAR EL CONVERSATIONHANDLER (YA DEBE EXISTIR EN TU CÓDIGO) ==========
# Al final de la sección de handlers, debes crear la aplicación y agregar los handlers.
# Como no puedo copiar todo tu código aquí, te indico que mantengas exactamente lo que ya tenías,
# solo que al final reemplazarás el bloque "if __name__ == '__main__'" por el que está más abajo.

# ========== WEBHOOK SETUP ==========
async def health_check(request: Request) -> JSONResponse:
    """Endpoint para que Render verifique que el servicio está vivo."""
    return JSONResponse({"status": "ok"})

async def webhook_endpoint(request: Request) -> PlainTextResponse:
    """Recibe las actualizaciones de Telegram."""
    try:
        body = await request.json()
        update = Update.de_json(body, application.bot)
        await application.process_update(update)
        return PlainTextResponse("", status_code=200)
    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        return PlainTextResponse("", status_code=500)

async def set_webhook():
    """Configura el webhook en Telegram al iniciar."""
    if not RENDER_EXTERNAL_URL:
        logger.error("No se puede configurar webhook: falta RENDER_EXTERNAL_URL")
        return
    webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logger.info(f"Webhook configurado: {webhook_url}")

# ========== INICIO (web server + bot) ==========
if __name__ == "__main__":
    # Crear la aplicación del bot (esto debe estar después de tus definiciones de handlers)
    application = Application.builder().token(TOKEN).build()

    # ========== AQUÍ AGREGA TU CONVERSATIONHANDLER Y TODOS LOS HANDLERS ==========
    # Ejemplo (debes poner tu ConversationHandler real con todos los estados):
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
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            CommandHandler("reiniciar", reiniciar),
            CommandHandler("generar", cmd_generar),
        ],
    )
    application.add_handler(conv_handler)
    # Si tienes otros CommandHandler fuera del conversation, agrégalos también.

    # Configurar webhook asíncrono y servidor
    async def start_app():
        await application.initialize()
        await set_webhook()
        starlette_app = Starlette(routes=[
            Route("/health", health_check, methods=["GET"]),
            Route(WEBHOOK_PATH, webhook_endpoint, methods=["POST"]),
        ])
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(start_app())
