import os
import sqlite3
from datetime import date, timedelta

import discord
from discord import app_commands

# ---- Settings ----
TOKEN = os.environ["DISCORD_TOKEN"]  # set this as a secret/env variable
POINTS_PER_PAGE = 2

# (minimum streak days, multiplier), checked from highest to lowest
STREAK_TIERS = [(30, 2.5), (15, 2.0), (5, 1.5), (1, 1.0)]

# ---- Database ----
db = sqlite3.connect(os.environ.get("DB_PATH", "reading.db"))
db.execute(
    """CREATE TABLE IF NOT EXISTS reports (
        user_id INTEGER,
        day TEXT,
        pages INTEGER,
        book TEXT,
        points REAL
    )"""
)
# Upgrade an older database that has no points column
try:
    db.execute("ALTER TABLE reports ADD COLUMN points REAL")
except sqlite3.OperationalError:
    pass
db.execute("UPDATE reports SET points = pages * ? WHERE points IS NULL", (POINTS_PER_PAGE,))
db.commit()


def fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def multiplier_for(streak: int) -> float:
    for minimum, mult in STREAK_TIERS:
        if streak >= minimum:
            return mult
    return 1.0


def reading_days(user_id: int) -> set:
    rows = db.execute(
        "SELECT DISTINCT day FROM reports WHERE user_id=?", (user_id,)
    ).fetchall()
    return {r[0] for r in rows}


def current_streak(user_id: int, counting_today: bool = False) -> int:
    """Consecutive days of reading. Today counts if already reported (or counting_today)."""
    days = reading_days(user_id)
    today = date.today()
    if counting_today:
        days.add(str(today))
    d = today if str(today) in days else today - timedelta(days=1)
    streak = 0
    while str(d) in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def pages_today(user_id: int) -> int:
    return db.execute(
        "SELECT COALESCE(SUM(pages), 0) FROM reports WHERE user_id=? AND day=?",
        (user_id, str(date.today())),
    ).fetchone()[0]


def total_points(user_id: int) -> float:
    return db.execute(
        "SELECT COALESCE(SUM(points), 0) FROM reports WHERE user_id=?", (user_id,)
    ).fetchone()[0]


# ---- Bot ----
class ReadingBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()


bot = ReadingBot()


@bot.tree.command(name="read", description="Report pages you read today")
@app_commands.describe(pages="Number of pages read", book="Book title (optional)")
async def read(interaction: discord.Interaction, pages: app_commands.Range[int, 1], book: str = ""):
    uid = interaction.user.id
    streak = current_streak(uid, counting_today=True)
    mult = multiplier_for(streak)
    earned = pages * POINTS_PER_PAGE * mult

    db.execute(
        "INSERT INTO reports (user_id, day, pages, book, points) VALUES (?, ?, ?, ?, ?)",
        (uid, str(date.today()), pages, book.strip(), earned),
    )
    db.commit()

    title = f" of *{book.strip()}*" if book.strip() else ""
    await interaction.response.send_message(
        f"📖 {interaction.user.mention} read **{pages}** pages{title} "
        f"and earned **{fmt(earned)}** points!\n"
        f"🔥 {streak}-day streak ({mult}x) • Total: **{fmt(total_points(uid))}**"
    )


@bot.tree.command(name="points", description="See your total points and streak")
async def points(interaction: discord.Interaction):
    uid = interaction.user.id
    streak = current_streak(uid)
    await interaction.response.send_message(
        f"You have **{fmt(total_points(uid))}** points.\n"
        f"🔥 Streak: {streak} days ({multiplier_for(streak)}x) • "
        f"{pages_today(uid)} pages today",
        ephemeral=True,
    )


@bot.tree.command(name="leaderboard", description="Top readers")
async def leaderboard(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT user_id, SUM(points) AS p FROM reports GROUP BY user_id ORDER BY p DESC LIMIT 10"
    ).fetchall()
    if not rows:
        await interaction.response.send_message("No reading reported yet!")
        return
    lines = [f"**{i}.** <@{uid}> — {fmt(p)} points" for i, (uid, p) in enumerate(rows, start=1)]
    await interaction.response.send_message("🏆 **Leaderboard**\n" + "\n".join(lines))


bot.run(TOKEN)
