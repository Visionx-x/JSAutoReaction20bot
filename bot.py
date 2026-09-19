"""
=========================================================
AUTO REACTION BOT
=========================================================

Flow:
1. User starts bot -> /start shows instructions: add bot as admin
   in your channel/group first.
2. User adds bot as admin -> bot auto-detects via on_chat_member_updated
   (my_chat_member equivalent), saves chat to MongoDB, and notifies
   the OWNER with chat name + link (if available) + who added it.
3. User (must be admin of that chat) runs /connect in the group/channel
   (or via bot PM using chat id) to mark the chat as "active" for reactions.
4. User runs /setemoji <emoji> in the chat to choose the reaction emoji
   used for that chat (defaults to a fallback emoji if not set).
5. Any new post/message in a connected chat -> bot reacts with the
   configured emoji automatically.

Requires:
    pip install kurigram motor

Run:
    python reaction_bot.py
"""

import asyncio
import logging
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import Message, ChatMemberUpdated
from pyrogram.errors import FloodWait
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
# =========================================================
# CONFIG
# =========================================================

import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "reaction_bot")

DEFAULT_EMOJI = "❤️"

# Telegram's currently allowed reaction emojis (non-premium set).
# Keep this updated if Telegram changes it; used to validate /setemoji input.
ALLOWED_EMOJIS = {
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
    "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨",
    "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿",
    "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂",
    "🤷", "🤷‍♀", "😡",
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("reaction_bot")

app = Client(
    "reaction_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo[DB_NAME]
users_col = db["users"]
chats_col = db["chats"]

# =========================================================
# DB HELPERS
# =========================================================

async def save_user(user_id: int, name: str, username: str = None):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "name": name, "username": username}},
        upsert=True,
    )


async def get_served_users():
    return await users_col.find({}).to_list(length=None)


async def save_chat(chat_id: int, title: str, chat_type: str, added_by: int, link: str = None):
    await chats_col.update_one(
        {"chat_id": chat_id},
        {
            "$set": {
                "chat_id": chat_id,
                "title": title,
                "type": chat_type,
                "added_by": added_by,
                "link": link,
                "connected": False,
                "emoji": DEFAULT_EMOJI,
                "added_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )


async def get_chat(chat_id: int):
    return await chats_col.find_one({"chat_id": chat_id})


async def set_chat_connected(chat_id: int, connected: bool):
    await chats_col.update_one({"chat_id": chat_id}, {"$set": {"connected": connected}})


async def set_chat_emoji(chat_id: int, emoji: str):
    await chats_col.update_one({"chat_id": chat_id}, {"$set": {"emoji": emoji}})


async def remove_chat(chat_id: int):
    await chats_col.delete_one({"chat_id": chat_id})


# =========================================================
# HELPERS
# =========================================================

async def is_user_chat_admin(cli: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await cli.get_chat_member(chat_id, user_id)
        return member.status in (
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        )
    except Exception:
        return False


async def get_chat_link(cli: Client, chat_id: int) -> str:
    try:
        chat = await cli.get_chat(chat_id)
        if chat.username:
            return f"https://t.me/{chat.username}"
        try:
            invite = await cli.export_chat_invite_link(chat_id)
            return invite
        except Exception:
            return None
    except Exception:
        return None


# =========================================================
# /start
# =========================================================

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(cli: Client, message: Message):
    await save_user(
        message.from_user.id,
        message.from_user.first_name,
        message.from_user.username,
    )

    me = await cli.get_me()

    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add to Channel", url=f"https://t.me/{me.username}?startchannel=true")],
            [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{me.username}?startgroup=true")],
        ]
    )
    await message.reply_text(
        "<b>👋 Hi, I'm an Auto Reaction Bot!</b>\n\n"
        "I automatically react to new posts in your channel or group "
        "with your chosen emoji.\n\n"
        "<b>How to use:</b>\n"
        "1️⃣ Add me as <b>admin</b> using the buttons below.\n"
        "2️⃣ Send <code>/connect</code> in the same channel/group.\n"
        "3️⃣ Send <code>/setemoji 🔥</code> to choose your emoji.\n\n"
        "That's it! I'll automatically react to new posts and messages.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=buttons,
    )


# =========================================================
# BOT ADDED TO CHAT -> save + notify owner
# =========================================================

@app.on_chat_member_updated()
async def on_bot_added(cli: Client, update: ChatMemberUpdated):
    me = await cli.get_me()

    if update.new_chat_member is None or update.new_chat_member.user.id != me.id:
        return

    new_status = update.new_chat_member.status

    if new_status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.MEMBER):
        chat = update.chat
        added_by = update.from_user

        link = await get_chat_link(cli, chat.id)

        await save_chat(
            chat_id=chat.id,
            title=chat.title or "Unknown",
            chat_type=str(chat.type),
            added_by=added_by.id if added_by else None,
            link=link,
        )

        adder_mention = added_by.mention if added_by else "Unknown user"
        chat_info = f'<a href="{link}">{chat.title}</a>' if link else f"<b>{chat.title}</b>"

        try:
            await cli.send_message(
                OWNER_ID,
                f"➕ Bot added to {chat_info}\n"
                f"👤 Added by: {adder_mention} (<code>{added_by.id if added_by else '-'}</code>)\n"
                f"🆔 Chat ID: <code>{chat.id}</code>\n"
                f"📌 Status: <b>{new_status.name}</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e:
            log.warning(f"Failed to notify owner: {e}")

    elif new_status in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        await remove_chat(update.chat.id)

        try:
            await cli.send_message(
                OWNER_ID,
                f"➖ Bot removed from <b>{update.chat.title}</b>\n"
                f"🆔 Chat ID: <code>{update.chat.id}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


# =========================================================
# /connect  (must be run in the group/channel, by an admin)
# =========================================================

@app.on_message(filters.command("connect") & (filters.group | filters.channel))
async def connect_cmd(cli: Client, message: Message):
    chat_id = message.chat.id

    chat_doc = await get_chat(chat_id)
    if not chat_doc:
        link = await get_chat_link(cli, chat_id)
        await save_chat(
            chat_id=chat_id,
            title=message.chat.title or "Unknown",
            chat_type=str(message.chat.type),
            added_by=message.from_user.id if message.from_user else None,
            link=link,
        )

    # Channels have no message.from_user (posts are anonymous), so admin
    # check only applies to groups/supergroups where we know who sent it.
    if message.from_user:
        is_admin = await is_user_chat_admin(cli, chat_id, message.from_user.id)
        if not is_admin:
            return await message.reply_text("❌ Only chat admins can use this command.")

    await set_chat_connected(chat_id, True)
    await message.reply_text(
        f"✅ Connected! I'll now react to new posts here with "
        f"{(await get_chat(chat_id)).get('emoji', DEFAULT_EMOJI)}.\n"
        f"Change it anytime with /setemoji"
    )


# =========================================================
# /disconnect
# =========================================================

@app.on_message(filters.command("disconnect") & (filters.group | filters.channel))
async def disconnect_cmd(cli: Client, message: Message):
    chat_id = message.chat.id

    if message.from_user:
        is_admin = await is_user_chat_admin(cli, chat_id, message.from_user.id)
        if not is_admin:
            return await message.reply_text("❌ Only chat admins can use this command.")

    await set_chat_connected(chat_id, False)
    await message.reply_text("🛑 Disconnected. I won't react here anymore.")


# =========================================================
# /setemoji <emoji>
# =========================================================

@app.on_message(filters.command("setemoji") & (filters.group | filters.channel))
async def setemoji_cmd(cli: Client, message: Message):
    chat_id = message.chat.id

    if len(message.command) < 2:
        return await message.reply_text("<b>Example:</b> <code>/setemoji 🔥</code>", parse_mode=enums.ParseMode.HTML)

    emoji = message.command[1].strip()

    if message.from_user:
        is_admin = await is_user_chat_admin(cli, chat_id, message.from_user.id)
        if not is_admin:
            return await message.reply_text("❌ Only chat admins can use this command.")

    chat_doc = await get_chat(chat_id)
    if not chat_doc:
        return await message.reply_text("⚠️ Run /connect first.")

    # Validate by actually trying it on this message — Telegram rejects
    # unsupported emojis with REACTION_INVALID, so no hardcoded list needed.
    try:
        await cli.send_reaction(chat_id=chat_id, message_id=message.id, emoji=emoji)
    except Exception as e:
        if "REACTION_INVALID" in str(e):
            return await message.reply_text(
                "❌ That emoji isn't a valid Telegram reaction emoji. "
                "Pick one from the reaction bar under any message and try again."
            )
        return await message.reply_text(f"❌ Couldn't set that emoji: {e}")

    await set_chat_emoji(chat_id, emoji)
    await message.reply_text(f"✅ Reaction emoji set to {emoji}")


# =========================================================
# AUTO REACT ON NEW POSTS/MESSAGES
# =========================================================

@app.on_message((filters.group | filters.channel) & ~filters.service, group=1)
async def auto_react(cli: Client, message: Message):
    chat_doc = await get_chat(message.chat.id)

    if not chat_doc or not chat_doc.get("connected"):
        return

    emoji = chat_doc.get("emoji", DEFAULT_EMOJI)

    try:
        await cli.send_reaction(
            chat_id=message.chat.id,
            message_id=message.id,
            emoji=emoji,
        )
    except FloodWait as e:
        await asyncio.sleep(int(e.value))
        try:
            await cli.send_reaction(
                chat_id=message.chat.id,
                message_id=message.id,
                emoji=emoji,
            )
        except Exception as ex:
            log.warning(f"Reaction retry failed in {message.chat.id}: {ex}")
    except Exception as e:
        log.warning(f"Failed to react in {message.chat.id}: {e}")


# =========================================================
# BROADCAST
# =========================================================

broadcast_stats = {"success": 0, "failed": 0}


def save_broadcast_stats():
    # Plug in your own persistence here (file/DB) if you want stats
    # to survive a restart. Left as a no-op placeholder.
    pass


@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(cli: Client, message: Message):

    if message.reply_to_message:

        x = message.reply_to_message.id
        y = message.chat.id

    else:

        if len(message.command) < 2:
            return await message.reply_text(
                "<b>Example:</b>\n"
                "/broadcast [message]\n"
                "or reply to a message"
            )

        query = message.text.split(None, 1)[1]

    success = 0
    failed_users = []
    served_users = []

    susers = await get_served_users()

    for user in susers:
        served_users.append(int(user["user_id"]))

    for i in served_users:

        try:

            if message.reply_to_message:

                await cli.copy_message(
                    chat_id=i,
                    from_chat_id=y,
                    message_id=x
                )

            else:

                await cli.send_message(
                    i,
                    text=query
                )

            success += 1

            await asyncio.sleep(0.2)

        except FloodWait as e:

            flood_time = int(e.value)

            if flood_time > 200:
                failed_users.append(i)
                continue

            await asyncio.sleep(flood_time)

        except Exception:
            # Covers blocked bot, deleted account, deactivated
            # account, etc. -> counted as a failed send.
            failed_users.append(i)
            continue

    # Persist this run's numbers so /stats can show them later.
    broadcast_stats["success"] = success
    broadcast_stats["failed"] = len(failed_users)
    save_broadcast_stats()

    try:

        await message.reply_text(
            f"✅ Broadcast sent to {success} users.\n"
            f"❌ Failed to send to {len(failed_users)} users."
        )

    except Exception:
        pass


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    log.info("Starting reaction bot...")
    app.run()
