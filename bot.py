import discord
from discord.ext import commands, tasks
import asyncpg
import os
from datetime import datetime, timedelta
import pytz

# ===== CONFIG =====
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CET = pytz.timezone("Europe/Berlin")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = None

# ===== STATUS EMOJI =====
def get_status_emoji(minutes_left):
    if minutes_left <= 1440:
        return "🔴"
    elif minutes_left <= 4320:
        return "🟡"
    else:
        return "🟢"

def format_remaining(td):
    total = int(td.total_seconds())
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    return f"{days}d {hours}h {minutes}m"

# ===== DATABASE CONNECT =====
@bot.event
async def on_ready():
    global db
    db = await asyncpg.connect(DATABASE_URL)
    print(f"Logged in as {bot.user}")
    if not daily_report.is_running():
        daily_report.start()

# ===== COMMANDS =====

@bot.command()
async def setpower(ctx, base_name: str, days: int):
    expire_time = datetime.now(CET) + timedelta(days=days)

    await db.execute("""
        INSERT INTO bases (base_name, owner_id, owner_name, expires)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (base_name)
        DO UPDATE SET
            owner_id = EXCLUDED.owner_id,
            owner_name = EXCLUDED.owner_name,
            expires = EXCLUDED.expires
    """, base_name, ctx.author.id, ctx.author.display_name, expire_time)

    await ctx.send(f"🔋 Power set for **{base_name}** ({days} days).")

@bot.command()
async def mypower(ctx):
    rows = await db.fetch("SELECT * FROM bases WHERE owner_id = $1", ctx.author.id)

    if not rows:
        await ctx.send("You have no active bases.")
        return

    now = datetime.now(CET)
    lines = []

    for row in rows:
        remaining = row["expires"] - now
        if remaining.total_seconds() <= 0:
            continue

        minutes_left = int(remaining.total_seconds() / 60)
        emoji = get_status_emoji(minutes_left)
        lines.append((minutes_left, f"{emoji} **{row['base_name']}** - {format_remaining(remaining)}"))

    if not lines:
        await ctx.send("You have no active bases.")
        return

    lines.sort(key=lambda x: x[0])

    message = "🔋 **Your Bases:**\n\n"
    for _, line in lines:
        message += line + "\n"

    await ctx.send(message)

@bot.command()
async def remove(ctx, base_name: str):
    result = await db.execute("""
        DELETE FROM bases
        WHERE base_name = $1 AND owner_id = $2
    """, base_name, ctx.author.id)

    if result == "DELETE 0":
        await ctx.send("Base not found or not yours.")
    else:
        await ctx.send(f"🗑 Removed **{base_name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setreportchannel(ctx):
    await db.execute("""
        INSERT INTO config (key, value)
        VALUES ('report_channel', $1)
        ON CONFLICT (key)
        DO UPDATE SET value = EXCLUDED.value
    """, str(ctx.channel.id))

    await ctx.send("📡 This channel is now set for daily reports.")

@bot.command()
async def report(ctx):
    await generate_report(ctx.channel)

# ===== REPORT LOGIC =====

async def generate_report(channel):
    row = await db.fetchrow("SELECT value FROM config WHERE key = 'report_channel'")
    if not row:
        return

    now = datetime.now(CET)
    rows = await db.fetch("SELECT * FROM bases")

    lines = []

    for r in rows:
        remaining = r["expires"] - now

        if remaining.total_seconds() <= 0:
            await db.execute("DELETE FROM bases WHERE base_name = $1", r["base_name"])
            continue

        minutes_left = int(remaining.total_seconds() / 60)
        emoji = get_status_emoji(minutes_left)

        lines.append((
            minutes_left,
            f"{emoji} **{r['base_name']}** (Owner: {r['owner_name']}) - {format_remaining(remaining)}"
        ))

    if not lines:
        await channel.send("No active bases.")
        return

    lines.sort(key=lambda x: x[0])

    message = "🔋 **Dune Power Report** 🔋\n\n"
    for _, line in lines:
        message += line + "\n"

    await channel.send(message)

# ===== DAILY TASK =====

@tasks.loop(hours=24)
async def daily_report():
    await bot.wait_until_ready()

    row = await db.fetchrow("SELECT value FROM config WHERE key = 'report_channel'")
    if not row:
        return

    channel = bot.get_channel(int(row["value"]))
    if channel:
        await generate_report(channel)

# ===== RUN =====
bot.run(TOKEN)
