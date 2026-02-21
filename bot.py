import os
import discord
from discord.ext import commands, tasks
from datetime import datetime
from pytz import UTC
import psycopg2

# ===== CONFIG =====
TOKEN = os.environ["DISCORD_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== DATABASE HELPERS =====
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Track bases per guild
            cur.execute("""
                CREATE TABLE IF NOT EXISTS base_power (
                    user_id TEXT,
                    guild_id TEXT,
                    base_name TEXT,
                    total_minutes INTEGER,
                    set_at TIMESTAMPTZ,
                    warned BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY(user_id, guild_id, base_name)
                );
            """)
            # Config table (e.g., report channel per guild)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
        conn.commit()

def set_base_power(uid, guild_id, base, total_minutes):
    now_utc = datetime.now(UTC)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO base_power (user_id, guild_id, base_name, total_minutes, set_at, warned)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                ON CONFLICT(user_id, guild_id, base_name)
                DO UPDATE SET total_minutes = EXCLUDED.total_minutes,
                              set_at = EXCLUDED.set_at,
                              warned = FALSE;
            """, (uid, guild_id, base, total_minutes, now_utc))
        conn.commit()

def get_user_bases(uid, guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT base_name, total_minutes, set_at, warned
                FROM base_power
                WHERE user_id=%s AND guild_id=%s;
            """, (uid, guild_id))
            rows = cur.fetchall()
    return [{"base_name": r[0], "total_minutes": r[1], "set_at": r[2], "warned": r[3]} for r in rows]

def get_all_bases(guild_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, base_name, total_minutes, set_at, warned
                FROM base_power
                WHERE guild_id=%s;
            """, (guild_id,))
            rows = cur.fetchall()
    return [{"user_id": r[0], "base_name": r[1], "total_minutes": r[2], "set_at": r[3], "warned": r[4]} for r in rows]

def set_warned(uid, guild_id, base):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE base_power
                SET warned = TRUE
                WHERE user_id=%s AND guild_id=%s AND base_name=%s;
            """, (uid, guild_id, base))
        conn.commit()

def delete_base(user_id, guild_id, base_name):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM base_power
                WHERE user_id=%s AND guild_id=%s AND base_name=%s;
            """, (user_id, guild_id, base_name))
        conn.commit()

# ===== CONFIG DB =====
def set_config(key, value):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO config (key, value)
                VALUES (%s, %s)
                ON CONFLICT(key)
                DO UPDATE SET value = EXCLUDED.value;
            """, (key, value))
        conn.commit()

def get_config(key):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM config WHERE key=%s;", (key,))
            row = cur.fetchone()
    return row[0] if row else None

# ===== TIME PARSER =====
import re
def parse_duration(text):
    pattern = r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?"
    match = re.fullmatch(pattern.strip().lower(), text)
    if not match:
        return None
    d, h, m = match.groups(default="0")
    return int(d) * 1440 + int(h) * 60 + int(m)

def format_minutes(minutes):
    if minutes <= 0:
        return "Expired"
    d, r = divmod(minutes, 1440)
    h, m = divmod(r, 60)
    return f"{d}d {h}h {m}m"

def get_status_emoji(minutes):
    if minutes <= 1440:
        return "🔴"
    elif minutes <= 10080:
        return "🟠"
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
    guild_id = str(ctx.guild.id)
    set_base_power(uid, guild_id, base, minutes)
    await ctx.send(f"✅ **{base}** set to `{duration}`")

@bot.command()
async def mypower(ctx):
    uid = str(ctx.author.id)
    guild_id = str(ctx.guild.id)
    bases = get_user_bases(uid, guild_id)
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
        emoji = get_status_emoji(remaining)
        lines.append(f"{emoji} **{info['base_name']}** → {format_minutes(remaining)}")
    await ctx.send("\n".join(lines))

@bot.command()
async def remove(ctx, base: str):
    uid = str(ctx.author.id)
    guild_id = str(ctx.guild.id)
    delete_base(uid, guild_id, base)
    await ctx.send(f"🗑 Removed **{base}**.")

@bot.command()
async def setreportchannel(ctx):
    key = f"daily_report_channel_{ctx.guild.id}"
    set_config(key, str(ctx.channel.id))
    await ctx.send(f"✅ This channel is now set for daily reports.")

@bot.command()
async def report(ctx):
    await generate_report(ctx.guild)

# ===== REPORT LOGIC =====
async def generate_report(guild):
    key = f"daily_report_channel_{guild.id}"
    channel_id = get_config(key)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return

    now_utc = datetime.now(UTC)
    report_data = []
    for entry in get_all_bases(str(guild.id)):
        set_at = entry["set_at"]
        if set_at.tzinfo is None:
            set_at = UTC.localize(set_at)
        elapsed = int((now_utc - set_at).total_seconds() / 60)
        remaining = entry["total_minutes"] - elapsed
        if remaining <= 0:
            delete_base(entry["user_id"], str(guild.id), entry["base_name"])
            continue
        member = guild.get_member(int(entry["user_id"]))
        username = member.display_name if member else "Unknown"
        report_data.append({
            "user": username,
            "base_name": entry["base_name"],
            "remaining": remaining
        })

    # Sort lowest → highest
    report_data.sort(key=lambda x: x["remaining"])

    lines = ["📅 **Daily Base Power Report (Lowest → Highest):**\n"]
    for item in report_data:
        emoji = get_status_emoji(item["remaining"])
        lines.append(f"{emoji} **{item['base_name']}** ({item['user']}) → {format_minutes(item['remaining'])}")
    await channel.send("\n".join(lines))

# ===== TRACKER LOOP =====
@tasks.loop(minutes=1)
async def tracker():
    for guild in bot.guilds:
        now_utc = datetime.now(UTC)
        for entry in get_all_bases(str(guild.id)):
            uid = entry["user_id"]
            base = entry["base_name"]
            total_minutes = entry["total_minutes"]
            set_at = entry["set_at"]
            warned = entry["warned"]

            if set_at.tzinfo is None:
                set_at = UTC.localize(set_at)
            elapsed = int((now_utc - set_at).total_seconds() / 60)
            remaining = total_minutes - elapsed

            member = guild.get_member(int(uid))
            username = member.display_name if member else "Unknown"

            # Expired
            if remaining <= 0:
                if member:
                    try:
                        await member.send(f"💀 **{base}** has expired and was removed from tracking.")
                    except:
                        pass
                delete_base(uid, str(guild.id), base)
                continue

            # Warning < 1 day
            if remaining <= 1440 and not warned:
                set_warned(uid, str(guild.id), base)
                if member:
                    try:
                        await member.send(f"⚠️ **{base}** has less than 1 day remaining ({format_minutes(remaining)})")
                    except:
                        pass

    # Daily report at 13:00 UTC
    now_utc = datetime.now(UTC)
    if now_utc.hour == 13 and now_utc.minute == 0:
        for guild in bot.guilds:
            await generate_report(guild)

# ===== START =====
init_db()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not tracker.is_running():
        tracker.start()

bot.run(TOKEN)
