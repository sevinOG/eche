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
        "### OpenRouter\n"
        "OpenRouter has its own key field. This Groq key stays saved when you switch.\n\n"
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
        "stores memory and bank data, in one category named `bot memory`.\n\n"
        "### How to copy the ID (no typing long numbers by hand)\n"
        "1. Discord → **User Settings → Advanced → Developer Mode = ON**\n"
        "2. Right-click the server name → **Copy Server ID**\n"
        "3. Paste here → Save\n\n"
        "Pasting the server icon link also works. Eche reads the ID from that link.\n\n"
        "### Why it matters\n"
        "Without a home server, the bot cannot create the bot memory category. "
        "This is required even if you skip the AI provider key.",
    ),
    "groq_model": (
        "Model ID (Cloud)",
        "### Cloud (Groq)\n"
        "The model name sent to Groq, e.g. `qwen/qwen3.6-27b`.\n\n"
        "### Local Ollama\n"
        "When Ollama is selected, this text field is **hidden**. Choose a model from the "
        "**Local Ollama Model** dropdown instead. Each backend keeps its own saved model.\n\n"
        "### OpenRouter\n"
        "OpenRouter uses the **Model ID (OpenRouter)** field, an `author/slug` id "
        "from openrouter.ai/models.\n\n"
        "### If chat breaks with “model not found”\n"
        "The provider renamed or removed that model. Copy a current name from their console, "
        "paste it here (Cloud), Save, restart the bot.",
    ),
    "openrouter_api_key": (
        "OpenRouter API Key",
        "### What is this?\n"
        "OpenRouter runs many companies’ models through one API. The key comes from "
        "[openrouter.ai/keys](https://openrouter.ai/keys).\n\n"
        "### How to use it\n"
        "1. Choose **OpenRouter** in the provider dropdown\n"
        "2. Create a key and paste it here\n"
        "3. Set a model id, then **Save Settings** and restart the bot\n\n"
        "### Safety\n"
        "Stored encrypted (DPAPI) for your Windows user only. It is kept separate "
        "from the Groq key, so switching providers does not erase either one.",
    ),
    "openrouter_model": (
        "Model ID (OpenRouter)",
        "### What to paste\n"
        "An OpenRouter id in `author/slug` form, copied from "
        "[openrouter.ai/models](https://openrouter.ai/models).\n\n"
        "### Default\n"
        "`openrouter/free` sends the reply through OpenRouter’s free pool. "
        "Replace it when you want a specific model.\n\n"
        "### If chat says the model was not found\n"
        "That id was renamed or removed. Copy a current one, Save, and restart the bot.",
    ),
    "provider_backend": (
        "Provider backend (Groq, OpenRouter, or Ollama)",
        "### Cloud (default — Groq)\n"
        "Shows **API key** + **Model ID**. Sends chat to Groq’s free-tier API.\n\n"
        "### OpenRouter\n"
        "Shows **OpenRouter API Key** + **Model ID**. One key reaches the models listed "
        "at openrouter.ai. The default model is the free router, `openrouter/free`.\n\n"
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
        "Every third turn, Eche asks an AI to fold the recent lines into the "
        "long-term summary. A new detail is added to the subject it already "
        "belongs to. The summary is shortened only when it nears Discord's "
        "pin limit. That call can use a **different** model than chat.\n\n"
        "### Leave blank?\n"
        "Uses the same Model ID as chat.\n\n"
        "### Why separate?\n"
        "You might want a cheap/fast model for summaries and a smarter one for replies.",
    ),
    "summarizer_prompt": (
        "Summarizer prompt file",
        "### What is this?\n"
        "The instruction sheet the bot gives the AI when it folds recent lines "
        "into long-term memory. Same idea as Personality, but for memory only.\n\n"
        "### Default path\n"
        "`config/summarizer_prompt.txt` in this package.\n\n"
        "### Custom path\n"
        "Optional: point at another `.txt` file. Relative paths are from the package root.\n\n"
        "### Placeholders\n"
        "Keep `{existing_summary}` and `{new_lines}` so the old cloud and the "
        "three new lines are inserted separately. An older sheet may still use "
        "`{combined_for_summary}`.",
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
        "Stop → Run so the new code loads.",
    ),
    "unifier": (
        "Unifier (how the prompt is assembled)",
        "### What is this?\n"
        "Before the AI answers, Eche sends one short system message: a few job lines, "
        "the personality as Voice, and a short tool list when tools are offered. "
        "The **unifier / builder** file is the user turn: today, the two memory pins, "
        "and the latest message.\n\n"
        "### Where it saves\n"
        "Package file `core/builder.py` (plain text). Restart the bot after saving.",
    ),
    "bot_memory": (
        "Self Memory (what the bot remembers about itself)",
        "### What is this?\n"
        "On the home Discord server, the category `bot memory` has one channel per user. "
        "That user's `context` thread holds two pinned notes: their context, and the bot's "
        "self-context for them (summary + recent lines).\n\n"
        "### How it differs from user context\n"
        "- **Self memory** = what the bot remembers with that person\n"
        "- **User context** = what it remembers about that person\n\n"
        "### Editing\n"
        "Enter a user ID, then fetch. This does not create a channel. "
        "Be careful — the pin is live on Discord.",
    ),
    "user_context": (
        "User Context (memory of each person)",
        "### What is this?\n"
        "For each opted-in user, Discord has a channel `user-{user id}` in `bot memory`, "
        "with a `context` thread and a pinned note (Summary + New lines).\n\n"
        "### What you can do here\n"
        "Browse by server, open a user’s pin, edit it, save back to Discord.",
    ),
    "project_path": (
        "eche_source folder (dev tree)",
        "### What is this?\n"
        "The **source** package: `eche_source/` with `BUILD.bat`, `core/`, `gui/`, and `.venv`.\n\n"
        "### Not the portable app\n"
        "The folder `eche/` only has `Eche.exe`. Builds run from **source**.\n\n"
        "### Local update\n"
        "Choose Local source folder, then Update Eche. Eche closes, and the update window "
        "runs `BUILD.bat` in the folder you picked.",
    ),
    "updates": (
        "Update from GitHub or local source",
        "### GitHub\n"
        "Downloads the app source (`eche_source`) from sevinOG/eche on main. "
        "It does not download the installer.\n\n"
        "### Local source\n"
        "Uses the folder you pick. That folder needs `BUILD.bat`, `core/`, and `gui/`.\n\n"
        "### Why Eche closes\n"
        "Windows cannot replace `Eche.exe` or the libraries it has open while Eche is running. "
        "After you confirm, Eche stops the bot and quits. A command window named **Eche update** "
        "waits for that exit, copies the new source, keeps cookies and settings, runs `BUILD.bat`, "
        "and starts Eche again.\n\n"
        "### First build on a new folder\n"
        "If `.venv` is missing, the update window creates it and installs `requirements.txt`. "
        "That step needs Python on PATH.",
    ),
    "economy": (
        "Economy / bank balances",
        "### Where money lives\n"
        "On the **Home Server**, each user can have a channel `user-{their id}` "
        "in `bot memory`, with a `bank` thread. Inside is a **pinned message** that starts with "
        "BANK DATA.\n\n"
        "### What the bank browser does\n"
        "Lists users, shows the pin, lets you change the balance, saves back to Discord. "
        "You need the bot token and Home Server ID set first.",
    ),
    "owner_id": (
        "Owner IDs",
        "### What is this?\n"
        "The Discord **user ids** of the people allowed to run owner-only commands "
        "and to ask Eche to mute, timeout, kick, or ban. Separate more than one "
        "with a comma. Other people can still ask for their own context and for lookups.\n\n"
        "### How to copy one\n"
        "1. Discord → Settings → Advanced → **Developer Mode**\n"
        "2. Right-click a name → **Copy User ID**\n"
        "3. Paste here. Add another after a comma → **Save Settings**\n\n"
        "A profile mention works too. The saved value is just the numbers.\n\n"
        "### If you leave it blank\n"
        "Eche uses the Discord application owner instead. A value that is "
        "not a user id matches nobody.\n\n"
        "### Admin tools switch\n"
        "The checkbox under this field is the same switch as **Admin tools** "
        "on the main window. Off, and mute, timeout, kick, and ban are not "
        "sent to the model.",
    ),
    "security": (
        "Security folders",
        "### Owner\n"
        "Set **Owner IDs** and the **Admin tools** switch here. Separate extra "
        "owners with a comma. Those ids can run owner-only commands. The switch "
        "is the same one on the main window. Mute, timeout, kick, and ban run "
        "only for those owners, and only while the switch is on.\n\n"
        "### Cookies\n"
        "Some features (e.g. music / YouTube) may need a cookies file. "
        "Open the cookies folder and save the file as **ytcookies.txt**, then restart the bot. "
        "A copy placed only inside `dist\\Eche` is not the folder this button opens.\n\n"
        "### Secrets (DPAPI)\n"
        "API tokens and the Discord bot token are stored encrypted for your Windows "
        "user in `config/secrets.dpapi.json`. Do not share that file.",
    ),
}