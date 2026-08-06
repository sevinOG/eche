import sys
import os
import multiprocessing
import inspect

print(">>> BOT.PY IS RUNNING — TOP OF FILE")
print(">>> PYTHON EXECUTABLE:", sys.executable)

import shutil
print("FFMPEG PATH:", shutil.which("ffmpeg"))
print(os.path.exists("cookies/ytcookies.txt"))
print(os.path.abspath("cookies/ytcookies.txt"))


# ---------------------------------------------------------
# OPUS LOAD (works in source + frozen exe)
# ---------------------------------------------------------
import discord

try:
    from core.paths import find_opus_dll
except Exception:
    find_opus_dll = None  # type: ignore

OPUS_PATH = find_opus_dll() if find_opus_dll else None
if OPUS_PATH:
    print(">>> LOADING OPUS FROM:", OPUS_PATH)
    try:
        discord.opus.load_opus(OPUS_PATH)
    except Exception as e:
        print(">>> OPUS LOAD FAILED:", e)
else:
    print(">>> OPUS DLL NOT FOUND (voice may be unavailable)")
print(">>> OPUS LOADED:", discord.opus.is_loaded())

# ---------------------------------------------------------
# NORMAL IMPORTS
# ---------------------------------------------------------
from discord.ext import commands
from dotenv import load_dotenv

# ⭐ NEW IMPORT — entertainer background loop
from cogs.hire.entertainers.entertainer_manager import entertainer_background_loop

# ⭐ NEW IMPORT — bot whitelist
from core.bot_whitelist import is_allowed_bot

# ⭐ NEW IMPORT — LAW MANAGER
from cogs.hire.lawyer.law_manager import LawManager

# ⭐ NEW IMPORT — RAW GROQ CALL
from core.client import call_groq_raw


# Prevent bot from loading inside GUI process
if multiprocessing.current_process().name != "MainProcess":
    raise RuntimeError("Bot attempted to load inside a child process (GUI). Aborting.")

load_dotenv()


def _resolve_home_server_id() -> int:
    """
    Read HOME_SERVER_ID after env/settings are applied.
    Prefer env (GUI injects from settings); fall back to settings.json.
    """
    raw = (os.getenv("HOME_SERVER_ID") or "").strip()
    if not raw:
        try:
            from core.secrets import load_all
            from core.paths import user_dir
            cfg = load_all(user_dir())
            raw = (cfg.get("home_server_id") or "").strip()
            if raw:
                os.environ["HOME_SERVER_ID"] = raw
        except Exception:
            pass
    if not raw:
        # Keep import soft — echelon.main validates and reports a friendly error
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


HOME_SERVER_ID = _resolve_home_server_id()

print(">>> USING BOT FILE:", inspect.getfile(inspect.currentframe()))
print(">>> bot.py imported")
print(">>> HOME_SERVER_ID:", HOME_SERVER_ID or "(not set yet)")


# ---------------------------------------------------------
# ⭐ BOT BLACKLIST — prevents bot-to-bot loops
# ---------------------------------------------------------
BOT_BLACKLIST = {
    1486240665951142079  # your bot's ID
}


# ---------------------------------------------------------
# ⭐ CUSTOM PREFIX — ONLY RESPOND TO MESSAGES STARTING WITH '?'
# ---------------------------------------------------------
def strict_prefix(bot, message):
    content = message.content
    if content.startswith("?"):
        return ["?"]   # must be iterable
    return []          # safe: “no prefixes”


class Echelon(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        print(">>> Echelon instance created")

        super().__init__(
            command_prefix=strict_prefix,
            case_insensitive=True,
            intents=intents,
            help_command=None
        )

        # GLOBAL EVENT DEDUPLICATION GUARD
        self._event_registry = set()

        def safe_add_listener(event_name, callback):
            key = (event_name, callback.__module__, callback.__name__)
            if key in self._event_registry:
                print(f"[GLOBAL GUARD] Prevented duplicate listener: {key}")
                return
            self._event_registry.add(key)
            super(Echelon, self).add_listener(callback, event_name)

        self.safe_add_listener = safe_add_listener

        # LOAD OPT-IN STATE
        from core.opt_in_manager import load_opted_in
        self.context_opted_in = load_opted_in()

        # TOKEN TRACKING
        self.token_usage = {
            "today_used": 0,
            "daily_limit": 200000,
            "reset_hour": 0
        }

    # ---------------------------------------------------------
    # RAW GATEWAY EVENT DIAGNOSTICS
    # ---------------------------------------------------------
    async def on_ready(self):
        print(f">>> BOT READY AS {self.user}")

        # ⭐ Auto-add itself to blacklist
        BOT_BLACKLIST.add(self.user.id)
        print(f">>> Added bot to blacklist: {self.user.id}")

        # Notify GUI (if running under the bridge)
        try:
            from core.gui_bridge import ready
            ready(str(self.user) if self.user else "")
        except Exception:
            pass

    # ---------------------------------------------------------
    # ⭐ GLOBAL BOT/BOT INTERACTION CHECK
    # ---------------------------------------------------------
    @commands.check
    async def global_bot_check(self, ctx):
        author = ctx.author

        # Block blacklisted bots (including itself)
        if author.id in BOT_BLACKLIST:
            return False

        # Allow humans always
        if not author.bot:
            return True

        # Allow only whitelisted bots
        return is_allowed_bot(author.id)

    # ---------------------------------------------------------
    # ⭐ SAFE on_message — allows bot messages, prevents loops
    # ---------------------------------------------------------
    async def on_message(self, message):
        # Block blacklisted bots (including itself)
        if message.author.id in BOT_BLACKLIST:
            return

        # Process commands normally
        await self.process_commands(message)

    # ---------------------------------------------------------
    # ⭐ UPDATED ERROR HANDLER — IGNORE UNKNOWN COMMANDS
    # ---------------------------------------------------------
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return

        print("COMMAND ERROR:", repr(error))
        print("ARGS:", error.args)
        try:
            await ctx.send(f"Error: {error}")
        except:
            pass

    # ---------------------------------------------------------
    # EXTENSION LOADING
    # ---------------------------------------------------------
    async def setup_hook(self):
        print(">>> setup_hook: loading extensions")

        # Attach GUI bridge once the asyncio loop is running
        try:
            from core.gui_bridge import attach, is_enabled
            if is_enabled():
                attach(self)
        except Exception as e:
            print(f">>> gui_bridge attach skipped: {e}")

        # Load each extension independently so one missing optional dep
        # (yt_dlp, dateparser, …) cannot abort the whole bot on a flash-drive copy.
        extensions = [
            "cogs.help.help",
            "cogs.debug_commands",
            "cogs.context_debug",
            "cogs.events.on_message",
            "cogs.remind.remind",
            "cogs.music.music",
            "cogs.charoverride",
            "cogs.image_search",
            "cogs.convert",
            "cogs.flash",
            "cogs.hire.lawyer.lawyer",
            "cogs.economy.bank",
            "cogs.economy.bet",
            "cogs.forceoptin",
            "cogs.economy.shops",
            "cogs.hire.hire",
            "cogs.hire.rob",
            "cogs.hire.heckle.heckle",
            "cogs.hire.entertainers.entertainer",
        ]

        loaded = 0
        failed: list[str] = []
        for ext in extensions:
            try:
                await self.load_extension(ext)
                loaded += 1
                print(f">>> loaded: {ext}")
            except Exception as e:
                failed.append(f"{ext}: {e}")
                print(f">>> FAILED to load {ext}: {e}")
                try:
                    from core.gui_bridge import log
                    log(f"Cog load failed (bot continues): {ext} — {e}", channel="bot")
                except Exception:
                    pass

        # Poker registers games via side-effect import (not a Cog extension)
        try:
            import cogs.poker.poker  # noqa: F401
            print(">>> imported: cogs.poker.poker")
        except Exception as e:
            failed.append(f"cogs.poker.poker: {e}")
            print(f">>> FAILED poker import: {e}")

        print(f">>> setup_hook: {loaded}/{len(extensions)} extensions loaded")
        if failed:
            print(f">>> setup_hook: {len(failed)} failed (non-fatal):")
            for line in failed:
                print(f"    - {line}")

        # ⭐ CREATE LAW MANAGER INSTANCE
        self.law_manager = LawManager(self, call_groq_raw)

        # ⭐ START ENTERTAINER BACKGROUND LOOP
        self.loop.create_task(entertainer_background_loop(self))


# ---------------------------------------------------------
# BOT ENTRY POINT
# ---------------------------------------------------------
def run_bot():
    bot = Echelon()
    token = os.getenv("DISCORD_TOKEN")
    bot.run(token)


if __name__ == "__main__":
    run_bot()
