# NFT Alerts Telegram Stars Bot

A polling-based Python Telegram bot that sells rolling 30-day VIP access using Telegram Stars (XTR), logs confirmed payments to a private channel, automatically approves eligible join requests, checks remaining subscription time, removes expired members, and accepts optional one-off Stars support payments.

## Features

- `/start`: welcome screen and Subscribe Now button
- `/subscribe`: directly sends the VIP invoice
- `/check`: shows remaining subscription time or tells the user if it expired
- `/setgroup` (admin, inside the VIP group): configures the group where requests are approved and expired members are removed
- Automatically approves join requests only for users with active subscriptions
- Periodically removes expired members and privately sends the expiry notice
- `/add 50`: one-off invoice for 50 Stars; does not grant VIP access
- `https://t.me/YOUR_BOT?start=add50`: shareable one-off invoice flow
- `/admin`: inline admin panel to update price, group invite link, log channel, and view stats
- Admin shortcuts: `/setprice 149`, `/setlink https://t.me/...`, `/setchannel @ChannelName`
- Payment success handling, pre-checkout validation, SQLite transaction records, and duplicate-charge protection
- `/myid`: displays your Telegram user ID for configuration

## Setup (Kali Linux / Linux)

1. Create a bot using [@BotFather](https://t.me/BotFather) and copy its token.
2. Find your numeric Telegram user ID (you can run the bot and send `/myid`, or use a trusted ID bot).
3. Copy `.env.example` to `.env` and fill in `BOT_TOKEN` and `ADMIN_IDS`.
4. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

5. Load `.env` and run:

   ```bash
   set -a
   source .env
   set +a
   python bot.py
   ```

6. Send `/admin` to the bot. Set the invite link and payment log channel.
7. Add the bot to the VIP group as an administrator. Inside that group, a configured admin sends `/setgroup`. Give the bot permission to invite/approve users and ban users.

## Telegram configuration

- Add the bot to your private log channel and grant permission to post messages. Set `/setchannel @YourLogChannel` (or its numeric chat ID).
- Create a private group invite link with **join requests enabled**. Set it through the admin panel or `/setlink https://t.me/+...`. The bot auto-approves eligible requests; admins do not need to approve them manually.
- In the VIP group, an ID in `ADMIN_IDS` must run `/setgroup` once.
- The bot must remain an administrator with permission to approve join requests and ban/remove members.
- Keep `ADMIN_IDS` restricted to trusted numeric Telegram user IDs. Do not share the bot token.
- Test with Telegram's test environment before accepting real payments.

## Payment and access notes

- Each subscription payment grants 30 days from the current expiry (if still active) or from the payment time; there is no automatic recurring charge.
- `/check` reports the remaining time. A scheduled job checks expiries hourly, removes expired members from the configured group, and sends the requested private notification.
- The bot automatically approves join requests only when the requesting Telegram user has an active subscription. Requests without active access are declined.
- `/add N` and `?start=addN` are voluntary one-off Stars payments only. They do not grant VIP access.
- Stars payments for digital goods/services inside Telegram must use Telegram Stars (`XTR`). The bot records the `telegram_payment_charge_id` for reconciliation/refunds.
- The invite URL is sent after a successful subscription payment in this format: “Thank you for subscribing. Here's your link : … Use /check to check your subscription”.
- The bot stores payment metadata in a local SQLite database. Back up the database securely.
