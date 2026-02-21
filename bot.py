import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import pytz

# ===== CONFIG =====
TOKEN = "YOUR_BOT_TOKEN"
DATA_FILE = "power_data.json"
CET = pytz.timezone("Europe/Berlin")

# ===== LOAD / SAVE =====
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"bases": {}, "report_channel": None}
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"bases": {}, "report_channel": None}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ===== STATUS EMOJI LOGIC =====
def get_status_emoji(minutes_left):
    if minutes_left <= 1440:      # 1 day
        return "🔴"
    elif minutes_left <= 4320:    # 3 days
        return "🟡"
    else:
        return "🟢"

def format_remaining(td):
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"

# ===== COMMANDS =====

@bot.command()
async def setpower(ctx, base_name: str, days: int):
    expire_time = datetime.now(CET) + timedelta(days=days)

    data["bases"][base_name] = {
        "expires": expire_time.isoformat(),
        "owner_id": ctx.author.id,
        "owner_name": ctx.author.display_name
    }

    save_data()
    await ctx.send(f"🔋 Power set for **{base_name}** ({days} days).")

@bot.command()
async def mypower(ctx):
    found = False
    now = datetime.now(CET)
    message = "🔋 **Your Bases:**\n\n"

    for base, info in data["bases"].items():
        if info["owner_id"] == ctx.author.id:
            expire_time = datetime.fromisoformat(info["expires"])
            remaining = expire_time - now
            if remaining.total_seconds() <= 0:
                continue

            minutes_left = int(remaining.total_seconds() / 60)
            emoji = get_status_emoji(minutes_left)
            message += f"{emoji} **{base}** - {format_remaining(remaining)}\n"
            found = True

    if not found:
        await ctx.send("You have no active bases.")
    else:
        await ctx.send(message)

@bot.command()
async def remove(ctx, base_name: str):
    if base_name not in data["bases"]:
        await ctx.send("Base not found.")
        return

    if data["bases"][base_name]["owner_id"] != ctx.author.id:
        await ctx.send("You can only remove your own bases.")
        return

    del data["bases"][base_name]
    save_data()
    await ctx.send(f"🗑 Removed **{base_name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setreportchannel(ctx):
    data["report_channel"] = ctx.channel.id
    save_data()
    await ctx.send("📡 This channel is now set for daily reports.")

@bot.command()
async def report(ctx):
    await generate_report(ctx.channel)

# ===== REPORT LOGIC =====

async def generate_report(channel):
    if not data.get("report_channel"):
        return

    now = datetime.now(CET)
    report_lines = []
    expired = []

    for base, info in data["bases"].items():
        expire_time = datetime.fromisoformat(info["expires"])
        remaining = expire_time - now
        minutes_left = int(remaining.total_seconds() / 60)

        if minutes_left <= 0:
            expired.append(base)
            continue

        emoji = get_status_emoji(minutes_left)

        report_lines.append((
            minutes_left,
            f"{emoji} **{base}** (Owner: {info['owner_name']}) - {format_remaining(remaining)}"
        ))

    # Remove expired
    for base in expired:
        del data["bases"][base]

    save_data()

    if not report_lines:
        await channel.send("No active bases.")
        return

    report_lines.sort(key=lambda x: x[0])

    message = "🔋 **Dune Power Report** 🔋\n\n"
    for _, line in report_lines:
        message += line + "\n"

    await channel.send(message)

# ===== DAILY TASK =====

@tasks.loop(hours=24)
async def daily_report():
    await bot.wait_until_ready()

    if not data.get("report_channel"):
        return

    channel = bot.get_channel(data["report_channel"])
    if channel:
        await generate_report(channel)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not daily_report.is_running():
        daily_report.start()

# ===== RUN =====
bot.run(TOKEN)
