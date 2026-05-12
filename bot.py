import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
import uvicorn
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("Falta TELEGRAM_BOT_TOKEN")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8080))

# Handler simple
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola! El bot funciona con webhook.")

# Endpoints
async def health(request: Request):
    return JSONResponse({"status": "ok"})

async def webhook(request: Request):
    try:
        body = await request.json()
        update = Update.de_json(body, application.bot)
        await application.process_update(update)
        return PlainTextResponse("", status_code=200)
    except Exception as e:
        logger.error(f"Error: {e}")
        return PlainTextResponse("", status_code=500)

if __name__ == "__main__":
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    async def start_app():
        await application.initialize()
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook/{TOKEN}"
        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"Webhook configurado en {webhook_url}")
        app = Starlette(routes=[
            Route("/health", health, methods=["GET"]),
            Route("/webhook/{token}", webhook, methods=["POST"]),
        ])
        config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(start_app())
