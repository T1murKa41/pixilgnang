import config
import logging
import json
import os
import uuid
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import IDFilter, ChatTypeFilter, IsReplyFilter, Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.handler import CancelHandler
from PIL import Image, ImageDraw, ImageFont
import io

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.API_TOKEN, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())
conn = sqlite3.connect('pghub.db')
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, 
    is_admin INTEGER DEFAULT 0, 
    is_banned INTEGER DEFAULT 0, 
    sender_name TEXT DEFAULT NULL
)
''')
cursor.execute(
    'CREATE TABLE IF NOT EXISTS storage (channel TEXT, last_send_time TEXT)'
)
cursor.execute('''
CREATE TABLE IF NOT EXISTS memes (
    meme_cache_id TEXT PRIMARY KEY,
    message_id INTEGER,
    from_user_id INTEGER,
    from_user_full_name TEXT,
    forwarded_message_id INTEGER
)
''')
conn.commit()


class Form(StatesGroup):
    post = State()
    meme = State()
    album = State()
    description = State()


class UserCheckMiddleware(BaseMiddleware):
    def __init__(self, db_cursor):
        super(UserCheckMiddleware, self).__init__()
        self.cursor = db_cursor

    async def on_process_message(self, message: types.Message, data: dict):
        user_id = message.from_user.id
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name if message.from_user.last_name else ""
        sender_name = f"{first_name} {last_name}".strip()

        # Проверка на бан пользователя
        self.cursor.execute('SELECT is_banned FROM users WHERE id = ?', (user_id,))
        user = self.cursor.fetchone()

        if user and user[0] == 1:
            await message.reply('🛑 *Вы заблокированы в боте!*', parse_mode='markdown')
            raise CancelHandler()

        # Обновление имени пользователя
        if user:
            self.cursor.execute('UPDATE users SET sender_name = ? WHERE id = ?', (sender_name, user_id))
        
        conn.commit()

dp.middleware.setup(UserCheckMiddleware(cursor))


@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    # Создаем разметку клавиатуры с кнопками "Отправить мем" и "Отправить сообщение"
    reply_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    )

    # Создаем разметку кнопок с каналами
    channel_buttons = InlineKeyboardMarkup()
    pixelgang_button = InlineKeyboardButton('📱 pixelgang', url='https://t.me/pixelgang')
    pocobytes_button = InlineKeyboardButton('📞 pocobytes', url='https://t.me/pocobytes')
    channel_buttons.add(pixelgang_button, pocobytes_button)

    user_id = message.from_user.id
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name if message.from_user.last_name else ""
    sender_name = f"{first_name} {last_name}".strip()

    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    if not user:
        # Если пользователь новый, добавляем его в базу данных
        cursor.execute('INSERT INTO users (id, is_admin, is_banned, sender_name) VALUES (?, 0, 0, ?)',
                       (user_id, sender_name))
        conn.commit()
        # Отправляем приветственное сообщение о регистрации с кнопками
        await message.reply('👋 *Привет*, вы зарегистрированы в боте предложки и поддержки каналов @pixelgang & @pocobytes!',
                            reply_markup=reply_markup, parse_mode='markdown')
        # Отправляем кнопки с каналами
        await message.answer('📢 Вы также можете присоединиться к нашим каналам:', reply_markup=channel_buttons)
        # Отправляем обычное приветственное сообщение
    else:
        # Если пользователь уже существует в базе данных, просто отправляем ему приветственное сообщение
        await message.answer('👋 *Привет*, это бот предложки и поддержки каналов @pixelgang & @pocobytes',
                             parse_mode='markdown', reply_markup=channel_buttons)


@dp.message_handler(IDFilter(chat_id=config.admin_chat), commands=['ban'])
async def ban_command(message: types.Message):
    admin_id = message.from_user.id

    cursor.execute('SELECT * FROM users WHERE id = ?', (admin_id,))
    admin = cursor.fetchone()
    if not admin or admin[1] != 1:
        await message.reply("⚠️ У вас нет прав для выполнения этой команды.")
        return

    bann_user_id = None

    if message.reply_to_message:
        if message.reply_to_message.forward_from:
            bann_user_id = message.reply_to_message.forward_from.id
        elif message.reply_to_message.from_user and not message.reply_to_message.from_user.is_bot:
            bann_user_id = message.reply_to_message.from_user.id
        elif message.reply_to_message.forward_sender_name:
            sender_name = message.reply_to_message.forward_sender_name
            cursor.execute('SELECT id FROM users WHERE sender_name = ?', (sender_name,))
            user_data = cursor.fetchone()
            if user_data:
                bann_user_id = user_data[0]
            else:
                await message.reply('⚠️ Пользователь с таким анонимным именем не найден.')
                return
        else:
            await message.reply('⚠️ Невозможно забанить анонимного пользователя, воспользуйтесь баном по ID.')
            return
    elif message.get_args():
        try:
            bann_user_id = int(message.get_args())
        except ValueError:
            await message.reply('⚠️ Указанный ID должен быть числом.')
            return
    else:
        await message.reply('⚠️ Команда требует указания пользователя через пересланное сообщение или его ID в аргументах.')
        return

    if bann_user_id == config.bot_id:
        await message.reply('⚠️ Я не могу забанить сам себя.')
        return

    if bann_user_id == admin_id:
        await message.reply("⚠️ Вы не можете забанить себя.")
        return

    cursor.execute('SELECT * FROM users WHERE id = ?', (bann_user_id,))
    user = cursor.fetchone()
    if not user:
        await message.reply(f'⚠️ Пользователь с ID: `{bann_user_id}` не найден в базе данных.', parse_mode='markdown')
        return
    if user[2] == 1:
        await message.reply(f'⚠️ Пользователь с ID: `{bann_user_id}` уже забанен.', parse_mode='markdown')
        return

    cursor.execute('UPDATE users SET is_banned = 1 WHERE id = ?', (bann_user_id,))
    conn.commit()

    await message.reply(f"🛑 Пользователь с ID: `{bann_user_id}` заблокирован", parse_mode='markdown')
    await bot.send_message(config.logs_channel, f"#BAN\n\nID: {bann_user_id}")


@dp.message_handler(IDFilter(chat_id=config.admin_chat), commands=['unban'])
async def unban_command(message: types.Message):
    admin_id = message.from_user.id

    cursor.execute('SELECT * FROM users WHERE id = ?', (admin_id,))
    admin = cursor.fetchone()
    if not admin or admin[1] != 1:
        await message.reply("⚠️ У вас нет прав для выполнения этой команды.")
        return

    unban_user_id = None

    if message.reply_to_message:
        if message.reply_to_message.forward_from:
            unban_user_id = message.reply_to_message.forward_from.id
        elif message.reply_to_message.from_user and not message.reply_to_message.from_user.is_bot:
            unban_user_id = message.reply_to_message.from_user.id
        elif message.reply_to_message.forward_sender_name:
            sender_name = message.reply_to_message.forward_sender_name
            cursor.execute('SELECT id FROM users WHERE sender_name = ?', (sender_name,))
            user_data = cursor.fetchone()
            if user_data:
                unban_user_id = user_data[0]
            else:
                await message.reply('⚠️ Пользователь с таким анонимным именем не найден.')
                return
        else:
            await message.reply('⚠️ Невозможно разблокировать анонимного пользователя.')
            return
    elif message.get_args():
        try:
            unban_user_id = int(message.get_args())
        except ValueError:
            await message.reply('⚠️ Указанный ID должен быть числом.')
            return
    else:
        await message.reply('⚠️ Команда требует указания пользователя через пересланное сообщение или его ID в аргументах.')
        return

    if unban_user_id == config.bot_id:
        await message.reply('⚠️ Это действие неприменимо к ботам.')
        return

    cursor.execute('SELECT * FROM users WHERE id = ?', (unban_user_id,))
    user = cursor.fetchone()
    if not user:
        await message.reply(f'⚠️ Пользователь с ID: `{unban_user_id}` не найден в базе данных.', parse_mode='markdown')
        return
    if user[2] == 0:
        await message.reply(f'⚠️ Пользователь с ID: `{unban_user_id}` уже разблокирован.', parse_mode='markdown')
        return

    cursor.execute('UPDATE users SET is_banned = 0 WHERE id = ?', (unban_user_id,))
    conn.commit()

    await message.reply(f"✅ Пользователь с ID: `{unban_user_id}` разблокирован", parse_mode='markdown')
    await bot.send_message(config.logs_channel, f"#UNBAN\n\nID: {unban_user_id}")


@dp.message_handler(ChatTypeFilter('private'), Text('Отправить сообщение'))
@dp.message_handler(commands=['send'], state='*')
async def start_send(message: types.Message):
    await Form.post.set()
    await message.reply('ℹ️ Отправьте ваше сообщение, которое хотите переслать админам.', reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
        types.KeyboardButton('Отмена')))


@dp.message_handler(Text('Отмена'), state=Form.post)
async def cancel_send(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer('♿️ Отменено.', reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    ))


@dp.message_handler(state=Form.post, content_types=types.ContentType.ANY)
async def send_to_admin(message: types.Message, state: FSMContext):
    if message.text not in {'/start', '/help', '/send'}:
        await bot.forward_message(chat_id=config.admin_chat, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer('✅ Ваше сообщение отправлено админам.',
                         reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    ))
        await state.finish()
    else:
        await message.answer('ℹ️ Отправьте ваше сообщение.')



@dp.message_handler(ChatTypeFilter('private'), Text('Отправить мем'))
@dp.message_handler(ChatTypeFilter('private'), commands=['sendmeme'])
async def sendmeme(message: types.Message):
    await message.answer('ℹ️ Отправьте ваши медиа и нажмите "Далее" для завершения.',
                         reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
        types.KeyboardButton('Далее'),
        types.KeyboardButton('Отмена')))
    await Form.album.set()


@dp.message_handler(Text('Отмена'), state=Form.album)
async def cancel_album(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer('♿️ Отменено.', reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    ))


@dp.message_handler(Text('Далее'), state=Form.album)
async def enter_description(message: types.Message, state: FSMContext):
    await Form.description.set()
    await message.answer('ℹ️ Введите описание к посту.',
                         reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
        types.KeyboardButton('Отмена')))


@dp.message_handler(Text('Отмена'), state=Form.description)
async def cancel_description(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer('♿️ Отменено.', reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    ))


@dp.message_handler(state=Form.description)
async def complete_album(message: types.Message, state: FSMContext):
    data = await state.get_data()
    album = data.get('album', [])
    description = message.text

    if not album:
        await message.answer('⚠️ Ничего не было отправлено.')
        return

    media_group = []
    for index, item in enumerate(album):
        caption = description if index == 0 else ''
        if item['type'] == 'photo':
            media_group.append(types.InputMediaPhoto(media=item['media'], caption=caption))
        elif item['type'] == 'video':
            media_group.append(types.InputMediaVideo(media=item['media'], caption=caption))
        elif item['type'] == 'document':
            media_group.append(types.InputMediaDocument(media=item['media'], caption=caption))

    try:
        forward = await bot.send_media_group(chat_id=config.admin_chat, media=media_group)
        meme_id = forward[0].message_id
    except Exception as e:
        logging.error(f"Ошибка при отправке медиа-группы: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке медиа-группы.")
        return

    await bot.forward_message(chat_id=config.admin_chat, from_chat_id=message.chat.id, message_id=message.message_id)
    await message.answer('✅ Мем отправлен админам на рассмотрение.',
                         reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton(text='Отправить мем'),
        KeyboardButton(text='Отправить сообщение')
    ))

    meme_cache_id = str(uuid.uuid4())
    acceptpg_id = "accept-pg-" + meme_cache_id
    acceptpoco_id = "accept-poco-" + meme_cache_id
    decline_id = "decline-" + meme_cache_id

    meme_data = {
        'album': album,
        'description': description,
        'user_id': message.from_user.id,
        'full_name': message.from_user.full_name
    }

    with open(f'callbackData-{meme_cache_id}.json', 'w') as f:
        json.dump(meme_data, f)

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text='✅ Отправить в pixelgang', callback_data=acceptpg_id))
    keyboard.add(InlineKeyboardButton(text='📳 Отправить в pocobytes', callback_data=acceptpoco_id))
    keyboard.add(InlineKeyboardButton(text='🆘 Отклонить', callback_data=decline_id))

    await bot.send_message(chat_id=config.admin_chat,
                           text=f'{message.from_user.full_name} отправил мем. Запостить в канал?',
                           reply_markup=keyboard)

    await bot.send_message(config.logs_channel,
                           f'Пользователь <a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a> отправил <a href="t.me/c/{config.admin_chat}/{meme_id}">мем</a>.')
    logging.info(
        f'Пользователь {message.from_user.full_name}({message.from_user.id}) отправил мем(t.me/c/{config.admin_chat}/{meme_id}).')

    await state.finish()


@dp.message_handler(state=Form.album, content_types=types.ContentType.ANY)
async def add_to_album(message: types.Message, state: FSMContext):
    data = await state.get_data()
    album = data.get('album', [])

    if message.photo:
        album.append({
            'type': 'photo',
            'media': message.photo[-1].file_id,
            'caption': message.caption
        })
    elif message.video:
        album.append({
            'type': 'video',
            'media': message.video.file_id,
            'caption': message.caption
        })
    elif message.document:
        album.append({
            'type': 'document',
            'media': message.document.file_id,
            'caption': message.caption
        })
    else:
        await message.answer('⚠️ Только медиафайлы поддерживаются для отправки в альбом.')
        return

    await state.update_data(album=album)
    await message.answer('✅ Добавлено к альбому.')

def apply_watermark(image_bytes, watermark_path='water.png'):
    # Открываем изображение и ватермарку
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    watermark = Image.open(watermark_path).convert("RGBA")

    # Изменяем размер ватермарки
    watermark_width = image.width // 6
    watermark_height = int((watermark_width / watermark.width) * watermark.height)
    watermark = watermark.resize((watermark_width, watermark_height))

    # Позиционируем ватермарку внизу справа
    position = (image.width - watermark_width - 10, image.height - watermark_height - 10)

    # Накладываем ватермарку на изображение
    transparent = Image.new("RGBA", image.size, (0, 0, 0, 0))
    transparent.paste(image, (0, 0))
    transparent.paste(watermark, position, mask=watermark)
    return transparent.convert("RGB")

@dp.callback_query_handler(lambda c: c.data.startswith("accept-"))
async def accept_meme(call: types.CallbackQuery):
    _, channel_id, meme_id = call.data.split("-", 2)
    filename = f'callbackData-{meme_id}.json'

    chat_id = config.channel if channel_id == "pg" else config.channel2 if channel_id == "poco" else None
    channel_name = '📱 pixelgang' if channel_id == "pg" else '📞 pocobytes' if channel_id == "poco" else None

    cursor.execute('SELECT * FROM storage WHERE channel = ?', (channel_id,))
    user = cursor.fetchone()
    last_used = user[1] if user else 0

    if chat_id is None:
        await call.answer("Неверный идентификатор канала.", show_alert=True)
        return

    if last_used is not None and (datetime.now() - datetime.fromisoformat(last_used)).total_seconds() < 1200:
        time_left = 1200 - (datetime.now() - datetime.fromisoformat(last_used)).total_seconds()
        await bot.answer_callback_query(call.id, f'⏳ В канале {channel_name} уже был недавно аппрув поста. Подожди еще {int(time_left)} секунд, по братски.')
        return

    with open(filename, 'r') as f:
        meme_data = json.load(f)

    description = f"{meme_data['description']}\nBy [{meme_data['full_name']}](tg://user?id={meme_data['user_id']})"

    media_group = []

    for index, item in enumerate(meme_data['album']):
        caption = description if index == 0 else ''

        if item['type'] == 'photo':
            # Получаем изображение из Telegram и накладываем ватермарку
            photo_bytes = await bot.download_file_by_id(item['media'])
            watermarked_image = apply_watermark(photo_bytes.read())
            output = io.BytesIO()
            watermarked_image.save(output, format='JPEG')
            output.seek(0)
            media_group.append(types.InputMediaPhoto(media=output, caption=caption, parse_mode='markdown'))

        elif item['type'] == 'video':
            # Временно пропускаем обработку видео, так как она сложнее
            media_group.append(types.InputMediaVideo(media=item['media'], caption=caption, parse_mode='markdown'))

        elif item['type'] == 'document':
            media_group.append(types.InputMediaDocument(media=item['media'], caption=caption, parse_mode='markdown'))

    try:
        await bot.send_media_group(chat_id=chat_id, media=media_group)
    except Exception as e:
        logging.error(f"Ошибка при отправке медиа-группы: {e}")
        await call.answer("Произошла ошибка при отправке медиа-группы", show_alert=True)
        return

    await bot.send_message(chat_id=meme_data['user_id'], text=f'✅ Ваш мем был одобрен админом и отправлен в {channel_name}.')

    await bot.answer_callback_query(call.id, 'Мем отправлен.')
    await bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text='Мем отправлен в канал.')
    cursor.execute('UPDATE storage SET last_send_time = ? WHERE channel = ?', (datetime.now(), channel_id))
    os.remove(filename)


@dp.callback_query_handler(lambda c: c.data.startswith("decline-"))
async def decline_meme(call: types.CallbackQuery):
    meme_id = call.data.split("-")[1]
    filename = f'callbackData-{meme_id}.json'

    with open(filename, 'r') as f:
        meme_data = json.load(f)

    # Отправляем уведомление пользователю об отклонении мема
    await bot.send_message(chat_id=meme_data['user_id'], text='❌ Ваш мем был отклонён админом.')

    await bot.answer_callback_query(call.id, 'Мем отклонён.')
    await bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text='Мем отклонён.')

    os.remove(filename)

@dp.callback_query_handler(lambda c: c.data.startswith("delete"))
async def delete_meme(call: types.CallbackQuery):
    _, message_id, channel_id = call.data.split('-')
    message_id = int(message_id)

    chat_id = config.channel if channel_id == "pg" else config.channel2 if channel_id == "poco" else None

    if chat_id is None:
        await call.answer("Неверный идентификатор канала.", show_alert=True)
        return

    await bot.delete_message(chat_id=chat_id, message_id=message_id)
    await bot.answer_callback_query(call.id, 'Пост удалён.')
    await bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text='Пост удалён.')
    await bot.send_message(chat_id=call.data.split()[2], text=f'Ваш пост был удалён админом.')

    user_id = call.data.split()[2]

    await bot.send_message(config.logs_channel, f'Пользователь <a href="tg://user?id={call.from_user.id}">{call.from_user.full_name}</a>({call.from_user.id}) удалил <a href="t.me/c/{config.admin_chat}/{call.message.message_id}">пост</a> от <a href="tg://user?id={user_id}">чела</a>({user_id}).')
    logging.info(f'Пользователь {call.from_user.full_name}({call.from_user.id}) удалил пост(t.me/c/{config.admin_chat}/{call.message.message_id}) от {user_id}.')


# @dp.message_handler(ChatTypeFilter("private"), ForwardedMessageFilter(False))
# async def main_private(message: types.Message):
#      await bot.forward_message(chat_id=config.admin_chat, from_chat_id=message.chat.id, message_id=message.message_id)


@dp.message_handler(IDFilter(chat_id=config.admin_chat), commands=['admin'])
async def admin_command(message: types.Message):
    # Логирование начала работы команды
    logging.info(f"Команда '/admin' вызвана пользователем: {message.from_user.id}")

    # Получаем ID администратора, который выполняет команду
    admin_id = message.from_user.id

    # Проверяем, является ли администратор зарегистрированным пользователем и имеет ли статус администратора
    cursor.execute('SELECT * FROM users WHERE id = ?', (admin_id,))
    admin = cursor.fetchone()
    logging.info(f"Проверка статуса администратора: {admin}")

    if not admin or admin[1] != 1:  # Проверка поля is_admin
        await message.reply("⚠️ У вас нет прав для выполнения этой команды.")
        logging.warning(f"Пользователь {admin_id} не имеет прав администратора.")
        return

    # Инициализация переменной для ID пользователя, которому нужно присвоить права администратора
    target_user_id = None

    # Если сообщение является ответом на другое сообщение, пытаемся получить ID пользователя
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        logging.info(f"Получен ID пользователя из ответа: {target_user_id}")
    # Если ID пользователя указан в аргументах команды
    elif message.get_args():
        try:
            target_user_id = int(message.get_args().strip())
            logging.info(f"Получен ID пользователя из аргументов команды: {target_user_id}")
        except ValueError:
            await message.reply('⚠️ Указанный ID должен быть числом.')
            logging.warning(f"ID пользователя не является числом: {message.get_args()}")
            return
    else:
        await message.reply('⚠️ Команда требует указания пользователя через пересланное сообщение или его ID в аргументах.')
        logging.warning("ID пользователя не указан.")
        return

    # Проверяем наличие пользователя в базе данных
    cursor.execute('SELECT * FROM users WHERE id = ?', (target_user_id,))
    user = cursor.fetchone()
    logging.info(f"Проверка наличия пользователя в базе данных: {user}")

    if not user:
        await message.reply(f'⚠️ Пользователь с ID: `{target_user_id}` не найден в базе данных.', parse_mode='markdown')
        logging.warning(f"Пользователь с ID {target_user_id} не найден в базе данных.")
        return

    # Проверяем, не пытается ли администратор назначить сам себя
    if target_user_id == admin_id:
        await message.reply("⚠️ Вы не можете назначить админом самого себя.")
        logging.warning(f"Администратор {admin_id} пытается назначить админом самого себя.")
        return

    # Проверяем, не является ли пользователь уже администратором
    if user[1] == 1:  # Проверка поля is_admin
        await message.reply(f'⚠️ Пользователь с ID: `{target_user_id}` уже является администратором.', parse_mode='markdown')
        logging.warning(f"Пользователь с ID {target_user_id} уже является администратором.")
        return

    # Обновляем статус пользователя в базе данных
    cursor.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (target_user_id,))
    conn.commit()
    logging.info(f"Пользователю с ID {target_user_id} присвоен статус администратора.")

    # Отправляем уведомление об успешном назначении администратора
    await message.reply(f"✅ Пользователь с ID: `{target_user_id}` назначен администратором", parse_mode='markdown')
    await bot.send_message(config.logs_channel, f"#GRANT_ADMIN\n\nID: {target_user_id}")
    logging.info(f"Пользователь с ID {target_user_id} назначен администратором и уведомление отправлено в лог-канал.")

@dp.message_handler(IDFilter(chat_id=config.admin_chat), IsReplyFilter(True))
async def main_admin(message: types.Message):
    if message.reply_to_message["from"]["id"] == config.bot_id:
        is_anon = True if message.reply_to_message.forward_sender_name else False

        if is_anon:
            sender_name = message.reply_to_message.forward_sender_name
            cursor.execute('SELECT id FROM users WHERE sender_name = ?', (sender_name,))
            user_data = cursor.fetchone()
            if user_data:
                user_id = user_data[0]
                cursor.execute('SELECT * FROM users WHERE id = ?', (message.from_user.id,))
                admin_data = cursor.fetchone()
                if admin_data and admin_data[1] == 1:
                    await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
                    await message.reply(f'😎 Пользователь был анонимным, но его удалось найти в БД и отправить сообщение анонимно.\n\nID: {user_id}')
                else:
                    await bot.forward_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
                    await message.reply(f'😎 Пользователь был анонимным, но его удалось найти в БД и отправить сообщение.\n\nID: {user_id}')
            else:
                await message.reply('🥲 Нет пользователя с таким анонимным именем.')
        else:
            user_id = message.reply_to_message.forward_from.id
            cursor.execute('SELECT * FROM users WHERE id = ?', (message.from_user.id,))
            admin_data = cursor.fetchone()

            if admin_data and admin_data[1] == 1:  # Проверка поля is_admin
                # Отправляем сообщение без указания автора
                await bot.send_message(chat_id=user_id, text=message.text)
                await message.reply('✅ Отправлено анонимно.')
            else:
                # Отправляем сообщение с указанием автора
                await bot.send_message(chat_id=user_id, text=f'Администратор {message.from_user.full_name} ({message.from_user.id}) написал:\n\n{message.text}')
                await message.reply('✅ Отправлено.')


# @dp.errors_handler()
# async def errors_handler(_, error):
#     logging.error(error)
#     try:
#         await bot.send_message(chat_id=config.error_channel,
#                                text=f"Лог об ошибке:\n\n<code>{error}</code>")
#         return True
#     except Exception as e:
#         print(e)
#         print(error)
#         return False


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
