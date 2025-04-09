from http.server import BaseHTTPRequestHandler
import os
import json
import asyncio
import logging
from telebot.async_telebot import AsyncTeleBot
from telebot import types
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler

# Load environment variables
load_dotenv()
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_USER_ID = int(os.environ.get('ADMIN_USER_ID'))

# Initialize Firebase
firebase_config = json.loads(os.environ.get('FIREBASE_SERVICE_ACCOUNT'))
cred = credentials.Certificate(firebase_config)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize bot
bot = AsyncTeleBot(BOT_TOKEN)

# Collection names
USERS_COLLECTION = "users"
MESSAGES_COLLECTION = "messages"

# Default welcome message
DEFAULT_WELCOME_MESSAGE = """
Welcome! Here are the group policies:
1. Be respectful.
2. No spam.
3. Follow the rules.
"""

# State management
user_states = {}

# Function to get the DM message from Firebase
def get_dm_message():
    message_ref = db.collection(MESSAGES_COLLECTION).document("welcome_message")
    message = message_ref.get()
    if message.exists:
        return message.to_dict()
    return {"text": DEFAULT_WELCOME_MESSAGE}

# Function to set the DM message in Firebase
def set_dm_message(new_message, media_file_id=None, media_type=None):
    message_data = {"text": new_message}
    if media_file_id:
        message_data["media_file_id"] = media_file_id
        message_data["media_type"] = media_type
    db.collection(MESSAGES_COLLECTION).document("welcome_message").set(message_data)

# Function to add a user to Firebase
def add_user(user_id, username, first_name, last_name):
    user_data = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "has_received_welcome": False
    }
    db.collection(USERS_COLLECTION).document(str(user_id)).set(user_data)

# Function to check if a user is new
def is_new_user(user_id):
    user_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    user = user_ref.get()
    return not user.exists

# Function to get all users from Firebase
def get_all_users():
    users = db.collection(USERS_COLLECTION).stream()
    return [user.to_dict() for user in users]

# Handle join requests
@bot.chat_join_request_handler()
async def handle_join_request(message: types.ChatJoinRequest):
    user = message.from_user
    user_id = user.id
    username = user.username or user.first_name
    first_name = user.first_name
    last_name = user.last_name or ""

    # Add user to Firebase if they are new
    if is_new_user(user_id):
        add_user(user_id, username, first_name, last_name)

    # Get the DM message
    message_data = get_dm_message()
    dm_message = message_data.get("text", DEFAULT_WELCOME_MESSAGE)
    media_file_id = message_data.get("media_file_id")
    media_type = message_data.get("media_type")

    try:
        if media_file_id:
            if media_type == "photo":
                await bot.send_photo(
                    chat_id=user_id,
                    photo=media_file_id,
                    caption=dm_message
                )
            elif media_type == "video":
                await bot.send_video(
                    chat_id=user_id,
                    video=media_file_id,
                    caption=dm_message
                )
            elif media_type == "animation":  # Handle GIFs
                await bot.send_animation(
                    chat_id=user_id,
                    animation=media_file_id,
                    caption=dm_message
                )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=dm_message
            )
        logging.info(f"DM sent to {username} (ID: {user_id})")
    except Exception as e:
        logging.error(f"Failed to send DM to {username}: {e}")

    # Approve the join request
    await bot.approve_chat_join_request(message.chat.id, user_id)
    logging.info(f"Join request approved for {username} (ID: {user_id})")

# Command to start setting the welcome message
@bot.message_handler(commands=['set_welcome'])
async def start_set_welcome(message: types.Message):
    user_id = message.from_user.id

    # Check if the user is the admin
    if user_id != ADMIN_USER_ID:
        await bot.reply_to(message, "You are not authorized to use this command.")
        return

    # Ask the user to provide the welcome message
    await bot.reply_to(message, "Please provide a new welcome message.")
    user_states[user_id] = {"state": "awaiting_welcome_message"}

# Handle the user's response for the welcome message
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "awaiting_welcome_message")
async def handle_welcome_message(message: types.Message):
    user_id = message.from_user.id

    # Store the welcome message text
    welcome_message = message.text

    # Ask the user to provide the media (photo, video, or GIF) or click /empty
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Skip Media", callback_data="skip_media_welcome"))
    await bot.reply_to(message, "Please upload a photo, video, or GIF for the welcome message or click 'Skip Media'.", reply_markup=markup)
    user_states[user_id] = {"state": "awaiting_welcome_media", "welcome_message": welcome_message}

# Handle the user's media upload for set_welcome
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "awaiting_welcome_media", content_types=['photo', 'video', 'document', 'animation'])
async def handle_set_welcome_media(message: types.Message):
    user_id = message.from_user.id

    # Get the welcome message from the state
    welcome_message = user_states[user_id].get("welcome_message")
    media_file_id = None
    media_type = None

    if message.photo:
        media_file_id = message.photo[-1].file_id  # Get the highest resolution photo's file_id
        media_type = "photo"
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"
    elif message.document and message.document.mime_type == "video/mp4":  # Handle GIFs
        media_file_id = message.document.file_id
        media_type = "animation"
    elif message.animation:  # Handle GIFs sent as animations
        media_file_id = message.animation.file_id
        media_type = "animation"
    else:
        # Invalid input
        await bot.reply_to(message, "Invalid input. Please upload a photo, video, or GIF.")
        return

    # Update the DM message in Firebase
    set_dm_message(welcome_message, media_file_id, media_type)
    await bot.reply_to(message, "Welcome message updated successfully!")
    logging.info(f"User {user_id} updated welcome message with media: {media_type}")

    # Clear the user's state
    user_states.pop(user_id, None)

# Handle the "Skip Media" callback for welcome message
@bot.callback_query_handler(func=lambda call: call.data == "skip_media_welcome")
async def handle_skip_media_welcome(call: types.CallbackQuery):
    user_id = call.from_user.id
    welcome_message = user_states[user_id].get("welcome_message")

    # Update the DM message in Firebase without media
    set_dm_message(welcome_message)
    await bot.answer_callback_query(call.id, "Welcome message updated successfully!")
    logging.info(f"User {user_id} updated welcome message without media.")

    # Clear the user's state
    user_states.pop(user_id, None)

# Command to start a broadcast
@bot.message_handler(commands=['broadcast'])
async def start_broadcast(message: types.Message):
    user_id = message.from_user.id

    # Check if the user is the admin
    if user_id != ADMIN_USER_ID:
        await bot.reply_to(message, "You are not authorized to use this command.")
        return

    # Ask the user to provide the broadcast message
    await bot.reply_to(message, "Please provide the broadcast message.")
    user_states[user_id] = {"state": "awaiting_broadcast_message"}

# Handle the user's response for the broadcast message
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "awaiting_broadcast_message")
async def handle_broadcast_message(message: types.Message):
    user_id = message.from_user.id

    # Store the broadcast message text
    broadcast_message = message.text

    # Ask the user to provide the media (photo, video, or GIF) or click /empty
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Skip Media", callback_data="skip_media_broadcast"))
    await bot.reply_to(message, "Please upload a photo, video, or GIF for the broadcast message or click 'Skip Media'.", reply_markup=markup)
    user_states[user_id] = {"state": "awaiting_broadcast_media", "broadcast_message": broadcast_message}

# Handle the user's media upload for broadcast
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id, {}).get("state") == "awaiting_broadcast_media", content_types=['photo', 'video', 'document', 'animation'])
async def handle_broadcast_media(message: types.Message):
    user_id = message.from_user.id

    # Get the broadcast message from the state
    broadcast_message = user_states[user_id].get("broadcast_message")
    media_file_id = None
    media_type = None

    if message.photo:
        media_file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"
    elif message.document and message.document.mime_type == "video/mp4": 
        media_file_id = message.document.file_id
        media_type = "animation"
    elif message.animation:  # Handle GIFs sent as animations
        media_file_id = message.animation.file_id
        media_type = "animation"
    else:
        # Invalid input
        await bot.reply_to(message, "Invalid input. Please upload a photo, video, or GIF.")
        return

    # Send the broadcast message to all users
    users = get_all_users()
    success_count = 0
    fail_count = 0
    success_users = []
    fail_users = []

    for user in users:
        try:
            if media_file_id:
                if media_type == "photo":
                    await bot.send_photo(
                        chat_id=user["user_id"],
                        photo=media_file_id,
                        caption=broadcast_message
                    )
                elif media_type == "video":
                    await bot.send_video(
                        chat_id=user["user_id"],
                        video=media_file_id,
                        caption=broadcast_message
                    )
                elif media_type == "animation":
                    await bot.send_animation(
                        chat_id=user["user_id"],
                        animation=media_file_id,
                        caption=broadcast_message
                    )
            else:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=broadcast_message
                )
            success_count += 1
            success_users.append(f"{user['username']} ({user['first_name']} {user.get('last_name', '')})".strip())
            logging.info(f"Broadcast message sent to {user['first_name']} (ID: {user['user_id']})")
        except Exception as e:
            fail_count += 1
            fail_users.append(f"{user['username']} ({user['first_name']} {user.get('last_name', '')})".strip())
            logging.error(f"Failed to send broadcast message to {user['first_name']}: {e}")

    # Prepare the response message
    response_message = f"Broadcast message sent successfully to {success_count} users. Failed for {fail_count} users.\n\n"
    if success_users:
        response_message += "Successfully sent to:\n" + "\n".join(success_users) + "\n\n"
    if fail_users:
        response_message += "Failed to send to:\n" + "\n".join(fail_users)

    await bot.reply_to(message, response_message)
    logging.info(f"User {user_id} completed broadcast.")

    # Clear the user's state
    user_states.pop(user_id, None)

# Handle the "Skip Media" callback for broadcast
@bot.callback_query_handler(func=lambda call: call.data == "skip_media_broadcast")
async def handle_skip_media_broadcast(call: types.CallbackQuery):
    user_id = call.from_user.id
    broadcast_message = user_states[user_id].get("broadcast_message")

    # Send the broadcast message to all users without media
    users = get_all_users()
    success_count = 0
    fail_count = 0
    success_users = []
    fail_users = []

    for user in users:
        try:
            await bot.send_message(
                chat_id=user["user_id"],
                text=broadcast_message
            )
            success_count += 1
            success_users.append(f"{user['username']} ({user['first_name']} {user.get('last_name', '')})".strip())
            logging.info(f"Broadcast message sent to {user['first_name']} (ID: {user['user_id']})")
        except Exception as e:
            fail_count += 1
            fail_users.append(f"{user['username']} ({user['first_name']} {user.get('last_name', '')})".strip())
            logging.error(f"Failed to send broadcast message to {user['first_name']}: {e}")

    # Prepare the response message
    response_message = f"Broadcast message sent successfully to {success_count} users. Failed for {fail_count} users.\n\n"
    if success_users:
        response_message += "Successfully sent to:\n" + "\n".join(success_users) + "\n\n"
    if fail_users:
        response_message += "Failed to send to:\n" + "\n".join(fail_users)

    await bot.answer_callback_query(call.id, response_message)
    logging.info(f"User {user_id} completed broadcast without media.")

    # Clear the user's state
    user_states.pop(user_id, None)
# Command to start the bot
@bot.message_handler(commands=['start'])
async def start(message: types.Message):
    await bot.reply_to(message, "Hello! I'm your channel manager bot.")

# HTTP handler for Vercel
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        update_dict = json.loads(post_data.decode('utf-8'))

        asyncio.run(self.process_update(update_dict))

        self.send_response(200)
        self.end_headers()

    async def process_update(self, update_dict):
        update = types.Update.de_json(update_dict)
        await bot.process_new_updates([update])

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write('Hello, BOT is running!'.encode('utf-8'))
  