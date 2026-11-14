
import random

TARGET_USER_ID = 702517391560540240

BOO_CHANCE = 0

BOO_LINES = [
    "boo.",
    "booooo.",
    "get bood.",
    "unfortunate. boo.",
    "lie detected.",
    "boo. skill issue.",
    "fuck you",
    "what the fuck",
    "stfu",
    "boo. tragic.",
]

async def maybe_boo(message):

    # REMOVED BOT BLOCK False

    if message.author.id != TARGET_USER_ID:
        return False

    if random.random() < BOO_CHANCE:
        reply = random.choice(BOO_LINES)
        await message.channel.send(reply)
        try:
            from core.audit import safe_activity
            guild = getattr(message, "guild", None)
            channel = getattr(message, "channel", None)
            author = getattr(message, "author", None)
            safe_activity(
                "maybe-boo",
                outcome="success",
                user_id=getattr(author, "id", None),
                guild_id=getattr(guild, "id", None),
                channel_id=getattr(channel, "id", None),
            )
        except Exception:
            pass
        return True

    return False
