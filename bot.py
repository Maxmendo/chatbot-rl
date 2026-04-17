"""
QUILMES BOT — bot.py (versión con Groq + Resend)
"""
import os
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

(INICIO, PREGUNTA_QUE, PREGUNTA_QUIEN, PREGUNTA_CUANDO,
 PREGUNTA_DONDE, PREGUNTA_COMO, PREGUNTA_POR_QUE,
 PREGUNTA_PARA_QUE, ESPERANDO_FOTOS) = range(9)

PREGUNTAS = {
    PREGUNTA_QUE:      {"texto": "📰 *¿QUÉ ocurrió?*\n\nDescribí el hecho central. ¿Qué sucedió exactamente?", "clave": "que"},
    PREGUNTA_QUIEN:    {"texto": "👤 *¿QUIÉNES están involucrados?*\n\nPersonas, organizaciones o comunidades protagonistas.", "clave": "quien"},
    PREGUNTA_CUANDO:   {"texto": "🕐 *¿CUÁNDO ocurrió?*\n\nFecha, hora, y si sigue en curso o terminó.", "clave": "cuando"},
    PREGUNTA_DONDE:    {"texto": "📍 *¿DÓNDE ocurrió?*\n\nPaís, ciudad, barrio, dirección.", "clave": "donde"},
    PREGUNTA_COMO:     {"texto": "🔍 *¿CÓMO ocurrió?*\n\nSecuencia de eventos y circunstancias.", "clave": "como"},
    PREGUNTA_POR_QUE:  {"texto": "💡 *¿POR QUÉ ocurrió?*\n\nCausas, contexto y antecedentes.", "clave": "por_que"},
    PREGUNTA_PARA_QUE: {"texto": "🎯 *¿Cuál es el IMPACTO?*\n\nConsecuencias y relevancia para comunidades migrantes.", "clave": "para_que"},
}

ORDEN = [PREGUNTA_QUE, PREGUNTA_QUIEN, PREGUNTA_CUANDO, PREGUNTA_DONDE, PREGUNTA_COMO, PREGUNTA_POR_QUE, PREGUNTA_PARA_QUE]

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


def generar_con_groq(respuestas: dict, nombre: str, fotos: int) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "❌ Falta GROQ_API_KEY."
    etiquetas = {"que":"QUÉ","quien":"QUIÉN","cuando":"CUÁNDO","donde":"DÓNDE","como":"CÓMO","por_que":"POR QUÉ","para_que":"IMPACTO"}
    datos = "".join(f"{v}: {respuestas.get(k,'(no especificado)')}\n" for k,v in etiquetas.items())
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json={"model":"llama-3.3-70b-versatile","messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":f"REPORTE:\n{datos}\nPeriodista: {nombre}\nFotos: {fotos}\n\nRedactá el borrador completo."}],"max_tokens":2500,"temperature":0.7},
            timeout=60)
        data = r.json()
        if "choices" in data: return data["choices"][0]["message"]["content"]
        return f"❌ Error: {data.get('error',{}).get('message','desconocido')}"
    except Exception as e:
        logger.error(f"Error Groq: {e}")
        return "❌ Error al conectar con la IA."


def enviar_con_resend(borrador: str, nombre: str, titulo: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    editorial_email = os.getenv("EDITORIAL_EMAIL")
    if not api_key or not editorial_email:
        logger.warning("Resend no configurado.")
        return False
    cuerpo = f"BORRADOR PERIODÍSTICO — REFUGIO LATINOAMERICANO\nPendiente de revisión editorial.\n\nPeriodista: {nombre}\n\n{'─'*50}\n\n{borrador}\n\n{'─'*50}\nGenerado por Quilmes Bot. No publicar sin revisión."
    try:
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
            json={"from":"Quilmes Bot <onboarding@resend.dev>","to":[editorial_email],"subject":f"[BORRADOR] {titulo} — {nombre}","text":cuerpo},
            timeout=30)
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    nombre = update.effective_user.first_name or "periodista"
    context.user_data.update({"respuestas":{},"fotos":0,"nombre":nombre})
    await update.message.reply_text(
        f"¡Hola, {nombre}! 👋\n\nSoy *Quilmes Bot* de *Refugio Latinoamericano*.\n\nVoy a hacerte *7 preguntas* para estructurar tu nota. Respondé en texto con el mayor detalle posible.\n\n⚠️ _Ningún contenido se publica sin revisión editorial._\n\n¿Empezamos?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["✅ Empezar"]],one_time_keyboard=True,resize_keyboard=True))
    return INICIO


async def handle_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Perfecto. Comenzamos:", reply_markup=ReplyKeyboardRemove())
    context.user_data["estado_pregunta"] = PREGUNTA_QUE
    await update.message.reply_text(PREGUNTAS[PREGUNTA_QUE]["texto"], parse_mode="Markdown")
    return PREGUNTA_QUE


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🎙️ _Los audios se activarán en la próxima versión._\n\nPor ahora respondé en texto.", parse_mode="Markdown")
    return context.user_data.get("estado_pregunta", PREGUNTA_QUE)


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
        await update.message.reply_text("✅ *¡Las 7 preguntas completas!*\n\n📸 Enviame *al menos una foto* del hecho.\nCuando termines escribí */generar*", parse_mode="Markdown")
        return ESPERANDO_FOTOS


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["fotos"] = context.user_data.get("fotos",0)+1
    n = context.user_data["fotos"]
    await update.message.reply_text(f"📷 Foto {n} recibida ✓\n_Más fotos o /generar para continuar._", parse_mode="Markdown")
    return ESPERANDO_FOTOS


async def cmd_generar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fotos = context.user_data.get("fotos",0)
    if fotos == 0:
        await update.message.reply_text("⚠️ Enviá al menos una foto antes de generar.")
        return ESPERANDO_FOTOS
    await update.message.reply_text("⏳ *Generando borrador...*\n_Tarda entre 10 y 20 segundos._", parse_mode="Markdown")
    nombre = context.user_data.get("nombre","colaborador/a")
    borrador = generar_con_groq(context.user_data.get("respuestas",{}), nombre, fotos)
    titulo = extraer_titulo(borrador)
    for i in range(0, len(borrador), 4000):
        await update.message.reply_text(borrador[i:i+4000])
    await update.message.reply_text("📧 _Enviando al equipo editorial..._", parse_mode="Markdown")
    enviado = enviar_con_resend(borrador, nombre, titulo)
    if enviado:
        await update.message.reply_text(f"✅ *Borrador enviado a:* {os.getenv('EDITORIAL_EMAIL','el equipo')}\n\n_El equipo lo revisará antes de publicar._\n\n_/start para un nuevo reporte._", parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ *Borrador generado.*\n_El equipo editorial lo revisará antes de publicar._\n\n_/start para un nuevo reporte._", parse_mode="Markdown")
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Reporte cancelado. Escribí /start para comenzar de nuevo.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("*Comandos:*\n\n/start — Iniciar reporte\n/generar — Generar borrador\n/cancelar — Cancelar\n/ayuda — Este mensaje", parse_mode="Markdown")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: raise ValueError("Falta TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    texto_h = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto)
    audio_h = MessageHandler(filters.VOICE | filters.AUDIO, handle_audio)
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            INICIO:[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inicio)],
            PREGUNTA_QUE:[texto_h,audio_h], PREGUNTA_QUIEN:[texto_h,audio_h],
            PREGUNTA_CUANDO:[texto_h,audio_h], PREGUNTA_DONDE:[texto_h,audio_h],
            PREGUNTA_COMO:[texto_h,audio_h], PREGUNTA_POR_QUE:[texto_h,audio_h],
            PREGUNTA_PARA_QUE:[texto_h,audio_h],
            ESPERANDO_FOTOS:[MessageHandler(filters.PHOTO,handle_foto),CommandHandler("generar",cmd_generar)],
        },
        fallbacks=[CommandHandler("cancelar",cancelar),CommandHandler("generar",cmd_generar)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("ayuda",ayuda))
    logger.info("Quilmes Bot corriendo con Groq + Resend...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
