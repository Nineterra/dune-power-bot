import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime

TOKEN = "YOUR_BOT_TOKEN"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ---------------- #

conn = sqlite3.connect("bases.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS bases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    base_name TEXT,
    power INTEGER,
    expires_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    report_channel_id INTEGER
)
""")

conn.commit()

# ---------------- HELPER FUNCTIONS ---------------- #

def delete_expired_bases():
    now = datetime.datetime.utcnow().isoformat()
    cursor.execute("DELETE FROM bases WHERE expires_at <= ?", (now,))
    conn.commit()

def get_bases_sorted(guild_id):
    cursor.execute("""
    SELECT user_id, base_name, power, expires_at
    FROM bases
    WHERE guild_id = ?
    ORDER BY power ASC
    """, (guild_id,))
    return cursor.fetchall()

# ---------------- EVENTS ---------------- #

@bot.event
async def on_ready():
    await bot.tree.sync()
    daily_report.start()
    print(f"Logged in as {bot.user}")

# ---------------- COMMANDS ---------------- #

@bot.tree.command(name="addbase", description="Add a base")
@app_commands.describe(name="Base name", power="Base power", hours="How many hours until expiration")
async def add_base(interaction: discord.Interaction, name: str, power: int, hours: int):

    expires = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)

    cursor.execute("""
    INSERT INTO bases (guild_id, user_id, base_name, power, expires_at)
    VALUES (?, ?, ?, ?, ?)
    """, (
        interaction.guild.id,
        interaction.user.id,
        name,
        power,
        expires.isoformat()
    ))

    conn.commit()

    await interaction.response.send_message(
        f"🏰 Base **{name}** added!\n⚡ Power: {power}\n⏳ Expires in: {hours} hours",
        ephemeral=True
    )

@bot.tree.command(name="setreportchannel", description="Set the daily report channel")
async def set_report_channel(interaction: discord.Interaction, channel: discord.TextChannel):

    cursor.execute("""
    INSERT INTO settings (guild_id, report_channel_id)
    VALUES (?, ?)
    ON CONFLICT(guild_id)
    DO UPDATE SET report_channel_id=excluded.report_channel_id
    """, (interaction.guild.id, channel.id))

    conn.commit()

    await interaction.response.send_message(
        f"📢 Daily report channel set to {channel.mention}",
        ephemeral=True
    )

# ---------------- DAILY REPORT ---------------- #

@tasks.loop(hours=24)
async def daily_report():

    delete_expired_bases()

    for guild in bot.guilds:

        cursor.execute("SELECT report_channel_id FROM settings WHERE guild_id = ?", (guild.id,))
        result = cursor.fetchone()

        if not result:
            continue

        channel_id = result[0]
        channel = guild.get_channel(channel_id)

        if not channel:
            continue

        bases = get_bases_sorted(guild.id)

        if not bases:
            await channel.send("📭 No active bases today.")
            continue

        embed = discord.Embed(
            title="📊 Daily Base Report",
            color=discord.Color.blue()
        )

        description = ""

        for user_id, base_name, power, expires_at in bases:

            member = guild.get_member(user_id)
            owner_name = member.display_name if member else "Unknown User"

            expires_time = datetime.datetime.fromisoformat(expires_at)
            time_left = expires_time - datetime.datetime.utcnow()

            hours_left = int(time_left.total_seconds() // 3600)

            description += (
                f"🏰 **{base_name}**\n"
                f"👤 Owner: {owner_name}\n"
                f"⚡ Power: {power}\n"
                f"⏳ {hours_left}h remaining\n\n"
            )

        embed.description = description

        await channel.send(embed=embed)

# ---------------- AUTO DM NOTIFICATIONS ---------------- #

@tasks.loop(minutes=30)
async def expiration_check():

    now = datetime.datetime.utcnow()
    cursor.execute("SELECT id, user_id, base_name, expires_at FROM bases")
    bases = cursor.fetchall()

    for base_id, user_id, base_name, expires_at in bases:

        expire_time = datetime.datetime.fromisoformat(expires_at)
        time_left = expire_time - now

        if 0 < time_left.total_seconds() <= 3600:

            user = await bot.fetch_user(user_id)
            try:
                await user.send(f"⚠️ Your base **{base_name}** expires within 1 hour!")
            except:
                pass

bot.run(TOKEN)
