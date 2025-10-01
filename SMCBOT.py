# SMCBot.py
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button
import sqlite3
import asyncio
import random
import datetime

# ---------- CONFIG ----------
INTENTS = discord.Intents.all()
BOT_PREFIX = "!"
DB_PATH = "moderation.db"
# ----------------------------

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=INTENTS)

# ---------- DATABASE SETUP ----------
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# base tables
c.execute("""
CREATE TABLE IF NOT EXISTS punishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    action TEXT,
    reason TEXT,
    moderator_id INTEGER,
    timestamp TEXT DEFAULT (datetime('now'))
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    modlog_channel_id INTEGER,
    suggest_channel_id INTEGER
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    message TEXT,
    author_id INTEGER,
    added_by INTEGER,
    timestamp TEXT DEFAULT (datetime('now'))
)
""")

# Simple migration fallback if older DB missing columns
try:
    c.execute("ALTER TABLE settings ADD COLUMN suggest_channel_id INTEGER")
except sqlite3.OperationalError:
    pass

conn.commit()
# ------------------------------------

# ---------- HELPERS ----------
def db_insert_punishment(guild_id, user_id, action, reason, moderator_id):
    c.execute(
        "INSERT INTO punishments (guild_id, user_id, action, reason, moderator_id) VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, action, reason, moderator_id)
    )
    conn.commit()

async def send_modlog(guild: discord.Guild, title: str, description: str, color=discord.Color.red()):
    c.execute("SELECT modlog_channel_id FROM settings WHERE guild_id = ?", (guild.id,))
    row = c.fetchone()
    if row and row[0]:
        channel = guild.get_channel(row[0])
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.timestamp = datetime.datetime.utcnow()
            await channel.send(embed=embed)

def format_timestamp_iso(ts_str: str):
    # ts_str is in 'YYYY-MM-DD HH:MM:SS' from sqlite default; try to parse
    try:
        dt = datetime.datetime.fromisoformat(ts_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str
# -----------------------------

# ---------- READY EVENT ----------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
# ------------------------------


# ---------------- MODLOG / SETTINGS ----------------

@bot.tree.command(name="setmodlog", description="Set the channel where moderation logs are sent")
@app_commands.checks.has_permissions(administrator=True)
async def setmodlog(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, modlog_channel_id, suggest_channel_id) VALUES (?, COALESCE((SELECT modlog_channel_id FROM settings WHERE guild_id = ?), ?), COALESCE((SELECT suggest_channel_id FROM settings WHERE guild_id = ?), NULL))",
              (interaction.guild.id, interaction.guild.id, channel.id, interaction.guild.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Mod log channel set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="setsuggestchannel", description="Set channel where suggestions are posted")
@app_commands.checks.has_permissions(administrator=True)
async def setsuggestchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, modlog_channel_id, suggest_channel_id) VALUES (?, COALESCE((SELECT modlog_channel_id FROM settings WHERE guild_id = ?), NULL), ?)",
              (interaction.guild.id, interaction.guild.id, channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Suggest channel set to {channel.mention}", ephemeral=True)


# ---------------- MODERATION: kick / ban / mute / unmute ----------------

@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.kick(reason=reason)
        db_insert_punishment(interaction.guild.id, member.id, "kick", reason, interaction.user.id)
        await interaction.response.send_message(f"🔨 {member} kicked (reason: {reason})", ephemeral=True)
        await send_modlog(interaction.guild, "🔨 User Kicked",
                          f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}",
                          discord.Color.dark_red())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    try:
        await member.ban(reason=reason)
        db_insert_punishment(interaction.guild.id, member.id, "ban", reason, interaction.user.id)
        await interaction.response.send_message(f"⛔ {member} banned (reason: {reason})", ephemeral=True)
        await send_modlog(interaction.guild, "⛔ User Banned",
                          f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}",
                          discord.Color.dark_red())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@bot.tree.command(name="mute", description="Mute a user (duration in minutes, 0 = indefinite)")
@app_commands.checks.has_permissions(manage_roles=True)
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int = 0, reason: str = "No reason provided"):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await interaction.guild.create_role(name="Muted", reason="Create muted role")
        for ch in interaction.guild.channels:
            try:
                await ch.set_permissions(muted_role, speak=False, send_messages=False, add_reactions=False)
            except Exception:
                pass

    try:
        await member.add_roles(muted_role, reason=reason)
        db_insert_punishment(interaction.guild.id, member.id, f"mute ({duration} min)" if duration > 0 else "mute (indefinite)", reason, interaction.user.id)
        await interaction.response.send_message(f"🤐 {member} muted. Reason: {reason}", ephemeral=True)
        await send_modlog(interaction.guild, "🤐 User Muted",
                          f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}\n**Duration:** {duration} min",
                          discord.Color.orange())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)
        return

    if duration > 0:
        # schedule auto-unmute
        async def unmute_later():
            await asyncio.sleep(duration * 60)
            try:
                await member.remove_roles(muted_role)
                await send_modlog(interaction.guild, "🔊 User Unmuted (auto)",
                                  f"**User:** {member.mention}\n**Reason:** mute expired",
                                  discord.Color.green())
            except Exception:
                pass
        bot.loop.create_task(unmute_later())


@bot.tree.command(name="unmute", description="Unmute a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
            await interaction.response.send_message(f"🔊 {member} unmuted.", ephemeral=True)
            await send_modlog(interaction.guild, "🔊 User Unmuted",
                              f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}",
                              discord.Color.green())
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {member} is not muted.", ephemeral=True)


# ---------------- WARNINGS ----------------

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    db_insert_punishment(interaction.guild.id, member.id, "warn", reason, interaction.user.id)
    await interaction.response.send_message(f"⚠️ {member} warned (reason: {reason})", ephemeral=True)
    await send_modlog(interaction.guild, "⚠️ User Warned",
                      f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}",
                      discord.Color.yellow())

@bot.tree.command(name="warnings", description="Show warnings for a user")
@app_commands.checks.has_permissions(kick_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT message, timestamp, added_by FROM (SELECT reason as message, timestamp, moderator_id as added_by FROM punishments WHERE guild_id = ? AND user_id = ? AND action = 'warn' ORDER BY id)", (interaction.guild.id, member.id))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"✅ {member} has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=discord.Color.yellow())
    for i, (reason, timestamp, mod_id) in enumerate(rows, start=1):
        mod = interaction.guild.get_member(mod_id)
        mod_name = mod.mention if mod else f"ID {mod_id}"
        embed.add_field(name=f"#{i}", value=f"**Reason:** {reason}\n**Moderator:** {mod_name}\n**Date:** {format_timestamp_iso(timestamp)}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="clearwarnings", description="Clear all warnings for a user")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    c.execute("DELETE FROM punishments WHERE guild_id = ? AND user_id = ? AND action = 'warn'", (interaction.guild.id, member.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Cleared warnings for {member}.", ephemeral=True)
    await send_modlog(interaction.guild, "🧹 Warnings Cleared",
                      f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}",
                      discord.Color.green())


# ---------------- MESSAGES / CHANNEL ----------------

@bot.tree.command(name="clear", description="Delete a number of messages in the channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)
    await send_modlog(interaction.guild, "🧹 Messages Cleared",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {interaction.channel.mention}\n**Amount:** {len(deleted)}",
                      discord.Color.green())

@bot.tree.command(name="purgeuser", description="Delete recent messages from a specific user in this channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def purgeuser(interaction: discord.Interaction, member: discord.Member, amount: int):
    def check(m):
        return m.author.id == member.id
    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.response.send_message(f"🧹 Deleted {len(deleted)} messages from {member}.", ephemeral=True)
    await send_modlog(interaction.guild, "🧹 User Purged",
                      f"**Moderator:** {interaction.user.mention}\n**User:** {member.mention}\n**Amount:** {len(deleted)}",
                      discord.Color.green())

@bot.tree.command(name="slowmode", description="Set slowmode in this channel (seconds)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int):
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"🐌 Set slowmode to {seconds} seconds.", ephemeral=True)
        await send_modlog(interaction.guild, "🐌 Slowmode Changed",
                          f"**Moderator:** {interaction.user.mention}\n**Channel:** {interaction.channel.mention}\n**Delay:** {seconds} sec",
                          discord.Color.orange())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔒 {channel.mention} locked.", ephemeral=True)
    await send_modlog(interaction.guild, "🔒 Channel Locked",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}",
                      discord.Color.red())

@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔓 {channel.mention} unlocked.", ephemeral=True)
    await send_modlog(interaction.guild, "🔓 Channel Unlocked",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}",
                      discord.Color.green())


# ---------------- ROLES ----------------

@bot.tree.command(name="addrole", description="Add a role to a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    try:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ Added {role.mention} to {member}.", ephemeral=True)
        await send_modlog(interaction.guild, "➕ Role Added",
                          f"**User:** {member.mention}\n**Role:** {role.mention}\n**Moderator:** {interaction.user.mention}",
                          discord.Color.blue())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@bot.tree.command(name="removerole", description="Remove a role from a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    try:
        await member.remove_roles(role)
        await interaction.response.send_message(f"✅ Removed {role.mention} from {member}.", ephemeral=True)
        await send_modlog(interaction.guild, "➖ Role Removed",
                          f"**User:** {member.mention}\n**Role:** {role.mention}\n**Moderator:** {interaction.user.mention}",
                          discord.Color.blue())
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)


# ---------------- ANNOUNCE ----------------

@bot.tree.command(name="announce", description="Send an announcement (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, title: str, message: str):
    embed = discord.Embed(title=title, description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Announcement by {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await channel.send(embed=embed)
    await interaction.response.send_message(f"📢 Announcement sent to {channel.mention}", ephemeral=True)
    await send_modlog(interaction.guild, "📢 Announcement Made",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}\n**Title:** {title}",
                      discord.Color.gold())


# ---------------- SUGGESTIONS ----------------

@bot.tree.command(name="suggest", description="Submit a suggestion")
async def suggest(interaction: discord.Interaction, suggestion: str):
    c.execute("SELECT suggest_channel_id FROM settings WHERE guild_id = ?", (interaction.guild.id,))
    row = c.fetchone()
    if not row or not row[0]:
        await interaction.response.send_message("⚠️ Suggest channel not set. Ask an admin to run /setsuggestchannel.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(row[0])
    if not channel:
        await interaction.response.send_message("⚠️ Suggest channel not found (maybe deleted).", ephemeral=True)
        return
    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=discord.Color.blue())
    embed.set_footer(text=f"Suggested by {interaction.user}", icon_url=interaction.user.display_avatar.url)
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
    except Exception:
        pass
    await interaction.response.send_message("✅ Your suggestion was submitted!", ephemeral=True)


# ---------------- TICKET SYSTEM ----------------

class TicketCloseView(View):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.channel = channel

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # only allow staff/mods to close OR the ticket opener
        if interaction.user.guild_permissions.manage_messages or interaction.user == self.channel.guild.owner:
            try:
                await self.channel.delete()
                await interaction.response.send_message("✅ Ticket closed.", ephemeral=True)
            except Exception:
                await interaction.response.send_message("⚠️ Could not delete channel.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You don't have permission to close this ticket.", ephemeral=True)

class TicketCreateView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.primary)
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        # create a channel with user name; ensure unique
        base_name = f"ticket-{interaction.user.name}"
        name = base_name
        i = 1
        while discord.utils.get(guild.channels, name=name):
            name = f"{base_name}-{i}"
            i += 1
        channel = await guild.create_text_channel(name=name, overwrites=overwrites, topic=f"Ticket for {interaction.user} (created by button)")
        await channel.send(f"{interaction.user.mention} support will be with you shortly.")
        # add close button
        await channel.send("Click the button below to close this ticket:", view=TicketCloseView(channel))
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)


@bot.tree.command(name="ticketpanel", description="Send a ticket panel (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def ticketpanel(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Support Tickets", description="Click the button below to open a support ticket.", color=discord.Color.blue())
    view = TicketCreateView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Ticket panel posted.", ephemeral=True)


# ---------------- QUOTES (quote, quotes, unquote) ----------------

@bot.tree.command(name="quote", description="Save a quote for a user")
async def quote(interaction: discord.Interaction, member: discord.Member, *, message: str):
    c.execute("INSERT INTO quotes (guild_id, message, author_id, added_by) VALUES (?, ?, ?, ?)",
              (interaction.guild.id, message, member.id, interaction.user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Quote saved for {member}.", ephemeral=True)
    await send_modlog(interaction.guild, "💬 Quote Added",
                      f"**User (quote):** {member.mention}\n**Added by:** {interaction.user.mention}\n**Quote:** {message}",
                      discord.Color.teal())

@bot.tree.command(name="quotes", description="Show quotes of a user")
async def quotes(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT id, message, added_by, timestamp FROM quotes WHERE guild_id = ? AND author_id = ? ORDER BY id", (interaction.guild.id, member.id))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"❌ No quotes found for {member}.", ephemeral=True)
        return
    embed = discord.Embed(title=f"💬 Quotes for {member}", color=discord.Color.teal())
    for i, (qid, msg, added_by, timestamp) in enumerate(rows, start=1):
        embed.add_field(name=f"#{i}", value=f"“{msg}”\n— added by <@{added_by}> • {format_timestamp_iso(timestamp)}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="unquote", description="Remove a quote by index (use index from /quotes output)")
@app_commands.checks.has_permissions(manage_messages=True)
async def unquote(interaction: discord.Interaction, member: discord.Member, index: int):
    c.execute("SELECT id, message FROM quotes WHERE guild_id = ? AND author_id = ? ORDER BY id", (interaction.guild.id, member.id))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"❌ No quotes found for {member}.", ephemeral=True)
        return
    if index < 1 or index > len(rows):
        await interaction.response.send_message(f"❌ Invalid index. Choose 1..{len(rows)}", ephemeral=True)
        return
    quote_id, quote_msg = rows[index - 1]
    c.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    conn.commit()
    await interaction.response.send_message(f"✅ Removed quote #{index} for {member}.", ephemeral=True)
    await send_modlog(interaction.guild, "🗑️ Quote Removed",
                      f"**Moderator:** {interaction.user.mention}\n**User:** {member.mention}\n**Index:** {index}\n**Quote:** {quote_msg}",
                      discord.Color.orange())


# ---------------- GIVEAWAY ----------------

class GiveawayView(View):
    def __init__(self, prize: str, duration: int):
        super().__init__(timeout=duration)
        self.prize = prize
        self.entries = set()

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.entries:
            await interaction.response.send_message("⚠️ You already joined!", ephemeral=True)
            return
        self.entries.add(interaction.user.id)
        await interaction.response.send_message("✅ You joined the giveaway!", ephemeral=True)

    async def on_timeout(self):
        # pick a winner and post results on the message's channel
        channel = self.message.channel
        if self.entries:
            winner_id = random.choice(list(self.entries))
            winner = channel.guild.get_member(winner_id)
            await channel.send(f"🎉 Congratulations {winner.mention if winner else f'<@{winner_id}>'}! You won **{self.prize}**!")
        else:
            await channel.send("❌ No entries for the giveaway.")

@bot.tree.command(name="giveaway", description="Start a giveaway (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction, prize: str, duration: int):
    embed = discord.Embed(title="🎉 Giveaway!", description=f"Prize: **{prize}**\nDuration: {duration}s", color=discord.Color.gold())
    view = GiveawayView(prize, duration)
    msg = await interaction.channel.send(embed=embed, view=view)
    view.message = msg  # store reference
    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)


# ---------------- POLL (mods create, everyone votes, mod-end button) ----------------

@bot.tree.command(name="poll", description="Create a poll (mods only). Options separated by |")
@app_commands.checks.has_permissions(manage_messages=True)
async def poll(interaction: discord.Interaction, question: str, options: str):
    options_list = [opt.strip() for opt in options.split("|") if opt.strip()]
    if len(options_list) < 2:
        await interaction.response.send_message("❌ You need at least 2 options (separate with |).", ephemeral=True)
        return

    class PollView(View):
        def __init__(self):
            super().__init__(timeout=None)
            self.votes = {opt: set() for opt in options_list}
            # create vote buttons
            for opt in options_list:
                btn = Button(label=opt, style=discord.ButtonStyle.primary)
                btn.callback = self.make_vote_callback(opt)
                self.add_item(btn)
            # end button
            end_btn = Button(label="End Poll", style=discord.ButtonStyle.danger)
            end_btn.callback = self.end_poll
            self.add_item(end_btn)

        def make_vote_callback(self, option):
            async def cb(interaction: discord.Interaction):
                uid = interaction.user.id
                if uid in self.votes[option]:
                    self.votes[option].remove(uid)
                else:
                    self.votes[option].add(uid)
                # update embed
                await self.update_message(interaction)
                # acknowledge without a visible message
                await interaction.response.defer()
            return cb

        async def update_message(self, interaction: discord.Interaction):
            desc = ""
            for opt, voters in self.votes.items():
                desc += f"**{opt}** - {len(voters)} votes\n"
            embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.green())
            await interaction.message.edit(embed=embed, view=self)

        async def end_poll(self, interaction: discord.Interaction):
            # only mods can end
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("❌ Only mods can end the poll.", ephemeral=True)
                return
            # disable buttons
            for child in list(self.children):
                child.disabled = True
            desc = ""
            for opt, voters in self.votes.items():
                desc += f"**{opt}** - {len(voters)} votes\n"
            embed = discord.Embed(title=f"📊 {question} (Final Results)", description=desc, color=discord.Color.red())
            # Use response.edit_message as our final interaction response
            await interaction.response.edit_message(embed=embed, view=self)

    view = PollView()
    desc = "\n".join([f"**{opt}** - 0 votes" for opt in options_list])
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.green())
    await interaction.response.send_message(embed=embed, view=view)


# ---------------- INFO: roleinfo, serverstats ----------------

@bot.tree.command(name="roleinfo", description="Get information about a role")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    perms_list = [name.replace("_", " ").title() for name, value in role.permissions if value]
    perms = ", ".join(perms_list) if perms_list else "None"
    embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Members", value=len(role.members), inline=True)
    embed.add_field(name="Mentionable", value=role.mentionable, inline=True)
    embed.add_field(name="Hoisted", value=role.hoist, inline=True)
    embed.add_field(name="Created At", value=role.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Permissions", value=(perms[:1024] + "…") if len(perms) > 1024 else perms, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="serverstats", description="Get statistics about this server")
async def serverstats(interaction: discord.Interaction):
    g = interaction.guild
    total = g.member_count
    humans = len([m for m in g.members if not m.bot])
    bots = len([m for m in g.members if m.bot])
    text_ch = len(g.text_channels)
    voice_ch = len(g.voice_channels)
    roles = len(g.roles)
    boosts = g.premium_subscription_count or 0
    embed = discord.Embed(title=f"Server Stats: {g.name}", color=discord.Color.blurple())
    embed.add_field(name="Total Members", value=total)
    embed.add_field(name="Humans", value=humans)
    embed.add_field(name="Bots", value=bots)
    embed.add_field(name="Text Channels", value=text_ch)
    embed.add_field(name="Voice Channels", value=voice_ch)
    embed.add_field(name="Roles", value=roles)
    embed.add_field(name="Boosts", value=boosts)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- AUDIT LOG ----------------

@bot.tree.command(name="auditlog", description="Show recent moderation actions (from DB)")
@app_commands.checks.has_permissions(administrator=True)
async def auditlog(interaction: discord.Interaction, limit: int = 10):
    c.execute("SELECT user_id, action, reason, moderator_id, timestamp FROM punishments WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (interaction.guild.id, limit))
    rows = c.fetchall()
    embed = discord.Embed(title="📜 Audit Log (recent actions)", color=discord.Color.purple())
    if not rows:
        embed.description = "No moderation actions found."
    else:
        for user_id, action, reason, mod_id, timestamp in rows:
            embed.add_field(name=f"{action.title()} • {format_timestamp_iso(timestamp)}",
                            value=f"User: <@{user_id}>\nMod: <@{mod_id}>\nReason: {reason}",
                            inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="userinfo", description="Get information about a user")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [role.mention for role in member.roles if role != interaction.guild.default_role]
    embed = discord.Embed(title=f"User Info - {member}", color=discord.Color.blurple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Bot?", value=member.bot, inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=False)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=False)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)



from discord.ui import View, Button

class ReactionRoleView(View):
    def __init__(self, role_dict):
        super().__init__(timeout=None)
        self.role_dict = role_dict
        for emoji, role_id in role_dict.items():
            self.add_item(RoleButton(emoji, role_id))

class RoleButton(Button):
    def __init__(self, emoji, role_id):
        super().__init__(style=discord.ButtonStyle.secondary, label=str(emoji))
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"✅ Removed {role.mention}", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Added {role.mention}", ephemeral=True)

# Slash command
@bot.tree.command(name="rr", description="Create a reaction role message")
async def rr(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    description: str,
    roles: str  # Comma-separated role IDs or mentions
):
    # Only allow mods
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission.", ephemeral=True)
        return

    role_list = [r.strip() for r in roles.split(",")]
    role_dict = {}

    for r in role_list:
        role = None
        if r.isdigit():
            role = interaction.guild.get_role(int(r))
        elif r.startswith("<@&") and r.endswith(">"):
            role_id = int(r[3:-1])
            role = interaction.guild.get_role(role_id)
        if role:
            emojis = ["🔹", "🔸", "🔺", "🔻", "⭐", "🌟", "💎", "🔥", "🎯", "🎵"]  # add more if needed
        role_dict = {}

    for i, r in enumerate(role_list):
    		role = None
    if r.isdigit():
    		role = interaction.guild.get_role(int(r))
    elif r.startswith("<@&") and r.endswith(">"):
        	role_id = int(r[3:-1])
        	role = interaction.guild.get_role(role_id)
    if role:
        	emoji = emojis[i % len(emojis)]  # assign a unique emoji
        	role_dict[emoji] = role.id

    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.add_field(name="Roles", value="\n".join([f"{emoji} - {interaction.guild.get_role(rid).mention}" for emoji, rid in role_dict.items()]))

    view = ReactionRoleView(role_dict)
    await channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ Reaction role message sent to {channel.mention}", ephemeral=True)


# ---------------- ERROR HANDLING ----------------

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
    else:
        # Try to send ephemeral error; fallback to printing
        try:
            await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)
        except Exception:
            print("Error while sending command error:", error)

# ---------- RUN ----------
if __name__ == "__main__":
    bot.run("MTQyMjY2OTcwOTE3Nzc4NjUzOA.GwLmnx.m570ac-luHjA4SnAkGrirdjVojxhkCZp7sR4Ig")
