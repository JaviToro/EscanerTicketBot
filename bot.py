import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio
import tempfile

from google import genai
import json

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("La variable de entorno TELEGRAM_BOT_TOKEN no está configurada.")
if not GEMINI_API_KEY:
    raise ValueError("La variable de entorno GEMINI_API_KEY no está configurada.")

logging.basicConfig(
    format='%(asctime)s - %(name)s [%(levelname)s] -> %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash" 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía un mensaje de bienvenida al iniciar el bot."""
    await update.message.reply_text('¡Hola! Envíame una foto de tu ticket de restaurante y usaré la inteligencia artificial para extraer la información. Asegúrate de que el texto sea lo más legible posible para obtener los mejores resultados.')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja las fotos enviadas al bot, las descarga, las sube a Gemini y extrae la info."""
    user = update.message.from_user
    logger.info("Foto recibida de %s (%s).", user.first_name, user.id)
    await update.message.reply_text("He recibido tu foto. Dame un momento mientras la IA la analiza...")

    temp_image_file_path = None
    uploaded_file = None

    try:
        file_id = update.message.photo[-1].file_id 
        telegram_file = await context.bot.get_file(file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
            temp_image_file_path = temp_file.name
            await telegram_file.download_to_drive(custom_path=temp_image_file_path)
            logger.info("Imagen descargada localmente a %s", temp_image_file_path)

        uploaded_file = client.files.upload(
            file=temp_image_file_path
        )
        logger.info("Archivo subido a Gemini. ID: %s, URI: %s", uploaded_file.name, uploaded_file.uri)

        prompt_parts = [
            uploaded_file,
            "\n\n",
            "Extrae la siguiente información de este recibo o ticket de restaurante: "
            "1. **Nombre del Restaurante**\n"
            "2. **Fecha** (en formato DD/MM/AAAA)\n"
            "3. **Hora** (en formato HH:MM, si está disponible)\n"
            "4. **Total** (incluyendo la divisa, si está presente, ej. 25.50€ o $25.50)\n"
            "5. Una lista de artículos comprados, donde cada artículo incluya: cantidad (si se especifica, ej. 2), nombre del artículo, precio por unidad (con dos decimales y divisa, ej. 1.50€) y precio total (también con dos decimales y divisa). Si un artículo no tiene precio (ej. Servicios) no lo incluyas.\n"
            "Si un dato no se encuentra o no es aplicable, indica 'No disponible' o una lista vacía para los artículos."
            "Si en el ticket existen varios artículos que son iguales (mismo precio) pero se listan en líneas diferentes, que es algo que ocurre a veces, agrúpalos en uno mismo para que sea más fácil leer el resultado."
            "Formatea la salida como un objeto JSON estricto utilizando el inglés para los nombres de las propiedades y con un formato estándar (restaurant_name, date, time, total, items [quantity, name, unit_price y total_price])."
        ]
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt_parts
        )
        
        reply_text = ""
        try:
            parsed_data_str = response.text
            if parsed_data_str.strip().startswith("```json"):
                parsed_data_str = parsed_data_str.replace("```json\n", "").replace("```", "").strip()

            json_output = json.loads(parsed_data_str)

            logger.info(json_output)

            message_parts = []
            
            restaurant_name = json_output.get("restaurantName", "No disponible")
            date = json_output.get("date", "No disponible")
            time = json_output.get("time", "No disponible")
            total = json_output.get("total", "No disponible")

            message_parts.append(f"🎉 *Resumen del ticket* 🎉\n")
            message_parts.append(f"📍 *Restaurante:* {restaurant_name}")
            message_parts.append(f"🗓️ *Fecha:* {date}")
            if time != "N/A":
                message_parts.append(f"⏰ *Hora:* {time}")
            message_parts.append(f"💰 *Total:* {total}\n")

            items = json_output.get("items", [])
            if items:
                message_parts.append("🍔 *Artículos consumidos:*")
                for item in items:
                    quantity = item.get("quantity", "")
                    name = item.get("name", "N/A")
                    unit_price = item.get("unit_price", "N/A")
                    total_price = item.get("total_price", "N/A")
                    
                    item_str = ""
                    if quantity:
                        item_str += f"{quantity}x "
                    item_str += f"{name}"
                    if total_price != "N/A":
                        item_str += f" *{total_price}*"
                    if unit_price != "N/A":
                        item_str += f" (👤 {(unit_price)} por unidad)"
                    message_parts.append(f"➡️ {item_str}")
            else:
                message_parts.append("No se encontraron artículos detallados.")

            reply_text = "\n".join(message_parts)
            
        except json.JSONDecodeError as e:
            logger.error("Error al parsear la respuesta de Gemini como JSON: %s. Respuesta original: %s", e, response.text)
            reply_text = (
                "Gemini procesó la imagen, pero tuvo problemas para estructurar la información como JSON. "
                "Aquí está la respuesta original:\n\n" + response.text
            )
        except Exception as e:
            logger.error("Error inesperado al procesar la respuesta de Gemini: %s. Respuesta original: %s", e, response.text)
            reply_text = (
                "Hubo un problema al interpretar la respuesta de Gemini. "
                "Respuesta original de Gemini:\n\n" + response.text
            )

        await update.message.reply_text(reply_text, parse_mode='Markdown')

    except Exception as e:
        logger.error("Error general procesando la foto con Gemini Vision (Client API - local file): %s", e)
        await update.message.reply_text("Lo siento, hubo un error al procesar tu foto con Gemini. Asegúrate de que la foto sea un ticket real y con texto legible.")
    finally:
        if temp_image_file_path and os.path.exists(temp_image_file_path):
            os.remove(temp_image_file_path)
            logger.info("Archivo temporal local eliminado: %s", temp_image_file_path)
        
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
                logger.info("Archivo temporal de Gemini eliminado: %s", uploaded_file.name)
            except Exception as e:
                logger.warning("No se pudo eliminar el archivo de Gemini %s: %s", uploaded_file.name, e)


def main() -> None:
    """Inicia el bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo))

    logger.info("Bot iniciado. Escuchando mensajes...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()