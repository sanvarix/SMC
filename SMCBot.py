import discord
from discord.ext import commands
from discord.ui import View, Button
import sqlite3
import asyncio
import random
from discord import app_commands, Interaction, Embed, ButtonStyle

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- SQLite setup ---
conn = sqlite3.connect("moderation.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS punishments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT,
    reason TEXT,
    moderator_id INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    modlog_channel_id INTEGER
)
""")
conn.commit()

c.execute("""
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    message TEXT,
    author_id INTEGER,
    added_by INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()


conn = sqlite3.connect("moderation.db")
c = conn.cursor()

# Try adding suggest_channel_id if it doesn't exist
try:
    c.execute("ALTER TABLE settings ADD COLUMN suggest_channel_id INTEGER")
except sqlite3.OperationalError:
    # Column already exists
    pass

conn.commit()




# --- Helper: Send mod log as embed ---
async def send_modlog(guild: discord.Guild, title: str, description: str, color=discord.Color.red()):
    c.execute("SELECT modlog_channel_id FROM settings WHERE guild_id = ?", (guild.id,))
    row = c.fetchone()
    if row and row[0]:
        channel = guild.get_channel(row[0])
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.timestamp = discord.utils.utcnow()
            await channel.send(embed=embed)


# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"⚠️ {e}")


# --- Set mod log channel ---
@bot.tree.command(name="setmodlog", description="Set the channel where moderation logs are sent")
@app_commands.checks.has_permissions(administrator=True)
async def setmodlog(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, modlog_channel_id) VALUES (?, ?)",
              (interaction.guild.id, channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Mod log channel set to {channel.mention}", ephemeral=True)


# --- Warn system ---
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    c.execute("INSERT INTO punishments (user_id, action, reason, moderator_id) VALUES (?, ?, ?, ?)",
              (member.id, "warn", reason, interaction.user.id))
    conn.commit()
    await interaction.response.send_message(f"⚠️ {member} has been warned. Reason: {reason}")
    await send_modlog(interaction.guild, "⚠️ User Warned",
                      f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}",
                      discord.Color.yellow())


@bot.tree.command(name="warnings", description="Show all warnings of a user")
@app_commands.checks.has_permissions(kick_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT reason, timestamp, moderator_id FROM punishments WHERE user_id = ? AND action = 'warn'",
              (member.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"✅ {member} has no warnings.")
        return

    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=discord.Color.yellow())
    for i, (reason, timestamp, mod_id) in enumerate(rows, start=1):
        moderator = interaction.guild.get_member(mod_id)
        mod_name = moderator.mention if moderator else f"ID {mod_id}"
        embed.add_field(name=f"#{i}", value=f"**Reason:** {reason}\n**Moderator:** {mod_name}\n**Date:** {timestamp}",
                        inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clearwarnings", description="Clear all warnings of a user")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    c.execute("DELETE FROM punishments WHERE user_id = ? AND action = 'warn'", (member.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ All warnings cleared for {member}.")
    await send_modlog(interaction.guild, "🧹 Warnings Cleared",
                      f"**User:** {member.mention}\n**Moderator:** {interaction.user.mention}",
                      discord.Color.green())


# --- Clear messages ---
@bot.tree.command(name="clear", description="Delete a number of messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):
    await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🧹 Deleted {amount} messages.", ephemeral=True)
    await send_modlog(interaction.guild, "🧹 Messages Cleared",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {interaction.channel.mention}\n**Amount:** {amount}",
                      discord.Color.green())


# --- Slowmode ---
@bot.tree.command(name="slowmode", description="Set slowmode for the channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"🐌 Slowmode set to {seconds} seconds.")
    await send_modlog(interaction.guild, "🐌 Slowmode Changed",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {interaction.channel.mention}\n**Delay:** {seconds} sec",
                      discord.Color.orange())


# --- Lock channel ---
@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔒 {channel.mention} has been locked.")
    await send_modlog(interaction.guild, "🔒 Channel Locked",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}",
                      discord.Color.red())


# --- Unlock channel ---
@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔓 {channel.mention} has been unlocked.")
    await send_modlog(interaction.guild, "🔓 Channel Unlocked",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}",
                      discord.Color.green())


# --- Add role ---
@bot.tree.command(name="addrole", description="Add a role to a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ Added {role.mention} to {member}.")
    await send_modlog(interaction.guild, "➕ Role Added",
                      f"**User:** {member.mention}\n**Role:** {role.mention}\n**Moderator:** {interaction.user.mention}",
                      discord.Color.blue())


# --- Remove role ---
@bot.tree.command(name="removerole", description="Remove a role from a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ Removed {role.mention} from {member}.")
    await send_modlog(interaction.guild, "➖ Role Removed",
                      f"**User:** {member.mention}\n**Role:** {role.mention}\n**Moderator:** {interaction.user.mention}",
                      discord.Color.blue())


# --- Announce ---
@bot.tree.command(name="announce", description="Make an announcement in a channel")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, title: str, message: str):
    embed = discord.Embed(title=title, description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Announcement by {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await channel.send(embed=embed)
    await interaction.response.send_message(f"📢 Announcement sent to {channel.mention}", ephemeral=True)
    await send_modlog(interaction.guild, "📢 Announcement Made",
                      f"**Moderator:** {interaction.user.mention}\n**Channel:** {channel.mention}\n**Title:** {title}",
                      discord.Color.gold())


# --- Error handling ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)



# --- Purge by user ---
@bot.tree.command(name="purgeuser", description="Delete messages from a specific user")
@app_commands.checks.has_permissions(manage_messages=True)
async def purgeuser(interaction: discord.Interaction, member: discord.Member, amount: int):
    def check(msg):
        return msg.author == member

    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.response.send_message(f"🧹 Deleted {len(deleted)} messages from {member}.", ephemeral=True)
    await send_modlog(interaction.guild, "🧹 User Purged",
                      f"**Moderator:** {interaction.user.mention}\n**User:** {member.mention}\n**Amount:** {len(deleted)}",
                      discord.Color.green())

# --- Audit log ---
@bot.tree.command(name="auditlog", description="Show recent moderation actions")
@app_commands.checks.has_permissions(administrator=True)
async def auditlog(interaction: discord.Interaction, limit: int = 10):
    c.execute("SELECT user_id, action, reason, moderator_id, timestamp FROM punishments ORDER BY id DESC LIMIT ?",
              (limit,))
    rows = c.fetchall()
    embed = discord.Embed(title="📜 Audit Log", color=discord.Color.purple())
    if not rows:
        embed.description = "No moderation actions found."
    else:
        for user_id, action, reason, mod_id, timestamp in rows:
            embed.add_field(
                name=f"{action.title()} | <t:{int(discord.utils.parse_time(timestamp).timestamp())}:R>",
                value=f"👤 <@{user_id}> | 🛠️ <@{mod_id}>\nReason: {reason}",
                inline=False
            )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Suggest system ---
@bot.tree.command(name="setsuggestchannel", description="Set the suggestions channel")
@app_commands.checks.has_permissions(administrator=True)
async def setsuggestchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    c.execute("INSERT OR REPLACE INTO settings (guild_id, suggest_channel_id) VALUES (?, ?)",
              (interaction.guild.id, channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Suggestions will be sent to {channel.mention}", ephemeral=True)

@bot.tree.command(name="suggest", description="Submit a suggestion")
async def suggest(interaction: discord.Interaction, suggestion: str):
    c.execute("SELECT suggest_channel_id FROM settings WHERE guild_id = ?", (interaction.guild.id,))
    row = c.fetchone()
    if not row or not row[0]:
        await interaction.response.send_message("⚠️ Suggestion channel not set.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(row[0])
    if not channel:
        await interaction.response.send_message("⚠️ Suggestion channel not found.", ephemeral=True)
        return

    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=discord.Color.blue())
    embed.set_footer(text=f"Suggested by {interaction.user}", icon_url=interaction.user.display_avatar.url)
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    await interaction.response.send_message("✅ Your suggestion was submitted!", ephemeral=True)

# --- Ticket system ---
class TicketView(discord.ui.View):
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
        channel = await guild.create_text_channel(name=f"ticket-{interaction.user.name}", overwrites=overwrites)
        await channel.send(f"{interaction.user.mention}, support will be with you shortly.")

        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

        close_button = TicketCloseView(channel)
        await channel.send("Click the button below to close this ticket:", view=close_button)

class TicketCloseView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.channel = channel

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.channel.delete()
        await interaction.response.send_message("✅ Ticket closed.", ephemeral=True)

@bot.tree.command(name="ticketpanel", description="Send the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticketpanel(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Support Tickets",
                          description="Click the button below to open a support ticket.",
                          color=discord.Color.blue())
    view = TicketView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Ticket panel created.", ephemeral=True)

# --- Quote system ---
@bot.tree.command(name="quote", description="Save a quote")
async def quote(interaction: discord.Interaction, member: discord.Member, *, message: str):
    c.execute("INSERT INTO quotes (guild_id, message, author_id, added_by) VALUES (?, ?, ?, ?)",
              (interaction.guild.id, message, member.id, interaction.user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Quote saved for {member}.", ephemeral=True)

@bot.tree.command(name="quotes", description="Show quotes of a user")
async def quotes(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT message, added_by, timestamp FROM quotes WHERE guild_id = ? AND author_id = ?",
              (interaction.guild.id, member.id))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message(f"❌ No quotes found for {member}.", ephemeral=True)
        return
    embed = discord.Embed(title=f"💬 Quotes for {member}", color=discord.Color.teal())
    for i, (msg, added_by, timestamp) in enumerate(rows, start=1):
        embed.add_field(name=f"#{i}", value=f"“{msg}”\n— added by <@{added_by}>", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Giveaway system ---
class GiveawayView(discord.ui.View):
    def __init__(self, prize: str, entries: list, duration: int, message: discord.Message):
        super().__init__(timeout=duration)
        self.prize = prize
        self.entries = entries
        self.message = message

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.entries:
            self.entries.append(interaction.user.id)
            await interaction.response.send_message("✅ You joined the giveaway!", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ You already joined!", ephemeral=True)

    async def on_timeout(self):
        if self.entries:
            winner_id = random.choice(self.entries)
            winner = self.message.guild.get_member(winner_id)
            await self.message.channel.send(f"🎉 Congratulations {winner.mention}! You won **{self.prize}**!")
        else:
            await self.message.channel.send("❌ No one joined the giveaway.")

@bot.tree.command(name="giveaway", description="Start a giveaway")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction, prize: str, duration: int):
    embed = discord.Embed(title="🎉 Giveaway!", description=f"Prize: **{prize}**\nDuration: {duration}s",
                          color=discord.Color.gold())
    msg = await interaction.channel.send(embed=embed)
    view = GiveawayView(prize, [], duration, msg)
    await msg.edit(view=view)
    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)

# --- Unquote command ---
@bot.tree.command(name="unquote", description="Remove a quote by its index")
@app_commands.checks.has_permissions(manage_messages=True)
async def unquote(interaction: discord.Interaction, member: discord.Member, index: int):
    # Fetch quotes for the user
    c.execute("SELECT id, message, added_by, timestamp FROM quotes WHERE guild_id = ? AND author_id = ? ORDER BY id",
              (interaction.guild.id, member.id))
    rows = c.fetchall()

    if not rows:
        await interaction.response.send_message(f"❌ No quotes found for {member}.", ephemeral=True)
        return

    if index < 1 or index > len(rows):
        await interaction.response.send_message(f"❌ Invalid index. Choose a number between 1 and {len(rows)}.", ephemeral=True)
        return

    # Get the quote ID to delete
    quote_id = rows[index - 1][0]
    c.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    conn.commit()

    await interaction.response.send_message(f"✅ Quote #{index} for {member} has been removed.", ephemeral=True)

    # Optional: send a modlog
    await send_modlog(
        interaction.guild,
        "🗑️ Quote Removed",
        f"**Moderator:** {interaction.user.mention}\n**User:** {member.mention}\n**Index:** {index}\n**Quote:** {rows[index-1][1]}",
        discord.Color.orange()
    )


@bot.tree.command(name="roleinfo", description="Get information about a role")
@app_commands.describe(role="The role you want information about")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(
        title=f"Role Info: {role.name}",
        color=role.color
    )
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Members", value=len(role.members), inline=True)
    embed.add_field(name="Mentionable", value=role.mentionable, inline=True)
    embed.add_field(name="Hoisted", value=role.hoist, inline=True)
    embed.add_field(name="Created At", value=role.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    perms = ", ".join([perm[0] for perm in role.permissions if perm[1]]) or "None"
    embed.add_field(name="Permissions", value=perms, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="serverstats", description="Get statistics about this server")
async def serverstats(interaction: discord.Interaction):
    guild = interaction.guild
    total_members = guild.member_count
    humans = len([m for m in guild.members if not m.bot])
    bots = len([m for m in guild.members if m.bot])
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    roles = len(guild.roles)
    boosts = guild.premium_subscription_count

    embed = discord.Embed(
        title=f"Server Stats: {guild.name}",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Total Members", value=total_members)
    embed.add_field(name="Humans", value=humans)
    embed.add_field(name="Bots", value=bots)
    embed.add_field(name="Text Channels", value=text_channels)
    embed.add_field(name="Voice Channels", value=voice_channels)
    embed.add_field(name="Roles", value=roles)
    embed.add_field(name="Boosts", value=boosts)
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="poll", description="Create a poll (mods only)")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(question="The poll question", options="Options separated by |")
async def poll(interaction: Interaction, question: str, options: str):
    options_list = [opt.strip() for opt in options.split("|")]
    if len(options_list) < 2:
        await interaction.response.send_message("❌ You need at least 2 options.", ephemeral=True)
        return

    class PollView(View):
        def __init__(self):
            super().__init__(timeout=None)
            self.votes = {opt: set() for opt in options_list}

            # Create vote buttons
            for opt in options_list:
                button = Button(label=opt, style=ButtonStyle.primary)
                button.callback = self.make_callback(opt)
                self.add_item(button)

            # End poll button for mods
            end_button = Button(label="End Poll", style=ButtonStyle.danger)
            end_button.callback = self.end_poll
            self.add_item(end_button)

        # Vote button callback
        def make_callback(self, option):
            async def callback(interaction: Interaction):
                # Toggle vote
                user_id = interaction.user.id
                if user_id in self.votes[option]:
                    self.votes[option].remove(user_id)
                else:
                    self.votes[option].add(user_id)
                await self.update_message(interaction)
                await interaction.response.defer()
            return callback

        # Update embed with votes
        async def update_message(self, interaction: Interaction):
            description = ""
            for opt, voters in self.votes.items():
                description += f"**{opt}** - {len(voters)} votes\n"
            embed = Embed(title=f"📊 {question}", description=description, color=0x00FF00)
            await interaction.message.edit(embed=embed, view=self)

        # End poll callback
        async def end_poll(self, interaction: Interaction):
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("❌ Only mods can end the poll.", ephemeral=True)
                return

            # Disable all buttons
            for child in self.children:
                child.disabled = True

            # Update embed with final results
            description = ""
            for opt, voters in self.votes.items():
                description += f"**{opt}** - {len(voters)} votes\n"
            embed = Embed(title=f"📊 {question} (Final Results)", description=description, color=0xFF0000)
            
            # This counts as the **interaction response**
            await interaction.response.edit_message(embed=embed, view=self)



    # Send initial poll message
    view = PollView()
    description = "\n".join([f"**{opt}** - 0 votes" for opt in options_list])
    embed = Embed(title=f"📊 {question}", description=description, color=0x00FF00)
    await interaction.response.send_message(embed=embed, view=view)



# --- Kick Command ---
@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    c.execute("INSERT INTO punishments (user_id, action, reason, moderator_id) VALUES (?, ?, ?, ?)",
              (member.id, "kick", reason, interaction.user.id))
    conn.commit()
    await interaction.response.send_message(f"🔨 {member} has been kicked. Reason: {reason}")


# --- Ban Command ---
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    c.execute("INSERT INTO punishments (user_id, action, reason, moderator_id) VALUES (?, ?, ?, ?)",
              (member.id, "ban", reason, interaction.user.id))
    conn.commit()
    await interaction.response.send_message(f"⛔ {member} has been banned. Reason: {reason}")


# --- Mute Command ---
@bot.tree.command(name="mute", description="Mute a user (optionally for X minutes)")
@app_commands.checks.has_permissions(manage_roles=True)
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int = 0, reason: str = "No reason provided"):
    """Mute a user (duration in minutes, 0 = indefinite)"""
    # Get or create muted role
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await interaction.guild.create_role(name="Muted")
        for channel in interaction.guild.channels:
            await channel.set_permissions(muted_role, speak=False, send_messages=False)

    await member.add_roles(muted_role, reason=reason)
    c.execute("INSERT INTO punishments (user_id, action, reason, moderator_id) VALUES (?, ?, ?, ?)",
              (member.id, f"mute ({duration} min)" if duration > 0 else "mute (indefinite)", reason, interaction.user.id))
    conn.commit()

    await interaction.response.send_message(f"🤐 {member} has been muted. Reason: {reason}")

    if duration > 0:
        await asyncio.sleep(duration * 60)
        await member.remove_roles(muted_role)
        await interaction.followup.send(f"🔊 {member} has been unmuted (time expired).")


# --- Unmute Command ---
@bot.tree.command(name="unmute", description="Unmute a user")
@app_commands.checks.has_permissions(manage_roles=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        await member.remove_roles(muted_role)
        await interaction.response.send_message(f"🔊 {member} has been unmuted.")
    else:
        await interaction.response.send_message(f"{member} is not muted.")


# --- Error Handling ---
@kick.error
@ban.error
@mute.error
@unmute.error
async def slash_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)

# --- Error handling ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Error: {error}", ephemeral=True)



bot.run("MTQyMjY2OTcwOTE3Nzc4NjUzOA.GqstQ3.LIWUT46G3_XFzyfpY_QX2Mf4XXsmAga7ZTswPY")
