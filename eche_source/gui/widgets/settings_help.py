# gui/widgets/settings_help.py
# Educational copy for Settings ℹ buttons.

from __future__ import annotations

FIELD_HELP: dict[str, tuple[str, str]] = {
    "discord_token": (
        "Discord Bot Token (the bot’s password)",
        "### What is this?\n"
        "Discord does not let random programs join as a bot. You create a **bot account** "
        "on Discord’s website, and Discord gives you a long secret string called a **token**. "
        "That token is the bot’s password.\n\n"
        "### How to get one (step by step)\n"
        "1. Open the [Discord Developer Portal](https://discord.com/developers/applications)\n"
        "2. Click **New Application**, give it a name\n"
        "3. Open **Bot** → **Reset Token** / **Copy**\n"
        "4. Paste it here and click **Save Settings**\n"
        "5. Under **OAuth2 → URL Generator**, pick `bot` + the permissions you need, "
        "open the invite link, add the bot to your server\n\n"
        "### Safety\n"
        "Anyone with this token controls your bot. Eche stores it encrypted for **your "
        "Windows user only**. Never post it in chat or commit it to GitHub.\n\n"
        "### How it fits the bigger picture\n"
        "Discord is just the **chat room**. The AI “brain” is separate (see Provider API Key).",
    ),
    "inf_api_key": (
        "Provider API Key (Cloud only)",
        "### What is this?\n"
        "A **provider** runs large AI models on their computers. For **Cloud (Groq)** you "
        "need an API key from [console.groq.com](https://console.groq.com/).\n\n"
        "### Local Ollama\n"
        "When **Local — Ollama** is selected, this field is **hidden**. Ollama does not "
        "need a paid key; the bot uses a placeholder automatically.\n\n"
        "### How to get a Groq key\n"
        "1. Go to [console.groq.com](https://console.groq.com/)\n"
        "2. Account → **API Keys** → create a key\n"
        "3. Paste here → **Save Settings**\n\n"
        "### Safety\n"
        "Stored encrypted (DPAPI) for your Windows user only.",
    ),
    "us_access_token": (
        "Unsplash Access Token (optional photo search)",
        "### What is this?\n"
        "Some bot commands search the web for photos (Unsplash). Unsplash gives you a free "
        "app key so they know who is searching.\n\n"
        "### Do I need it?\n"
        "Only if you use image-search features. Chat, bank, and games work without it.\n\n"
        "### How to get one\n"
        "Create a free developer account at Unsplash, register an application, copy the "
        "**Access Key**, paste it here, Save.",
    ),
    "us_secret_token": (
        "Unsplash Secret Token (optional)",
        "A second secret Unsplash sometimes uses. Treat it like a password. "
        "Most simple searches only need the Access Token.",
    ),
    "home_server_id": (
        "Home Server ID (which Discord server is “home”)",
        "### What is this?\n"
        "Your bot can join many Discord servers. **Home Server** is the one place Eche "
        "stores memory and bank data (folders of channels named `memory-…`).\n\n"
        "### How to copy the ID (no typing long numbers by hand)\n"
        "1. Discord → **User Settings → Advanced → Developer Mode = ON**\n"
        "2. Right-click your server icon → **Copy Server ID**\n"
        "3. Paste here → Save\n\n"
        "### Why it matters\n"
        "Without a home server, the bot cannot create user memory or economy channels. "
        "This is required even if you skip the AI provider key.",
    ),
    "thoughts_thread_id": (
        "Thoughts Thread ID (optional debug notepad)",
        "### What is this?\n"
        "Optional. You can make a private thread where the bot posts “thinking” notes "
        "while you learn how it works.\n\n"
        "### How to get a thread ID\n"
        "Enable Developer Mode, right-click the thread → **Copy Channel ID**.\n\n"
        "### Can I leave it blank?\n"
        "Yes. Completely optional for normal use.",
    ),
    "groq_model": (
        "Model ID (Cloud)",
        "### Cloud (Groq)\n"
        "The model name sent to Groq, e.g. `llama-3.3-70b-versatile`.\n\n"
        "### Local Ollama\n"
        "When Ollama is selected, this text field is **hidden**. Choose a model from the "
        "**Local Ollama Model** dropdown instead. Each backend keeps its own saved model.\n\n"
        "### If chat breaks with “model not found”\n"
        "The provider renamed or removed that model. Copy a current name from their console, "
        "paste it here (Cloud), Save, restart the bot.",
    ),
    "provider_backend": (
        "Provider backend (Cloud vs Ollama)",
        "### Cloud (default — Groq)\n"
        "Shows **API key** + **Model ID**. Sends chat to Groq’s free-tier API.\n\n"
        "### Local Ollama\n"
        "Hides the API key and Cloud model field. Shows the **local model list** + Refresh. "
        "No paid key required. Install [Ollama](https://ollama.com/), pull a model, pick it "
        "from the list.\n\n"
        "### Edit client.py\n"
        "Advanced users can still open the provider file to change URLs by hand. "
        "The dropdown sets `ECHE_PROVIDER` so normal switches don’t require code edits.",
    ),
    "summarizer_model": (
        "Summarizer model",
        "### What is this?\n"
        "When memory fills up, Eche asks an AI to **compress** old messages into a "
        "short Summary. That call can use a **different** model than chat.\n\n"
        "### Leave blank?\n"
        "Uses the same Model ID as chat.\n\n"
        "### Why separate?\n"
        "You might want a cheap/fast model for summaries and a smarter one for replies.",
    ),
    "summarizer_prompt": (
        "Summarizer prompt file",
        "### What is this?\n"
        "The instruction sheet the bot gives the AI when compressing memory — "
        "same idea as Personality, but for summaries only.\n\n"
        "### Default path\n"
        "`config/summarizer_prompt.txt` in this package.\n\n"
        "### Custom path\n"
        "Optional: point at another `.txt` file. Relative paths are from the package root.\n\n"
        "### Placeholder\n"
        "Keep `{combined_for_summary}` in the file so conversation text is inserted.",
    ),
    "personality": (
        "Personality (the bot’s character sheet)",
        "### What is this?\n"
        "A plain-English instruction sheet that is added to **every** AI chat call. "
        "It sets tone, humor, boundaries, and identity — without training a new model.\n\n"
        "### Examples of what you might write\n"
        "- “You are a chill server butler who keeps answers short.”\n"
        "- “Never reveal API keys. Stay in character.”\n\n"
        "### Where it saves\n"
        "`config/personality.txt` in this package (not encrypted).\n\n"
        "### Tip\n"
        "Change one sentence at a time and test. Small wording changes can matter a lot.",
    ),
    "provider": (
        "Provider code (client.py) — the phone line to the AI",
        "### What is this editor?\n"
        "It opens `core/client.py`, the program code that **calls the AI provider**.\n\n"
        "Near the **top of the file** you will see URLs for Groq and Ollama, key/model "
        "helpers, and `call_groq(...)`.\n\n"
        "### Do I need to edit code on day one?\n"
        "No. Use the Settings backend dropdown + key/model fields. Edit this file only "
        "when you want a custom URL or advanced changes.\n\n"
        "### After Save\n"
        "Kill Bot → Run Bot so the new code loads.",
    ),
    "unifier": (
        "Unifier (how the prompt is assembled)",
        "### What is this?\n"
        "Before the AI answers, Eche builds one big instruction package: personality, "
        "memory snippets, user message, rules. The **unifier / builder** file is where that "
        "assembly is defined.\n\n"
        "### Where it saves\n"
        "Package file `core/builder.py` (plain text). Restart the bot after saving.",
    ),
    "bot_memory": (
        "Self Memory (what the bot remembers about itself)",
        "### What is this?\n"
        "On the home Discord server, under a category like `bot-memory`, there is a "
        "pinned note the bot uses as its own diary (summary + recent lines).\n\n"
        "### How it differs from user context\n"
        "- **Self memory** = about the bot\n"
        "- **User context** = about each human (per user folder)\n\n"
        "### Editing\n"
        "You can view and edit that pin from this button. Be careful — it is live on Discord.",
    ),
    "user_context": (
        "User Context (memory of each person)",
        "### What is this?\n"
        "For each opted-in user, Discord has a category `memory-{user id}` with a "
        "`#context` channel and a pinned note (Summary + New lines).\n\n"
        "### What you can do here\n"
        "Browse by server, open a user’s pin, edit it, save back to Discord.",
    ),
    "project_path": (
        "eche_source folder (dev tree)",
        "### What is this?\n"
        "The **source** package: `eche_source/` with `BUILD.bat`, `core/`, `gui/`, `.venv`.\n\n"
        "### Not the portable app\n"
        "The flash-drive folder `eche/` only has `Eche.exe` — rebuilds always run from **source**.\n\n"
        "### Rebuild\n"
        "One-tap update runs `BUILD.bat` there and publishes a fresh portable app into sibling `eche/`.",
    ),
    "updates": (
        "One-tap rebuild from eche_source",
        "### What happens\n"
        "1. Finds `eche_source` (or the path you set)\n"
        "2. Runs **`BUILD.bat`** with the source `.venv`\n"
        "3. Publishes `Eche.exe` + `_internal` + **icons** into `../eche/`\n"
        "4. Keeps your `config/` secrets on the portable side\n\n"
        "### First time\n"
        "In `eche_source`: `python -m venv .venv` then "
        "`.venv\\Scripts\\pip install -r requirements.txt` once.",
    ),
    "economy": (
        "Economy / bank balances",
        "### Where money lives\n"
        "On the **Home Server**, each user can have a category `memory-{their id}` "
        "with a channel named `#economy`. Inside is a **pinned message** that starts with "
        "BANK DATA.\n\n"
        "### What the bank browser does\n"
        "Lists users, shows the pin, lets you change the balance, saves back to Discord. "
        "You need the bot token and Home Server ID set first.",
    ),
    "security": (
        "Security folders",
        "### Cookies\n"
        "Some features (e.g. music / YouTube) may need a cookies file. "
        "Open the cookies folder, add your file, then **restart the bot**.\n\n"
        "### Secrets (DPAPI)\n"
        "API tokens and the Discord bot token are stored encrypted for your Windows "
        "user in `config/secrets.dpapi.json`. Do not share that file.",
    ),
}