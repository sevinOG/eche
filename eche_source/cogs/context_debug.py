# context_debug.py — User Context Debug + Editing Commands (Owner Only)

import discord
from discord.ext import commands

from core.permissions import owner_only

from core.context_manager import ensure_context_channel, get_home_guild, read_raw_context
from core.context_summarizer import fold_person, replace_memory


async def _send_fenced(ctx, heading: str, body: str) -> None:
    """Post a titled code block. A long body continues as later messages."""
    from core.client import REPLY_MAX_CHARS, discord_chunks

    head = f"{heading}\n"
    room = max(16, REPLY_MAX_CHARS - len(head) - 8)
    pieces = discord_chunks((body or "").strip() or "(empty)", room)
    await ctx.send(f"{head}```\n{pieces[0]}\n```")
    for extra in pieces[1:]:
        await ctx.send(f"```\n{extra}\n```")


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
        from core.context_manager import parse_pin_sections

        if "Summary:" not in content:
            summary_body = "(malformed context)"
        else:
            _label, summary_body, _recent = parse_pin_sections(content, "")

        await _send_fenced(ctx, f"**Summary for {member.name}:**", summary_body)

    # ---------------------------------------------------------
    # OVERWRITE SUMMARY (PATCHED + SAFE)
    # ---------------------------------------------------------
    @commands.command(name="context_overwrite")
    @owner_only()
    async def context_overwrite(self, ctx, member: discord.Member, *, new_summary: str):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )
        if not channel or not pinned:
            return await ctx.send("Could not reach that user's context pin.")

        # 1. Extract clean summary text
        cleaned = new_summary
        for header in ["Context for", "Summary:", "New:", "USER:"]:
            cleaned = cleaned.replace(header, "")
        cleaned = cleaned.strip()

        # 2. Old recent lines on the pin wait in the local buffer.
        from core.context_manager import parse_pin_sections

        content = pinned.content or ""
        _label, _summary, recent = parse_pin_sections(content, f"Context for {member.name}:\n")
        if recent:
            from core.personal_recent import add_stash

            add_stash(member.id, "user", recent)

        # 3. Start the structured memory over from the given text. It is
        # sorted into sections under the same durability and privacy rules.
        ok = await replace_memory(self.bot, guild, member.id, member.name, cleaned, side="user")
        if not ok:
            return await ctx.send(
                f"Could not update the memory for **{member.name}**. Nothing was changed."
            )
        await ctx.send(f"Updated Summary for **{member.name}**.")

    # ---------------------------------------------------------
    # CLEAR SUMMARY
    # ---------------------------------------------------------
    @commands.command(name="context_clear")
    @owner_only()
    async def context_clear(self, ctx, member: discord.Member):
        guild = get_home_guild(self.bot)
        channel, pinned = await ensure_context_channel(
            self.bot, guild, member.id, member.name
        )
        if not channel or not pinned:
            return await ctx.send("Could not reach that user's context pin.")

        content = pinned.content or ""
        from core.context_manager import parse_pin_sections

        if "Summary:" not in content:
            return await ctx.send("Context format is malformed.")
        _label, _summary, recent = parse_pin_sections(
            content, f"Context for {member.name}:\n"
        )
        if recent:
            from core.personal_recent import add_stash

            add_stash(member.id, "user", recent)
        await replace_memory(self.bot, guild, member.id, member.name, "", side="user")
        await ctx.send(f"Cleared Summary for **{member.name}**.")

    # ---------------------------------------------------------
    # RAW CONTEXT
    # ---------------------------------------------------------
    @commands.command(name="context_raw")
    @commands.is_owner()
    async def context_raw(self, ctx, member: discord.Member):
        content = await read_raw_context(self.bot, member.id, member.name)
        if content is None:
            return await ctx.send("Could not read that context.")

        await _send_fenced(ctx, f"**Raw Context for {member.name}:**", content)

    # ---------------------------------------------------------
    # REPAIR BOT MEMORY (FULL AUTO-REBUILD)
    # ---------------------------------------------------------
    @commands.command(name="bot_repair")
    @owner_only()
    async def repair_bot_memory(self, ctx, member: discord.Member = None):
        from core.bot_memory import ensure_bot_memory_channel
        from core.discord_store import bot_memory_header
        guild = get_home_guild(self.bot)
        member = member or ctx.author
        header = bot_memory_header(member.name)

        # Self context lives in this user's context thread.
        channel, pinned = await ensure_bot_memory_channel(
            self.bot, member.id, member.name
        )
        if not channel or not pinned:
            return await ctx.send(
                "Could not reach bot memory. Check the home server id and that the bot is in that server."
            )
        content = pinned.content or ""

        # Keep any old New: lines in the local buffer. The pin is the long-term block.
        from core.context_manager import parse_pin_sections, summary_only
        from core.personal_recent import add_stash

        _label, summary, bot_lines = parse_pin_sections(content, header)
        if bot_lines:
            add_stash(member.id, "bot", bot_lines)
            kept = summary if summary and summary != "(none yet)" else "(none yet)"
            rebuilt = summary_only(header, kept)
            if rebuilt != content:
                from core.discord_store import edit_record
                await edit_record(pinned, rebuilt)

        folded = await fold_person(self.bot, guild, member.id, member.name, reason="repair", force=True)
        if not folded:
            return await ctx.send(
                f"Could not rebuild bot memory for **{member.name}**. The memory was left in place."
            )
        await ctx.send(f"Bot memory for **{member.name}** has been fully repaired and rebuilt.")

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
        if not channel or not pinned:
            return await ctx.send("Could not reach that user's context pin.")

        from core.context_manager import parse_pin_sections

        _label, summary, _recent = parse_pin_sections(
            pinned.content or "",
            f"Context for {member.name}:\n\n",
        )
        await _send_fenced(
            ctx,
            f"**User Context Debug for {member.name}:**\nChannel: {channel.mention}\nPinned:",
            pinned.content or "",
        )
        await _send_fenced(ctx, "**Summary:**", summary or "")


async def setup(bot):
    await bot.add_cog(ContextDebug(bot))
