import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import pytz

# ===== CONFIG =====
TOKEN = "YOUR_BOT_TOKEN"
DATA_FILE = "power_data.json"
CET = pytz.timezone("Europe/Berlin")

# ===== BOT SETUP =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed for display names
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== LOAD DATA =====
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"bases": {}, "report_channel": None}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

# ===== HELPER FUNCTIONS =====
def get_remaining_minutes(last_update):
    last_time = datetime.fromisoformat(last_update)
    now = datetime.now(CET)
    expiry = last_time + timedelta(days=7)
    remaining = expiry - now
    return int(remaining.total_seconds() / 60)

def format_time(minutes):
    if minutes <= 0:
        return "EXPIRED"
    days = minutes // (60 * 24)
    hours = (minutes % (60 * 24)) // 60
    mins = minutes % 60
    return f"{days}d {hours}h {mins}m"

def get_color_emoji(minutes):
    if minutes <= 1440:
        return "🔴"
    elif minutes <= 4320:
        return "🟡"
    else:
        return "🟢"

# ===== COMMANDS =====

@bot.command()
async def setpower(ctx, base_name: str):
    user_id = str(ctx.author.id)
    display_name = ctx.author.display_name
    now = datetime.now(CET).isoformat()

    data["bases"][base_name] = {
        "owner_id": user_id,
        "owner_name": display_name,
        "last_update": now
    }

    save_data(data)

    await ctx.send(f"🔋 Power set for **{base_name}** by **{display_name}**!")

@bot.command()
async def mypower(ctx):
    user_id = str(ctx.author.id)
    found = False
    message = "🔋 **Your Bases:**\n"

    for base, info in data["bases"].items():
        if info["owner_id"] == user_id:
            remaining = get_remaining_minutes(info["last_update"])
            emoji = get_color_emoji(remaining)
            message += f"{emoji} {base}: {format_time(remaining)}\n"
            found = True

    if not found:
        message = "You don't have any tracked bases."

    await ctx.send(message)

@bot.command()
async def remove(ctx, base_name: str):
    if base_name not in data["bases"]:
        await ctx.send("Base not found.")
        return

    if data["bases"][base_name]["owner_id"] != str(ctx.author.id):
        await ctx.send("You can only remove your own bases.")
        return

    del data["bases"][base_name]
    save_data(data)

    await ctx.send(f"🗑️ Removed base **{base_name}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setreportchannel(ctx):
    data["report_channel"] = ctx.channel.id
    save_data(data)
    await ctx.send("📢 This channel is now set for daily power reports.")

# ===== DAILY REPORT =====

@tasks.loop(hours=24)
async def daily_report():
    if not data.get("report_channel"):
        return

    channel = bot.get_channel(data["report_channel"])
    if not channel:
        return

    report_lines = []
    now = datetime.now(CET)

    # Sort bases by remaining time (lowest first)
    sorted_bases = sorted(
        data["bases"].items(),
        key=lambda x: get_remaining_minutes(x[1]["last_update"])
    )

    for base, info in sorted_bases:
        remaining = get_remaining_minutes(info["last_update"])
        emoji = get_color_emoji(remaining)
        owner = info["owner_name"]
        time_left = format_time(remaining)

        report_lines.append(
            f"{emoji} **{base}** ({owner}) → {time_left}"
        )

    if not report_lines:
        report = "No bases are currently tracked."
    else:
        report = "📊 **Daily Power Report**\n\n" + "\n".join(report_lines)

    await channel.send(report)

@daily_report.before_loop
async def before_daily():
    await bot.wait_until_ready()

# ===== START =====

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    daily_report.start()

bot.run(TOKEN)
