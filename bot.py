import discord
from discord.ext import commands, tasks
import json
import re
from datetime import datetime, timezone, timedelta

# ====== CONFIG ======
import os
TOKEN = os.getenv("DISCORD_TOKEN")
DAILY_CHANNEL_ID = 682623773442179072
NOTIFY_HOUR_CET = 13  # 13:00 CET for daily report
DATA_FILE = "power_data.json"

# ====== BOT SETUP ======
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ====== LOAD & SAVE ======
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

power_data = load_data()

# ====== PARSE DURATION ======
def parse_power_duration(duration_str):
    """
    Parses a string like '19d 17h 52m' and returns total minutes.
    """
    pattern = r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?"
    match = re.fullmatch(pattern, duration_str.strip())
    if not match:
        return None

    days, hours, minutes = match.groups(default="0")
    total_minutes = int(days)*24*60 + int(hours)*60 + int(minutes)
    return total_minutes

# ====== COMMANDS ======
@bot.command(name="setpower")
async def set_power(ctx, base_name: str, *, power_str: str):
    """
    Set power for a specific base.
    Example: !setpower Alpha 19d 17h 52m
    """
    total_minutes = parse_power_duration(power_str)
    if total_minutes is None:
        await ctx.send("❌ Invalid format! Use like `!setpower Alpha 19d 17h 52m`")
        return

    user_id = str(ctx.author.id)
    now_iso = datetime.now(timezone(timedelta(hours=1))).isoformat()  # CET

    # Initialize list if first base for this user
    if user_id not in power_data:
        power_data[user_id] = []

    # Check if base already exists, replace it
    updated = False
    for entry in power_data[user_id]:
        if entry["base_name"].lower() == base_name.lower():
            entry.update({
                "raw": power_str,
                "minutes": total_minutes,
                "timestamp": now_iso
            })
            updated = True
            break

    # If new base, append
    if not updated:
        power_data[user_id].append({
            "base_name": base_name,
            "raw": power_str,
            "minutes": total_minutes,
            "timestamp": now_iso
        })

    save_data(power_data)
    await ctx.send(f"✅ {ctx.author.display_name}, base **{base_name}** set to **{power_str}** ({total_minutes} minutes).")

@bot.command(name="mypower")
async def my_power(ctx):
    """Show all bases for the user."""
    user_id = str(ctx.author.id)
    if user_id not in power_data or len(power_data[user_id]) == 0:
        await ctx.send("You haven’t set any bases yet! Use `!setpower <BaseName> <Duration>`.")
        return

    lines = [f"🔋 {ctx.author.display_name}'s Bases:"]
    for entry in power_data[user_id]:
        lines.append(f"{entry['base_name']}: {entry['raw']} (last updated {entry['timestamp']})")
    await ctx.send("\n".join(lines))

@bot.command(name="listpower")
async def list_power(ctx):
    """List all users and all their bases."""
    if not power_data:
        await ctx.send("No power levels have been set yet.")
        return

    lines = ["📋 **All Power Levels**"]
    for user_entries in power_data.values():
        for entry in user_entries:
            lines.append(f"{entry['base_name']} ({entry['name'] if 'name' in entry else 'User'}): {entry['raw']} ✦ updated {entry['timestamp']}")
    await ctx.send("\n".join(lines))

# ====== DAILY REPORT TASK ======
CET = timezone(timedelta(hours=1))  # Fixed CET offset

@tasks.loop(minutes=1)
async def daily_report():
    now = datetime.now(CET)
    if now.hour == NOTIFY_HOUR_CET and now.minute == 0:
        channel = bot.get_channel(682623773442179072)
        if not channel:
            return
        if not power_data:
            await channel.send("No power levels set yet! Use `!setpower` to add your data.")
            return

        lines = ["📅 **Daily Power Report**"]
        for user_entries in power_data.values():
            for entry in user_entries:
                lines.append(f"{entry['base_name']} ({entry['raw']})")
        await channel.send("\n".join(lines))

# ====== START LOOP IN on_ready ======
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not daily_report.is_running():
        daily_report.start()

# ====== RUN ======
bot.run(TOKEN)
