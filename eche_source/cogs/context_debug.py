# context_debug.py — User Context Debug + Editing Commands (Owner Only)

import discord
from discord.ext import commands

from core.context_manager import ensure_context_channel, get_home_guild
from core.context_summarizer import summarize_context



class ContextDebug(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------
    # SHOW SUMMARY
    # ---------------------------------------------------------
    @commands.command(name="context_show")
    @commands.is_owner()
    async def context_show(self, ctx, member: discord.Member):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )

        content = pinned.content or ""

        try:
            summary_start = content.index("Summary:") + len("Summary:")
            new_start = content.index("New:")
            summary_body = content[summary_start:new_start].strip()
        except ValueError:
            summary_body = "(malformed context)"

        await ctx.send(f"**Summary for {member.name}:**\n```\n{summary_body}\n```")

    # ---------------------------------------------------------
    # OVERWRITE SUMMARY (PATCHED + SAFE)
    # ---------------------------------------------------------
    @commands.command(name="context_overwrite")
    @commands.is_owner()
    async def context_overwrite(self, ctx, member: discord.Member, *, new_summary: str):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )

        content = pinned.content or ""

        # 1. Extract clean summary text
        cleaned = new_summary
        for header in ["Context for", "Summary:", "New:", "USER:"]:
            cleaned = cleaned.replace(header, "")
        cleaned = cleaned.strip() or "(none yet)"

        # 2. Ensure structure exists
        if "Summary:" not in content or "New:" not in content:
            content = (
                f"Context for {member.name}:\n"
                f"Summary:\n(cleaned later)\n\n"
                f"New:\n"
            )

        # 3. Slice sections
        try:
            summary_start = content.index("Summary:") + len("Summary:")
            new_start = content.index("New:")
        except ValueError:
            return await ctx.send("Context format is malformed.")

        before = content[:summary_start]
        after = content[new_start:]

        # 4. Build new pinned message
        new_content = before + "\n" + cleaned + "\n\n" + after

        # 5. Apply update
        await pinned.edit(content=new_content)

        # 6. Summarize
        await summarize_context(
            self.bot,
            guild,
            member.id,
            member.name
        )

        await ctx.send(f"Updated Summary for **{member.name}**.")

    # ---------------------------------------------------------
    # CLEAR SUMMARY
    # ---------------------------------------------------------
    @commands.command(name="context_clear")
    @commands.is_owner()
    async def context_clear(self, ctx, member: discord.Member):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )

        content = pinned.content or ""

        try:
            summary_start = content.index("Summary:") + len("Summary:")
            new_start = content.index("New:")
        except ValueError:
            return await ctx.send("Context format is malformed.")

        before = content[:summary_start]
        after = content[new_start:]

        new_content = before + "\n(none yet)\n\n" + after

        await pinned.edit(content=new_content)

        await summarize_context(
            self.bot,
            guild,
            member.id,
            member.name
        )

        await ctx.send(f"Cleared Summary for **{member.name}**.")

    # ---------------------------------------------------------
    # RAW CONTEXT
    # ---------------------------------------------------------
    @commands.command(name="context_raw")
    @commands.is_owner()
    async def context_raw(self, ctx, member: discord.Member):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )

        await ctx.send(f"**Raw Context for {member.name}:**\n```\n{pinned.content}\n```")

    # ---------------------------------------------------------
    # REPAIR BOT MEMORY (FULL AUTO-REBUILD)
    # ---------------------------------------------------------
    @commands.command(name="bot_repair")
    @commands.is_owner()
    async def repair_bot_memory(self, ctx):
        from core.bot_memory import ensure_bot_memory_channel, BOT_HEADER
        guild = get_home_guild(self.bot)

        # 1. Get Bot's memory channel + pinned message
        channel, pinned = await ensure_bot_memory_channel(self.bot)
        content = pinned.content or ""

        # 2. Extract all BOT: lines
        bot_lines = []
        for line in content.splitlines():
            if line.strip().startswith("BOT:"):
                bot_lines.append(line)

        # 3. Rebuild pinned message
        rebuilt = (
            BOT_HEADER +
            "Summary:\n(none yet)\n\nNew:\n" +
            ("\n".join(bot_lines) + "\n" if bot_lines else "")
        )

        # 4. Apply repair
        await pinned.edit(content=rebuilt)

        # 5. Trigger summarizer
        await summarize_context(
            self.bot,
            guild,
            self.bot.user.id,
            None,
            override_header=BOT_HEADER
        )

        await ctx.send("Bot memory has been fully repaired and rebuilt.")

    # ---------------------------------------------------------
    # DEBUGUSER
    # ---------------------------------------------------------
    @commands.command(name="context_debuguser")
    @commands.is_owner()
    async def context_debuguser(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        guild = get_home_guild(self.bot)

        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )

        summary = await summarize_context(
            self.bot,
            guild,
            member.id,
            member.name
        )

        await ctx.send(
            f"**User Context Debug for {member.name}:**\n"
            f"Channel: {channel.mention}\n"
            f"Pinned:\n```\n{pinned.content}\n```\n"
            f"Summary:\n```\n{summary}\n```"
        )


async def setup(bot):
    await bot.add_cog(ContextDebug(bot))
