import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, PreCheckoutQueryHandler, ChatJoinRequestHandler, filters,
)

from flask import Flask
from threading import Thread

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive"

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "nft_stars_bot.sqlite3")
DEFAULT_PRICE = 149
ACCESS_DAYS = 30
EXPIRY_CHECK_INTERVAL = 3600  # seconds

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
log = logging.getLogger("nft-stars-bot")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS payments (
            charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, username TEXT,
            first_name TEXT, amount INTEGER NOT NULL, currency TEXT NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL, paid_at TEXT NOT NULL,
            expires_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS access (
            user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_states (
            user_id INTEGER PRIMARY KEY, state TEXT NOT NULL
        )""")
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('price', ?)", (str(DEFAULT_PRICE),))
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('invite_link', '')")
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('log_channel', '')")
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('group_id', '')")


def setting(key: str) -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""


def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⭐ Subscribe Now", callback_data="subscribe")]])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Change subscription price", callback_data="admin:price")],
        [InlineKeyboardButton("🔗 Change invite link", callback_data="admin:invite_link")],
        [InlineKeyboardButton("📣 Change log channel", callback_data="admin:log_channel")],
        [InlineKeyboardButton("📊 Payment stats", callback_data="admin:stats")],
    ])


def format_user(user) -> str:
    username = f"@{user.username}" if user.username else "(no username)"
    return f"{user.full_name} {username}\nUser ID: `{user.id}`"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or update.effective_chat.type != ChatType.PRIVATE:
        return
    args = context.args
    if args and re.fullmatch(r"add([1-9][0-9]{0,4})", args[0], re.IGNORECASE):
        amount = int(re.fullmatch(r"add([1-9][0-9]{0,4})", args[0], re.IGNORECASE).group(1))
        await send_payment_invoice(update, context, amount, "topup", f"Voluntary support payment: {amount} Stars")
        return
    price = int(setting("price") or DEFAULT_PRICE)
    await update.effective_message.reply_text(
        "👋 Welcome!\n\nGet access to the VIP group with Telegram Stars.\n\n"
        f"💰 Price: {price} ⭐ / {ACCESS_DAYS} days\n\nTap below to subscribe.\n\nAfter joining, use /check to see your remaining subscription time.",
        reply_markup=main_keyboard(),
    )


async def send_payment_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int, kind: str, description: str):
    user = update.effective_user
    if not user:
        return
    if amount < 1 or amount > 10000:
        await update.effective_message.reply_text("The amount must be between 1 and 10,000 Stars.")
        return
    payload = f"{kind}:{user.id}:{uuid.uuid4().hex}"
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="VIP Contents 🥵" if kind == "subscription" else "Support the bot",
        description=description[:255],
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Telegram Stars", amount=amount)],
        start_parameter="vipcon" if kind == "subscription" else f"add{amount}",
    )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    price = int(setting("price") or DEFAULT_PRICE)
    await send_payment_invoice(update, context, price, "subscription", f"VIP Conry Contents 🥵 access for {ACCESS_DAYS} days.")


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /add 50\nThis creates a one-off Stars payment invoice for the specified amount.")
        return
    await send_payment_invoice(update, context, int(context.args[0]), "topup", f"Voluntary support payment: {context.args[0]} Stars")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ You are not authorized to use the admin panel.")
        return
    price = setting("price") or str(DEFAULT_PRICE)
    invite = setting("invite_link") or "Not set"
    channel = setting("log_channel") or "Not set"
    group_id = setting("group_id") or "Not set (run /setgroup inside the VIP group)"
    await update.effective_message.reply_text(
        f"⚙️ Bot Admin\n\nSubscription price: {price} ⭐ / {ACCESS_DAYS} days\n"
        f"Invite link: {invite}\nLog channel: {channel}\nVIP group ID: {group_id}\n\n"
        "Use /setprice 149, /setlink https://t.me/..., /setchannel @channel. "
        "To configure the VIP group, send /setgroup as an admin inside that group.",
        reply_markup=admin_keyboard(),
        disable_web_page_preview=True,
    )


async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data or ""
    if data == "subscribe":
        price = int(setting("price") or DEFAULT_PRICE)
        await send_payment_invoice_from_query(query, context, price)
        return
    if not is_admin(user.id):
        await query.answer("Not authorized", show_alert=True)
        return
    if data == "admin:stats":
        with db() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM payments WHERE kind='subscription'").fetchone()["n"]
            stars = conn.execute("SELECT COALESCE(SUM(amount),0) AS n FROM payments").fetchone()["n"]
            supporters = conn.execute("SELECT COUNT(*) AS n FROM payments WHERE kind='topup'").fetchone()["n"]
        await query.message.reply_text(f"📊 Stats\nSubscription payments: {total}\nOne-off payments: {supporters}\nTotal Stars collected: {stars}")
        return
    state_map = {
        "admin:price": ("price", "Send the new subscription price as a whole number of Stars (1–10,000)."),
        "admin:invite_link": ("invite_link", "Send the group's Telegram invite link. For manual approval, use a link with join requests enabled."),
        "admin:log_channel": ("log_channel", "Send the private channel @username or numeric chat ID. Add the bot to that channel with permission to post."),
    }
    if data in state_map:
        state, prompt = state_map[data]
        with db() as conn:
            conn.execute("INSERT INTO admin_states(user_id,state) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state", (user.id, state))
        await query.message.reply_text(prompt)


async def send_payment_invoice_from_query(query, context, amount):
    user = query.from_user
    payload = f"subscription:{user.id}:{uuid.uuid4().hex}"
    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="VIP leaks 🥵",
        description=f"Subscribe for exclusive content 🥵 Direct viral leaks 🔥 !",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="VIP access", amount=amount)],
        start_parameter="vipcon",
    )


async def setprice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= 10000:
        await update.effective_message.reply_text("Usage: /setprice 149 (1–10,000 Stars)")
        return
    set_setting("price", str(int(context.args[0])))
    await update.effective_message.reply_text(f"Subscription price updated to {int(context.args[0])} ⭐.")


async def setlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1 or not context.args[0].startswith("https://t.me/"):
        await update.effective_message.reply_text("Usage: /setlink https://t.me/your_join_request_link")
        return
    set_setting("invite_link", context.args[0])
    await update.effective_message.reply_text("Invite link updated.")


async def setchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /setchannel @YourPrivateLogChannel or /setchannel -1001234567890")
        return
    set_setting("log_channel", context.args[0])
    await update.effective_message.reply_text("Log channel updated. Make sure the bot can post there.")


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.effective_message or not update.effective_message.text:
        return
    with db() as conn:
        row = conn.execute("SELECT state FROM admin_states WHERE user_id=?", (user.id,)).fetchone()
    if not row:
        return
    state = row["state"]
    value = update.effective_message.text.strip()
    if value.startswith("/"):
        return
    if state == "price":
        if not value.isdigit() or not 1 <= int(value) <= 10000:
            await update.effective_message.reply_text("Enter a whole number from 1 to 10,000.")
            return
        value = str(int(value))
    elif state == "invite_link":
        if not value.startswith("https://t.me/"):
            await update.effective_message.reply_text("Please send a valid https://t.me/ invite link.")
            return
    elif state == "log_channel":
        if not (value.startswith("@") or re.fullmatch(r"-100\d+", value)):
            await update.effective_message.reply_text("Send a channel @username or numeric -100… chat ID.")
            return
    set_setting(state, value)
    with db() as conn:
        conn.execute("DELETE FROM admin_states WHERE user_id=?", (user.id,))
    await update.effective_message.reply_text(f"Updated {state.replace('_', ' ')}.")


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        kind, user_id, _ = query.invoice_payload.split(":", 2)
        valid = kind in {"subscription", "topup"} and int(user_id) == query.from_user.id and query.currency == "XTR" and query.total_amount > 0
    except (ValueError, AttributeError):
        valid = False
    await query.answer(ok=valid, error_message=None if valid else "This payment could not be verified. Please start again from the bot.")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    payment = message.successful_payment
    try:
        kind, intended_user, _ = payment.invoice_payload.split(":", 2)
        intended_user = int(intended_user)
    except (ValueError, AttributeError):
        await message.reply_text("Payment received, but the order reference could not be verified. Please contact an admin.")
        return
    if kind not in {"subscription", "topup"} or intended_user != user.id or payment.currency != "XTR":
        await message.reply_text("Payment received, but its order details need admin review.")
        return

    now = datetime.now(timezone.utc)
    expires_at = None
    charge_id = payment.telegram_payment_charge_id
    inserted = False
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO payments(charge_id,user_id,username,first_name,amount,currency,kind,payload,paid_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (charge_id, user.id, user.username, user.first_name, payment.total_amount, payment.currency, kind, payment.invoice_payload, now.isoformat(), expires_at),
            )
            inserted = True
        except sqlite3.IntegrityError:
            inserted = False
        if inserted and kind == "subscription":
            old = conn.execute("SELECT expires_at FROM access WHERE user_id=?", (user.id,)).fetchone()
            start = now
            if old:
                try:
                    old_expiry = datetime.fromisoformat(old["expires_at"])
                    if old_expiry > now:
                        start = old_expiry
                except ValueError:
                    pass
            new_expiry = (start + timedelta(days=ACCESS_DAYS)).isoformat()
            conn.execute("INSERT INTO access(user_id,expires_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at", (user.id, new_expiry))
            conn.execute("UPDATE payments SET expires_at=? WHERE charge_id=?", (new_expiry, charge_id))
            expires_at = new_expiry
    if not inserted:
        await message.reply_text("This payment has already been processed.")
        return

    if kind == "subscription":
        invite = setting("invite_link")
        if invite:
            await message.reply_text(
                "Thank you for subscribing.\n"
                f"Here's your link : {invite}\n"
                "Use /check to check your subscription",
                disable_web_page_preview=True,
            )
        else:
            await message.reply_text("✅ Payment successful, but the group invite link is not configured yet. Please contact an admin.")
    else:
        await message.reply_text(f"❤️ Thank you! Your one-off payment of {payment.total_amount} ⭐ was received. This payment does not grant VIP access.")

    channel = setting("log_channel")
    if channel:
        username = f"@{user.username}" if user.username else "(no username)"
        kind_label = "VIP SUBSCRIPTION" if kind == "subscription" else "ONE-OFF SUPPORT"
        expiry_line = f"\nAccess expires: {expires_at}" if expires_at else "\nVIP access: Not included"
        text = (
            f"💸 <b>{kind_label} PAYMENT</b>\n\n"
            f"Name: {user.full_name}\nUsername: {username}\nUser ID: <code>{user.id}</code>\n"
            f"Amount: ⭐ {payment.total_amount}\nCurrency: {payment.currency}\n"
            f"Charge ID: <code>{charge_id}</code>\nPaid at: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            f"{expiry_line}\n\nPayload: <code>{payment.invoice_payload}</code>"
        )
        try:
            await context.bot.send_message(chat_id=channel, text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            log.exception("Could not post payment log to channel %s", channel)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    with db() as conn:
        row = conn.execute("SELECT expires_at FROM access WHERE user_id=?", (user.id,)).fetchone()
    if not row:
        await update.effective_message.reply_text("You don't have an active subscription. Use /start to subscribe.")
        return
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except ValueError:
        await update.effective_message.reply_text("I couldn't read your subscription record. Please contact an admin.")
        return
    remaining = expiry - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        await update.effective_message.reply_text("Your subscription has expired. Please use /start to resubscribe.")
        return
    days = remaining.days
    hours = remaining.seconds // 3600
    await update.effective_message.reply_text(
        f"✅ Your subscription is active.\nTime remaining: {days} day(s) and {hours} hour(s).\n"
        f"Expires: {expiry.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


async def setgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("Only a configured bot admin can set the VIP group.")
        return
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await update.effective_message.reply_text("Run /setgroup inside the VIP group itself.")
        return
    set_setting("group_id", str(chat.id))
    await update.effective_message.reply_text(
        f"VIP group configured (ID: {chat.id}). Ensure the bot is an administrator with permission to approve join requests and ban/remove members."
    )


async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    if not request:
        return
    group_id = setting("group_id")
    if not group_id or str(request.chat.id) != group_id:
        log.warning("Ignoring join request for unconfigured group %s", request.chat.id)
        return
    user_id = request.from_user.id
    with db() as conn:
        row = conn.execute("SELECT expires_at FROM access WHERE user_id=?", (user_id,)).fetchone()
    active = False
    if row:
        try:
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            active = expiry > datetime.now(timezone.utc)
        except ValueError:
            pass
    if not active:
        try:
            await context.bot.decline_chat_join_request(chat_id=request.chat.id, user_id=user_id)
        except Exception:
            log.exception("Failed to decline expired/unpaid join request for user %s", user_id)
        try:
            await context.bot.send_message(user_id, "Your subscription is not active. Please subscribe before requesting to join the VIP group.")
        except Exception:
            log.info("Could not DM user %s about denied join request", user_id)
        return
    try:
        await context.bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user_id)
        log.info("Approved VIP join request from user %s", user_id)
    except Exception:
        log.exception("Could not approve join request from user %s", user_id)


async def expire_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc)
    group_id = setting("group_id")
    with db() as conn:
        rows = conn.execute("SELECT user_id, expires_at FROM access").fetchall()
    for row in rows:
        user_id = int(row["user_id"])
        try:
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except ValueError:
            log.exception("Invalid expiry date for user %s", user_id)
            continue
        if expiry > now:
            continue
        notified_expiry = setting(f"expired_notified:{user_id}")
        if notified_expiry == expiry.isoformat():
            continue
        # Remove access from the configured VIP group. Ban then unban removes the member
        # while allowing them to join again after purchasing a new subscription.
        if group_id:
            try:
                await context.bot.ban_chat_member(chat_id=int(group_id), user_id=user_id)
                await context.bot.unban_chat_member(chat_id=int(group_id), user_id=user_id, only_if_banned=True)
            except Exception:
                log.exception("Could not remove expired subscriber %s from group %s", user_id, group_id)
        try:
            await context.bot.send_message(
                user_id,
                "Your subscription expired kindly resubscribe to enjoy the features",
            )
        except Exception:
            log.info("Could not DM expired subscriber %s", user_id)
        # Mark expiry as handled so the user is not repeatedly kicked/notified every hour.
        # Keep the access row for /check to report the expired state.
        with db() as conn:
            conn.execute("UPDATE access SET expires_at=? WHERE user_id=?", (expiry.isoformat(), user_id))
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"expired_notified:{user_id}", expiry.isoformat()))


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Your Telegram user ID: `{update.effective_user.id}`", parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled bot error", exc_info=context.error)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and configure it, then export the variables or use a dotenv runner.")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("setprice", setprice_command))
    app.add_handler(CommandHandler("setlink", setlink_command))
    app.add_handler(CommandHandler("setchannel", setchannel_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CallbackQueryHandler(admin_buttons))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, admin_text_input))
    app.add_error_handler(error_handler)
    if app.job_queue is None:
        raise SystemExit("JobQueue is unavailable. Install requirements with: pip install -r requirements.txt")
    app.job_queue.run_repeating(expire_subscriptions, interval=EXPIRY_CHECK_INTERVAL, first=10, name="expire-subscriptions")
    log.info("Stars bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, use_reloader=False),
        daemon=True,
    ).start()

    while True:
        try:
            main()
        except Exception as e:
            logger.exception("Bot crashed, restarting in 5 seconds...")
            import time

            time.sleep(5)