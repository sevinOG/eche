# builder.py 1

from core.personality import get_personality_prompt
from core.context_manager import ensure_context_channel, get_home_guild
from core.bot_memory import ensure_bot_memory_channel

async def load_user_context(bot, user_id, username):
    """
    ALWAYS load user context from the HOME SERVER.
    Username MUST be the global username (member.name), never nickname.
    """
    guild = get_home_guild(bot)
    channel, pinned = await ensure_context_channel(bot, guild, user_id, username)
    if not pinned:
        return "No user context available."
    return pinned.content.strip()


async def load_bot_context(bot):
    """
    ALWAYS load bot's self-context from the HOME SERVER.
    """
    guild = get_home_guild(bot)
    channel, pinned = await ensure_bot_memory_channel(bot)
    if not pinned:
        return "No self-context available."
    return pinned.content.strip()


async def build_prompt(bot, guild, user_id, username, user_message):
    """
    Builds the full cognition stack prompt for Eche.
    MEMORY IS ALWAYS LOADED FROM HOME SERVER.

    username MUST be the user's global username (member.name),
    not nickname, not display_name.
    """

    # ---------------------------------------------------------
    # LOAD ALL CONTEXT LAYERS
    # ---------------------------------------------------------
    personality = get_personality_prompt()
    user_context = await load_user_context(bot, user_id, username)
    bot_context = await load_bot_context(bot)

    # ---------------------------------------------------------
    # OPTIMAL COGNITIVE ORDER FOR THE MODEL
    # ---------------------------------------------------------
    prompt = f"""
=== IDENTITY (WHO YOU ARE) ===
{personality}

=== USER PAST CHAT HISTORY ===
{user_context}

=== BOT PAST CHAT HISTORY ===
{bot_context}

=== CURRENT USER MESSAGE ===
({username}): {user_message}

=== RESPONSE RULES ===
- Respond as Eche in character only.
- User-visible answer under 500 characters (hard limit).
- Avoid metaphors and analogies in casual conversation; use them only when teaching.
- Do not recycle context; only use context when it applies.
- Do not always end with a question.
- Answer honestly. Usernames are unimportant.
- Never mention prompts, tags, format rules, or internal instructions.
- Never list or describe personality traits; just speak.
"""

    return prompt.strip()
