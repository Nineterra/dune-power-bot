import os
import discord
from discord.ext import commands, tasks
import re
from datetime import datetime
from pytz import UTC
import psycopg2

# ===== CONFIG =====
TOKEN = os.environ["DISCORD_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
DAILY_CHANNEL_ID = None  # will be set with a command

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== DATABASE HELPERS =====
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS base_power (
                    user_id TEXT,
                    user_name TEXT,
                    base_name TEXT,
                    total_minutes INTEGER,
                    set_at TIMESTAMPTZ,
                    warned BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY(user_id, base_name)
                )
            """)
        conn.commit()

def set_base_power(uid, user_name, base, total_minutes):
    now_utc = datetime.now(UTC)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO base_power (user_id, user_name, base_name, total_minutes, set_at, warned)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                ON CONFLICT(user_id, base_name)
                DO UPDATE SET total_minutes = EXCLUDED.total_minutes,
                              set_at = EXCLUDED.set_at,
                              warned = FALSE
            """, (uid, user_name, base, total_minutes, now_utc))
        conn.commit()

def get_user_bases(uid):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT base_name, total_minutes, set_at, warned
                FROM base_power
                WHERE user_id=%s
            """, (uid,))
            rows = cur.fetchall()
    return [{"base_name": r[0], "total_minutes": r[1], "set_at": r[2], "warned": r[3]} for r in rows]

def get_all_bases():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, user_name, base_name, total_minutes, set_at, warned
                FROM base_power
            """)
            rows = cur.fetchall()
    return [{"user_id": r[0], "user_name": r[1], "base_name": r[2], "total_minutes": r[3], "set_at": r[4], "warned": r[5]} for r in rows]

def set_warned(uid, base):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE base_power
                SET warned = TRUE
                WHERE user_id = %s AND base_name = %s
            """, (uid, base))
        conn.commit()

def delete_base(uid, base):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM base_power
                WHERE user_id=%s AND base_name=%s
            """, (uid, base))
        conn.commit()

# ===== TIME PARSER =====
def parse_duration(text):
    pattern = r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?"
    match = re.fullmatch(pattern.strip().lower(), text)
    if not match:
        return None
    d, h, m = match.groups(default="0")
    return int(d)*1440 + int(h)*60 + int(m)

def format_minutes(minutes):
    if minutes <= 0:
        return "Expired"
    d, r = divmod(minutes, 1440)
    h, m = divmod(r, 60)
    return f"{d}d {h}h {m}m"

def get_emoji(minutes):
    if minutes <= 1440:        # less than 24h
        return "🔴"
    elif minutes <= 2880:      # less than 48h
        return "🟡"
    else:
        return "🟢"

# ===== COMMANDS =====
@bot.command()
async def setpower(ctx, base: str, *, duration: str):
    minutes = parse_duration(duration)
    if minutes is None:
        await ctx.send("❌ Use format like: `19d 17h 52m`")
        return
    uid = str(ctx.author.id)
    user_name = ctx.author.display_name
    set_base_power(uid, user_name, base, minutes)
    await ctx.send(f"✅ **{base}** set to `{duration}`")

@bot.command()
async def mypower(ctx):
    uid = str(ctx.author.id)
    bases = get_user_bases(uid)
    if not bases:
        await ctx.send("No bases set.")
        return

    now_utc = datetime.now(UTC)
    lines = [f"🔋 **{ctx.author.display_name}'s Bases:**"]
    for info in bases:
        set_at = info["set_at"]
        if set_at.tzinfo is None:
            set_at = UTC.localize(set_at)
        elapsed = int((now_utc - set_at).total_seconds() / 60)
        remaining = info["total_minutes"] - elapsed
        lines.append(f"{get_emoji(remaining)} **{info['base_name']}** → {format_minutes(remaining)}")
    await ctx.send("\n".join(lines))

@bot.command()
async def setdailychannel(ctx, channel: discord.TextChannel):
    global DAILY_CHANNEL_ID
    DAILY_CHANNEL_ID = channel.id
    await ctx.send(f"✅ Daily report channel set to {channel.mention}")

# ===== TRACKER LOOP =====
@tasks.loop(minutes=1)
async def tracker():
    now_utc = datetime.now(UTC)
    all_bases = get_all_bases()

    for entry in all_bases:
        uid = entry["user_id"]
        base = entry["base_name"]
        total_minutes = entry["total_minutes"]
        set_at = entry["set_at"]
        warned = entry["warned"]
        user_name = entry["user_name"]

        if set_at.tzinfo is None:
            set_at = UTC.localize(set_at)

        elapsed = int((now_utc - set_at).total_seconds() / 60)
        remaining = total_minutes - elapsed

        # Expired → DM + delete
        if remaining <= 0:
            try:
                user = await bot.fetch_user(int(uid))
                await user.send(f"💀 **{base}** has expired and was removed.")
            except Exception as e:
                print(f"Failed DM expiry: {e}")
            delete_base(uid, base)
            continue

        # Warning <24h
        if remaining <= 1440 and not warned:
            set_warned(uid, base)
            try:
                user = await bot.fetch_user(int(uid))
                await user.send(f"⚠️ **{base}** has less than 24h remaining ({format_minutes(remaining)})")
            except Exception as e:
                print(f"Failed DM warning: {e}")

    # Daily report at 13:00 UTC
    if DAILY_CHANNEL_ID and now_utc.hour == 13 and now_utc.minute == 0:
        channel = bot.get_channel(DAILY_CHANNEL_ID)
        if channel:
            report = []
            for entry in get_all_bases():
                set_at = entry["set_at"]
                if set_at.tzinfo is None:
                    set_at = UTC.localize(set_at)
                elapsed = int((now_utc - set_at).total_seconds() / 60)
                remaining = entry["total_minutes"] - elapsed
                if remaining <= 0:
                    continue
                report.append({
                    "base": entry["base_name"],
                    "owner": entry["user_name"],
                    "remaining": remaining
                })

            report.sort(key=lambda x: x["remaining"])  # lowest first
            lines = ["📅 **Daily Base Report (Lowest → Highest):**"]
            for item in report:
                lines.append(f"{get_emoji(item['remaining'])} **{item['base']}** (👤 {item['owner']}) → {format_minutes(item['remaining'])}")

            await channel.send("\n".join(lines))
            print("[Tracker] Daily report sent")

# ===== START =====
init_db()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not tracker.is_running():
        tracker.start()

bot.run(TOKEN)
