import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import aiohttp
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime, time
import pytz

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота (получи у @BotFather)
BOT_TOKEN = ""

# ID телеграм канала
CHANNEL_ID = 

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Заголовки для запросов к Авито
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

# Глобальные переменные
sent_ads = {}
send_to_channel = False
channel_configured = False

# Переменные для планировщика
scheduled_tasks = {}
search_query_schedule = None
schedule_times = []
schedule_running = False

async def parse_avito(search_query, max_results=5):
    """
    Парсит объявления с Авито по заданному запросу
    """
    base_url = "https://www.avito.ru"
    search_url = f"{base_url}/rossiya?q={search_query}"
    
    results = []
    
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(search_url) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Поиск карточек объявлений
                    items = soup.find_all('div', {'data-marker': 'item'})[:max_results]
                    
                    for item in items:
                        try:
                            # Название
                            title_elem = item.find('h3', {'itemprop': 'name'})
                            title = title_elem.text.strip() if title_elem else 'Название не найдено'
                            
                            # Цена
                            price_elem = item.find('span', {'itemprop': 'price'})
                            price = price_elem.get('content') if price_elem else 'Цена не указана'
                            if price_elem and not price:
                                price = price_elem.text.strip()
                            
                            # Описание
                            desc_elem = item.find('div', class_=re.compile(r'description'))
                            description = desc_elem.text.strip() if desc_elem else 'Описание отсутствует'
                            
                            # Ссылка
                            link_elem = item.find('a', {'data-marker': 'item-title'})
                            link = base_url + link_elem.get('href') if link_elem else 'Ссылка не найдена'
                            
                            # Местоположение
                            location_elem = item.find('div', class_=re.compile(r'geo'))
                            location = location_elem.text.strip() if location_elem else 'Местоположение не указано'
                            
                            # Изображения
                            images = []
                            
                            # Способ 1: Поиск в data-marker
                            image_elems = item.find_all('img')
                            for img in image_elems:
                                src = img.get('src') or img.get('data-src')
                                if src and 'http' in src and not src.endswith('.gif'):
                                    # Преобразование маленьких превью в нормальные изображения
                                    if '64x48' in src:
                                        src = src.replace('64x48', '640x480')
                                    elif '50x37' in src:
                                        src = src.replace('50x37', '500x375')
                                    images.append(src)
                            
                            # Способ 2: Поиск в JSON данных
                            script_data = item.find('script', type='application/ld+json')
                            if script_data:
                                try:
                                    json_data = json.loads(script_data.string)
                                    if isinstance(json_data, list):
                                        json_data = json_data[0]
                                    if 'image' in json_data:
                                        if isinstance(json_data['image'], str):
                                            images.append(json_data['image'])
                                        elif isinstance(json_data['image'], list):
                                            images.extend(json_data['image'][:3])
                                except:
                                    pass
                            
                            # Убираем дубликаты и ограничиваем количество изображений
                            images = list(dict.fromkeys(images))[:3]
                            
                            # Создаем уникальный ID объявления
                            ad_id = hash(link)
                            
                            results.append({
                                'id': ad_id,
                                'title': title,
                                'price': price,
                                'description': description[:200] + '...' if len(description) > 200 else description,
                                'link': link,
                                'location': location,
                                'images': images
                            })
                            
                        except Exception as e:
                            logger.error(f"Ошибка при парсинге элемента: {e}")
                            continue
                            
                else:
                    logger.error(f"Ошибка HTTP: {response.status}")
                    
    except Exception as e:
        logger.error(f"Ошибка при запросе к Авито: {e}")
    
    return results

async def send_ad_to_channel(ad_data):
    """
    Отправляет объявление в телеграм канал
    """
    global CHANNEL_ID
    
    if not CHANNEL_ID:
        return False
        
    try:
        result_text = f"""
🏷️ <b>Название:</b> {ad_data['title']}
💰 <b>Цена:</b> {ad_data['price']}
📍 <b>Местоположение:</b> {ad_data['location']}
📝 <b>Описание:</b> {ad_data['description']}
🔗 <a href="{ad_data['link']}">Ссылка на объявление</a>

#объявление #avito
        """
        
        # Если есть изображения, отправляем их как медиа-группу
        if ad_data['images']:
            try:
                # Создаем медиа-группу
                media = []
                for j, image_url in enumerate(ad_data['images'][:3]):
                    if j == 0:
                        media.append(types.InputMediaPhoto(
                            media=image_url,
                            caption=result_text,
                            parse_mode='HTML'
                        ))
                    else:
                        media.append(types.InputMediaPhoto(media=image_url))
                
                await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
                logger.info(f"Объявление отправлено в канал: {ad_data['title']}")
                return True
                
            except Exception as e:
                logger.error(f"Ошибка при отправке изображений в канал: {e}")
                await bot.send_message(
                    chat_id=CHANNEL_ID, 
                    text=result_text, 
                    parse_mode='HTML', 
                    disable_web_page_preview=False
                )
                return True
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID, 
                text=result_text, 
                parse_mode='HTML', 
                disable_web_page_preview=False
            )
            logger.info(f"Объявление отправлено в канал: {ad_data['title']}")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при отправке в канал: {e}")
        return False

async def check_channel_permissions():
    """
    Проверяет права бота в канале
    """
    global CHANNEL_ID, channel_configured
    
    if not CHANNEL_ID:
        return False
        
    try:
        chat_member = await bot.get_chat_member(CHANNEL_ID, (await bot.get_me()).id)
        if chat_member.status in ['administrator', 'creator']:
            channel_configured = True
            return True
        else:
            logger.error(f"Бот не является администратором канала {CHANNEL_ID}!")
            return False
    except Exception as e:
        logger.error(f"Ошибка при проверке прав бота в канале: {e}")
        return False

async def send_scheduled_ad(ad_index, total_ads, target_time):
    """
    Отправляет одно объявление по расписанию
    """
    global search_query_schedule, sent_ads
    
    try:
        # Парсим объявления
        results = await parse_avito(search_query_schedule, max_results=total_ads)
        
        if results and ad_index < len(results):
            ad = results[ad_index]
            
            # Отправляем в канал
            if ad['id'] not in sent_ads:
                success = await send_ad_to_channel(ad)
                if success:
                    sent_ads[ad['id']] = True
                    logger.info(f"Объявление {ad_index + 1} отправлено по расписанию в {target_time}: {ad['title']}")
                    
                    # Отправляем уведомление в ЛС
                    admin_id = 123456789  # Замените на ваш ID
                    await bot.send_message(
                        admin_id,
                        f"✅ Объявление {ad_index + 1} отправлено в {target_time}\n"
                        f"🏷️ {ad['title']}\n"
                        f"💰 {ad['price']}"
                    )
            else:
                logger.info(f"Объявление {ad_index + 1} уже было отправлено ранее")
                
        else:
            logger.error(f"Не удалось получить объявление {ad_index + 1} для отправки")
            
    except Exception as e:
        logger.error(f"Ошибка при отправке объявления по расписанию: {e}")

async def schedule_daily_tasks():
    """
    Создает ежедневные задачи для отправки объявлений по расписанию
    """
    global schedule_times, scheduled_tasks
    
    # Останавливаем предыдущие задачи
    await stop_scheduled_tasks()
    
    for i, schedule_time in enumerate(schedule_times):
        # Создаем задачу для каждого времени
        task = asyncio.create_task(schedule_ad_task(i, schedule_time))
        scheduled_tasks[i] = task
        logger.info(f"Задача {i+1} создана для времени {schedule_time}")

async def schedule_ad_task(ad_index, target_time_str):
    """
    Задача для отправки конкретного объявления в указанное время
    """
    while schedule_running:
        try:
            now = datetime.now(pytz.timezone('Europe/Moscow'))
            target_time = datetime.strptime(target_time_str, '%H:%M').time()
            
            # Создаем datetime объект для сегодня с целевым временем
            target_datetime = datetime.combine(now.date(), target_time)
            target_datetime = pytz.timezone('Europe/Moscow').localize(target_datetime)
            
            # Если время уже прошло сегодня, планируем на завтра
            if now > target_datetime:
                target_datetime += timedelta(days=1)
            
            # Вычисляем время ожидания
            wait_seconds = (target_datetime - now).total_seconds()
            
            if wait_seconds > 0:
                logger.info(f"Ожидание {wait_seconds} секунд до отправки объявления {ad_index + 1} в {target_time_str}")
                await asyncio.sleep(wait_seconds)
                
                if schedule_running:
                    await send_scheduled_ad(ad_index, len(schedule_times), target_time_str)
            
            # Ждем до следующего дня
            await asyncio.sleep(60)  # Проверяем каждую минуту
            
        except Exception as e:
            logger.error(f"Ошибка в задаче расписания: {e}")
            await asyncio.sleep(60)

async def stop_scheduled_tasks():
    """
    Останавливает все запланированные задачи
    """
    global scheduled_tasks, schedule_running
    
    schedule_running = False
    
    for task_id, task in scheduled_tasks.items():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    scheduled_tasks.clear()
    logger.info("Все запланированные задачи остановлены")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """
    Обработчик команды /start
    """
    welcome_text = """
🤖 Привет! Я бот для поиска объявлений на Авито.

Просто отправь мне что хочешь найти, например:
• "iPhone 13"
• "квартира Москва"
• "автомобиль б/у"

Я найду актуальные объявления с фотографиями и покажу тебе!

📢 Для работы с каналом используйте команды:
/set_channel - настроить канал
/add_to_channel - включить отправку
/stop_channel - выключить отправку
/channel_status - статус канала

⏰ Команды для расписания:
/set_schedule - установить расписание отправки
/start_schedule - запустить расписание
/stop_schedule - остановить расписание
/schedule_status - статус расписания
    """
    await message.answer(welcome_text)

@dp.message(Command("set_schedule"))
async def cmd_set_schedule(message: Message):
    """
    Установка расписания отправки объявлений
    """
    global search_query_schedule, schedule_times
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ Укажите поисковый запрос и времена после команды:\n"
            "/set_schedule 'поисковый запрос' время1 время2 ...\n\n"
            "Пример:\n"
            "/set_schedule 'iPhone 13' 09:00 12:00 15:00 18:00 21:00\n\n"
            "⏰ Укажите 5 времен для 5 объявлений (формат ЧЧ:ММ)"
        )
        return
    
    parts = message.text.split()
    if len(parts) < 7:  # команда + запрос + 5 времен
        await message.answer("❌ Укажите 5 времен для 5 объявлений!")
        return
    
    # Извлекаем поисковый запрос (может содержать пробелы)
    query_parts = parts[1:-5]
    search_query_schedule = ' '.join(query_parts)
    
    # Извлекаем времена
    schedule_times = parts[-5:]
    
    # Проверяем формат времен
    valid_times = []
    for time_str in schedule_times:
        try:
            datetime.strptime(time_str, '%H:%M')
            valid_times.append(time_str)
        except ValueError:
            await message.answer(f"❌ Неверный формат времени: {time_str}. Используйте ЧЧ:ММ")
            return
    
    if len(valid_times) != 5:
        await message.answer("❌ Нужно указать 5 корректных времен!")
        return
    
    schedule_times = valid_times
    
    await message.answer(
        f"✅ Расписание установлено!\n\n"
        f"🔍 Поисковый запрос: {search_query_schedule}\n"
        f"⏰ Времена отправки:\n"
        f"1. {schedule_times[0]} - Объявление 1\n"
        f"2. {schedule_times[1]} - Объявление 2\n"
        f"3. {schedule_times[2]} - Объявление 3\n"
        f"4. {schedule_times[3]} - Объявление 4\n"
        f"5. {schedule_times[4]} - Объявление 5\n\n"
        f"Для запуска используйте /start_schedule"
    )

@dp.message(Command("start_schedule"))
async def cmd_start_schedule(message: Message):
    """
    Запуск расписания
    """
    global schedule_running, search_query_schedule, schedule_times
    
    if not search_query_schedule or not schedule_times:
        await message.answer("❌ Сначала установите расписание командой /set_schedule")
        return
    
    if not channel_configured:
        await message.answer("❌ Сначала настройте канал командой /set_channel")
        return
    
    schedule_running = True
    await schedule_daily_tasks()
    
    await message.answer(
        f"✅ Расписание запущено!\n\n"
        f"🔍 Поисковый запрос: {search_query_schedule}\n"
        f"⏰ Отправка по расписанию:\n"
        + "\n".join([f"{i+1}. {time} - Объявление {i+1}" for i, time in enumerate(schedule_times)])
    )

@dp.message(Command("stop_schedule"))
async def cmd_stop_schedule(message: Message):
    """
    Остановка расписания
    """
    global schedule_running
    
    await stop_scheduled_tasks()
    await message.answer("⏹️ Расписание остановлено!")

@dp.message(Command("schedule_status"))
async def cmd_schedule_status(message: Message):
    """
    Статус расписания
    """
    global schedule_running, search_query_schedule, schedule_times
    
    status_text = "📊 Статус расписания:\n\n"
    
    if schedule_running:
        status_text += "🟢 Статус: Запущено\n"
    else:
        status_text += "🔴 Статус: Остановлено\n"
    
    if search_query_schedule:
        status_text += f"🔍 Запрос: {search_query_schedule}\n"
    else:
        status_text += "🔍 Запрос: Не установлен\n"
    
    if schedule_times:
        status_text += "⏰ Расписание:\n"
        for i, time in enumerate(schedule_times):
            status_text += f"{i+1}. {time} - Объявление {i+1}\n"
    else:
        status_text += "⏰ Расписание: Не установлено\n"
    
    await message.answer(status_text)

# ... остальные команды (set_channel, channel_status, add_to_channel, stop_channel, help) остаются без изменений

@dp.message(Command("set_channel"))
async def cmd_set_channel(message: Message):
    """
    Настройка канала
    """
    global CHANNEL_ID
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ Укажите ID канала после команды:\n"
            "/set_channel @username_канала\n"
            "или\n"
            "/set_channel -1001234567890\n\n"
            "📝 Как получить ID канала:\n"
            "1. Для публичного канала: @username\n"
            "2. Для приватного канала: добавьте @username_to_id_bot в канал и получите цифровой ID"
        )
        return
    
    channel_id = message.text.split()[1].strip()
    CHANNEL_ID = channel_id
    
    # Проверяем права
    if await check_channel_permissions():
        await message.answer(f"✅ Канал настроен: {CHANNEL_ID}\nБот имеет необходимые права!")
    else:
        await message.answer(
            f"❌ Не удалось настроить канал {CHANNEL_ID}\n"
            "Убедитесь, что:\n"
            "1. Бот добавлен в канал как администратор\n"
            "2. У бота есть права на отправку сообщений\n"
            "3. ID канала указан правильно"
        )

@dp.message(Command("channel_status"))
async def cmd_channel_status(message: Message):
    """
    Показать статус канала
    """
    global CHANNEL_ID, send_to_channel, channel_configured
    
    status_text = f"""
📊 Статус канала:

🆔 ID канала: {CHANNEL_ID or 'Не настроен'}
⚙️ Настроен: {'✅ Да' if channel_configured else '❌ Нет'}
📤 Отправка: {'✅ Включена' if send_to_channel else '❌ Выключена'}
    """
    
    await message.answer(status_text)

@dp.message(Command("add_to_channel"))
async def cmd_add_to_channel(message: Message):
    """
    Включить отправку объявлений в канал
    """
    global send_to_channel, channel_configured
    
    if not CHANNEL_ID or not channel_configured:
        await message.answer(
            "❌ Сначала настройте канал командой /set_channel\n"
            "Убедитесь, что бот добавлен в канал как администратор!"
        )
        return
    
    send_to_channel = True
    await message.answer("✅ Отправка объявлений в канал включена!")

@dp.message(Command("stop_channel"))
async def cmd_stop_channel(message: Message):
    """
    Выключить отправку объявлений в канал
    """
    global send_to_channel
    send_to_channel = False
    await message.answer("❌ Отправка объявлений в канал выключена!")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """
    Обработчик команды /help
    """
    help_text = """
📋 Как пользоваться ботом:

1. Просто напиши что хочешь найти
2. Я поищу объявления на Авито
3. Покажу тебе результаты с фотографиями

Примеры запросов:
• "ноутбук asus"
• "работа программист"
• "диван"

📢 Команды для работы с каналом:
/set_channel - настроить канал
/add_to_channel - включить отправку в канал
/stop_channel - выключить отправку в канал
/channel_status - статус канала

⏰ Команды для расписания:
/set_schedule - установить расписание отправки
/start_schedule - запустить расписание  
/stop_schedule - остановить расписание
/schedule_status - статус расписания

⚠️ Бот показывает только первые несколько объявлений
    """
    await message.answer(help_text)

@dp.message()
async def handle_search(message: Message):
    """
    Обработчик поисковых запросов
    """
    search_query = message.text.strip()
    
    if not search_query:
        await message.answer("Пожалуйста, введите поисковый запрос")
        return
    
    # Отправляем сообщение о начале поиска
    wait_msg = await message.answer(f"🔍 Ищу объявления по запросу: '{search_query}'...")
    
    try:
        # Парсим объявления
        results = await parse_avito(search_query, max_results=5)
        
        if not results:
            await message.answer("❌ По вашему запросу ничего не найдено. Попробуйте изменить запрос.")
            return
        
        # Отправляем результаты пользователю
        sent_to_channel_count = 0
        for i, item in enumerate(results, 1):
            result_text = f"""
📋 Объявление {i}:

🏷️ <b>Название:</b> {item['title']}
💰 <b>Цена:</b> {item['price']}
📍 <b>Местоположение:</b> {item['location']}
📝 <b>Описание:</b> {item['description']}
🔗 <a href="{item['link']}">Ссылка на объявление</a>
            """
            
            # Отправляем пользователю
            if item['images']:
                try:
                    media = []
                    for j, image_url in enumerate(item['images'][:3]):
                        if j == 0:
                            media.append(types.InputMediaPhoto(
                                media=image_url,
                                caption=result_text,
                                parse_mode='HTML'
                            ))
                        else:
                            media.append(types.InputMediaPhoto(media=image_url))
                    
                    await message.answer_media_group(media=media)
                    
                except Exception as e:
                    logger.error(f"Ошибка при отправке изображений: {e}")
                    await message.answer(result_text, parse_mode='HTML', disable_web_page_preview=True)
            else:
                await message.answer(result_text, parse_mode='HTML', disable_web_page_preview=True)
            
            # Отправляем в канал если включено
            if send_to_channel and channel_configured:
                if item['id'] not in sent_ads:
                    success = await send_ad_to_channel(item)
                    if success:
                        sent_ads[item['id']] = True
                        sent_to_channel_count += 1
                        logger.info(f"Объявление добавлено в канал: {item['title']}")
            
            await asyncio.sleep(1)
        
        # Финальное сообщение
        channel_info = ""
        if send_to_channel and channel_configured:
            channel_info = f"\n📢 В канал отправлено: {sent_to_channel_count} объявлений"
        elif send_to_channel and not channel_configured:
            channel_info = "\n❌ Канал не настроен (используйте /set_channel)"
        
        await message.answer(
            f"✅ Найдено {len(results)} объявлений.{channel_info}\n"
            f"Для нового поиска просто введите запрос!"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}")
        await message.answer("❌ Произошла ошибка при поиске. Попробуйте позже.")
    
    # Удаляем сообщение о ожидании
    await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)

async def main():
    """
    Главная функция
    """
    logger.info("Бот запущен!")
    
    # Проверяем настройки канала если он указан
    if CHANNEL_ID:
        await check_channel_permissions()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
