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
        "Discord is just the **chat room**. The SI “brain” is separate (see Provider API Key).",
    ),
    "inf_api_key": (
        "Provider API Key (Cloud only)",
        "### What is this?\n"
        "A **provider** runs large SI models on their computers. For **Cloud (Groq)** you "
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
    "xai_api_key": (
        "xAI API key",
        "### What is this?\n"
        "The key for Grok Imagine, the only backend ?asset uses. "
        "It is stored encrypted with your other secrets.\n\n"
        "### Do I need it?\n"
        "Yes, if you turn ?asset on. There is no fallback generator. "
        "A missing key fails closed with an owner-facing message.\n\n"
        "If this key may have been used before the removal, revoke it at the xAI console and paste a new one.",
    ),
    "asset_enabled": (
        "Enable ?asset",
        "### Kill switch\n"
        "Off by default. Only an owner can turn ?asset on in Settings → Media. "
        "While it is off, the command, the asset tool, and any generation request are refused.\n\n"
        "**Owner only** is a second, optional restriction once generation is on.\n\n"
        "### 18+ mode\n"
        "Off by default, set only in Settings. When it is on, any user in a Discord age-restricted channel can use 18+ ?asset, not just the owner, and never in DMs. "
        "While 18+ is on, ?asset in a non-NSFW channel runs under the normal SFW rules "
        "(no 18+ relaxing). "
        "Owner approval is separate and can be on or off. Every minors check stays, including the matcher, media filter, "
        "child check, age vision, incident rule, and the safety footer (the footer text does not change). "
        "The age check is stricter: anything short of clearly adult is blocked, and so are anime, "
        "cartoon, chibi, doll, stylized, and real or identifiable people. The only relaxation is "
        "consensual adult sexual content (Llama Guard S12) on generated output; input still blocks S12, "
        "and S4 and every other category still block.\n\n"
        "### Owner approval\n"
        "On by default. Turning it off skips owner review of each asset. Video stays "
        "available. The hourly caps are the same either way: each user gets 10 images "
        "and 5 videos an hour, counted separately, and each server 40 images and 15 "
        "videos. A blocked or failed try gives its slot back. The bot owner has no caps. "
        "Without 18+ it also makes ?asset owner-only. "
        "18+ can run with approval off; the minors checks still run on every request.",
    ),
    "asset_adult_enabled": (
        "18+",
        "### 18+ mode\n"
        "Off by default, settings-only. See the ?asset help for the full rules: any user in an age-restricted "
        "channels only (in non-NSFW channels ?asset runs under the normal SFW rules), "
        "owner approval optional, stricter age and style checks, and S12 relaxed "
        "on output only.",
    ),
    "asset_approval_enabled": (
        "Require owner approval",
        "### Owner approval\n"
        "On by default. Turning it off skips owner review of each asset. The hourly "
        "caps stay the same: 10 images and 5 videos per user, 40 images and 15 videos "
        "per server (the owner has none). "
        "Without 18+ it also makes ?asset owner-only. 18+ can run with approval off.",
    ),
    "prompt_tools": (
        "Tools in the prompt",
        "### What these boxes do\n"
        "Each box puts that tool in the prompt Eche sees. Off, and the model is not offered it. "
        "A blank install leaves them on.\n\n"
        "The main window **Default tools** switch still turns lookup, thread, and context off together. "
        "These boxes can turn one of them off while the switch stays on.\n\n"
        "### Episodic memory\n"
        "Off, and this server's episode is left out of the prompt. Eche still stores it, "
        "so turning the box back on brings it back.\n\n"
        "Admin tools stay on the Security page.",
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
        "This is required even if you skip the SI provider key.",
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
        "Every third turn, Eche asks an SI to fold the recent lines into the "
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
        "The instruction sheet the bot gives the SI when it folds recent lines "
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
        "A plain-English instruction sheet that is added to **every** SI chat call. "
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
        "Provider code (client.py) — the phone line to the SI",
        "### What is this editor?\n"
        "It opens `core/client.py`, the program code that **calls the SI provider**.\n\n"
        "Near the **top of the file** you will see URLs for Groq and Ollama, key/model "
        "helpers, and `call_groq(...)`.\n\n"
        "### Do I need to edit code on day one?\n"
        "No. Use the Settings backend dropdown + key/model fields. Edit this file only "
        "when you want a custom URL or advanced changes.\n\n"
        "### After Save\n"
        "Stop → Run so the new code loads.",
    ),
    "reasoning": (
        "Reasoning",
        "### What is this?\n"
        "Some models write a thinking trace before the reply. Off, Eche asks for "
        "the reply only. On, that provider may think. The trace opens in "
        "**Reasoning output** and is left out of chat.\n\n"
        "### Which provider\n"
        "One switch. The Provider backend dropdown chooses who it applies to. "
        "Groq, OpenRouter, and Ollama each remember their own value, and "
        "changing the dropdown shows that value. A blank value stays off. "
        "Save applies on the next message. No restart is required for the switch itself.\n\n"
        "### What still reaches chat\n"
        "A sentence the model writes as the reply is still posted. The window "
        "shows the trace. It is not a second copy of the chat.",
    ),
    "reasoning_budget": (
        "Thinking budget",
        "### What is this?\n"
        "A thinking model writes its trace out of the same output tokens as the "
        "reply. When thinking is on, Eche adds this many tokens to the reply cap "
        "so a short channel reply still has room after the trace. Default 1024.\n\n"
        "### Too small\n"
        "The trace uses everything and the reply comes back empty. Eche then asks "
        "once more with thinking off, which is slower. Raise the budget if that "
        "happens often. 0 adds nothing.",
    ),
    "reasoning_models": (
        "Reasoning for this model (Ollama)",
        "### What is this?\n"
        "A choice saved for the Ollama model selected above, so a thinking model "
        "and a plain model can sit side by side.\n\n"
        "- **Follow the Reasoning switch**: switch on means Auto, off means Off.\n"
        "- **Auto**: think only when Ollama says the model can (`ollama show` lists "
        "*thinking*). Other models get a normal request.\n"
        "- **On**: always ask the model to think.\n"
        "- **Off**: ask for the reply only.\n"
        "- **Low / Medium / High**: on, with that effort, for models that take a "
        "level (gpt-oss).\n\n"
        "Background jobs (summaries, heckles) never think.",
    ),
    "reasoning_sampling": (
        "Thinking sampling (Ollama)",
        "### What is this?\n"
        "Sampling used only when a local model is thinking. Qwen-family models "
        "(Qwen, QwQ, and abliterated builds) default to temperature 0.6, top P "
        "0.95, top K 20, which is what Qwen recommends for thinking mode. Other "
        "models keep Eche's usual temperature.\n\n"
        "### Overrides\n"
        "A value here replaces that default for every thinking model. Leave a "
        "field blank to keep the default. Background calls (summaries, heckles) "
        "never think and are not affected.",
    ),
    "ollama_num_ctx": (
        "Context window (Ollama)",
        "### What is this?\n"
        "How many tokens of prompt plus reply the local model holds at once "
        "(Ollama `num_ctx`). Default 8192, which fits a Discord turn.\n\n"
        "### Trade-off\n"
        "Bigger uses more VRAM and loads slower. Changing it makes Ollama reload "
        "the model on the next message.",
    ),
    "unifier": (
        "Unifier (how the prompt is assembled)",
        "### What is this?\n"
        "Before the SI answers, Eche sends one system message: Voice, the task, "
        "the shape of the reply, and a tool list only for the tools offered on that turn. "
        "Voice is left off the plan and the search. "
        "The **unifier / builder** file is the user turn: today, a background line, "
        "the two memory clouds, the recent lines, this server's episode, "
        "and the latest message.\n\n"
        "### Where it saves\n"
        "Package file `core/builder.py` (plain text). Restart the bot after saving.",
    ),
    "context_editor": (
        "Context editor",
        "### What is this?\n"
        "One window for the pins on the home server. Pick User, Bot, or Episodic, "
        "then enter an id.\n\n"
        "### User\n"
        "What Eche remembers about that person. Enter their Discord user ID.\n\n"
        "### Bot\n"
        "What Eche remembers about itself with that person. Same user ID. "
        "Fetch does not create a channel. Reset clears that pin to a blank summary.\n\n"
        "### Episodic\n"
        "That server's episode, stored in the home bot channel. Enter the server ID. "
        "The pin starts with Episodic and that id.\n\n"
        "### Editing\n"
        "Fetch, edit, then save. The pin is live on Discord.",
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
    "channel_completion_tokens": (
        "Channel output tokens",
        "### What is this?\n"
        "The length of one reply in a channel, when someone mentions Eche or "
        "replies to it. The number is completion tokens, the budget the model "
        "is allowed to write.\n\n"
        "### Default\n"
        "64. That is about two or three sentences, one paragraph. "
        "A longer answer asks to open a thread.\n\n"
        "### Range\n"
        "16 through 8192. A reply longer than Discord allows is sent as the "
        "next message in the channel.\n\n"
        "### When it changes\n"
        "Save Settings. The next channel reply uses the new number.",
    ),
    "thread_completion_tokens": (
        "Thread output tokens",
        "### What is this?\n"
        "The length of one post inside a thread. Each later post is its own "
        "reply with the same budget.\n\n"
        "### Default\n"
        "316. That is about one Discord message. A longer post continues in "
        "the following message.\n\n"
        "### Range\n"
        "16 through 8192.\n\n"
        "### When it changes\n"
        "Save Settings. The next thread post uses the new number. The short "
        "planning step before those posts keeps its own budget.",
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

# ---------------------------------------------------------------------------
# Context & Memory sliders (core/context_caps.py). Default and range come
# from the cap table so this copy cannot drift from the code.
# ---------------------------------------------------------------------------
_CAP_HELP: dict[str, str] = {
    "memory_prompt_chars": (
        "The whole memory part of a reply prompt: today's date, both long-term "
        "summaries, the recent messages, this server's episode, and the message "
        "being answered. When it is over, the long-term summaries are shortened "
        "first, then the oldest recent messages. The newest message always stays."
    ),
    "memory_summary_prompt_chars": (
        "How much of each long-term summary the reply sees: **About this person** "
        "and **Your own past outputs with this person**. The stored summary can be longer; this "
        "only limits what goes into each prompt. Cut on a whole bullet or sentence."
    ),
    "episode_prompt_chars": (
        "How much of this server's episode summary (**This server:**) the reply "
        "sees. Turning **Episodic memory** off above leaves it out entirely."
    ),
    "memory_recent_messages": (
        "How many full recent messages are kept word for word for each side: the "
        "person's last messages and your last replies to them. These sit next to "
        "the message being answered. Older ones live only in the long-term summary."
    ),
    "memory_message_chars": (
        "The longest a single message can be in the recent-message buffer, for "
        "both sides. Longer messages are cut with an ellipsis. **0** keeps whole "
        "messages, which was the behaviour before this setting existed."
    ),
    "media_prompt_chars": (
        "Text read from a turn's attachments, link embeds, the replied-to message, "
        "and forwards, all together."
    ),
    "attachment_text_chars": (
        "How much of one attached text or code file is read before it is cut."
    ),
    "media_memory_chars": (
        "How much attachment text is stored with the message in memory, so it can "
        "be summarized later. The prompt for the current turn uses the larger "
        "**Attachments & embeds** cap instead."
    ),
    "memory_fold_every": (
        "How many new messages from one side wait before the summarizer updates "
        "that side's long-term summary. Higher means fewer summarizer calls and "
        "bigger batches. Messages waiting on a summary are never dropped."
    ),
    "memory_summary_chars": (
        "The size of each person's long-term summary pin, and the length target "
        "the summarizer is given. The summarizer's output-token cap follows it. "
        "Discord allows 2000 characters per message, so 1900 is the most. "
        "A smaller number gives tighter summaries."
    ),
    "episode_fold_after": (
        "How many server lines collect in the local file before the summarizer "
        "updates the server's episode summary."
    ),
    "episode_summary_chars": (
        "The size of each server's episode pin, and the length target the "
        "summarizer is given. The output-token cap follows it."
    ),
    "thread_notes_chars": (
        "Search results gathered before a thread's posts are written. Each post "
        "sees these notes. When they run over, the newest results are kept."
    ),
    "thread_posted_chars": (
        "Each new post in a thread sees the posts already written, so it does "
        "not repeat them. **0** sends all of them. A number keeps only the newest "
        "text, which keeps long threads fast on small context windows."
    ),
    "thread_topic_scan": (
        "When someone asks for \"more room\" without naming a subject, Eche looks "
        "back this many channel messages for the subject."
    ),
    "local_history_turns": (
        "Earlier turns of the desktop chat window sent with each new message. "
        "0 sends none."
    ),
}


def _cap_help() -> dict[str, tuple[str, str]]:
    try:
        from core.context_caps import CAPS
    except Exception:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for cap in CAPS:
        what = _CAP_HELP.get(cap.key, cap.hint)
        default = f"{cap.default} {cap.unit}"
        if cap.default == 0 and cap.zero_label:
            default = f"0 ({cap.zero_label})"
        body = (
            "### What is this?\n"
            f"{what}\n\n"
            "### Default\n"
            f"{default}. This is what Eche used before the slider existed.\n\n"
            "### Range\n"
            f"{cap.low} through {cap.high} {cap.unit}.\n\n"
            "### When it changes\n"
            "Save Settings. The next message uses the new number. "
            f"Saved as `{cap.key}` in config/settings.json."
        )
        out[cap.key] = (cap.label, body)
    out["context_memory"] = (
        "Context & Memory",
        "### What is this?\n"
        "Every limit on how much history and memory goes into a prompt, and how "
        "long the stored summaries are.\n\n"
        "### Smaller prompts, faster replies\n"
        "Local models answer faster with less text to read. Lowering **Whole "
        "memory prompt**, **Long-term summary in the prompt**, and **Recent "
        "messages kept** is the quickest win.\n\n"
        "### Tighter summaries\n"
        "**Long-term summary length** and **Server episode length** are the "
        "summarizer's length target. The stored summary only reaches the prompt "
        "up to the **in the prompt** caps, so a summary much longer than those is "
        "mostly unused.\n\n"
        "### Reset\n"
        "**Reset to defaults** puts every slider back to the value Eche used "
        "before these were settings.",
    )
    out["memory_mask_slurs"] = (
        "Mask slurs in memory",
        "### On (default)\n"
        "Slurs in memory pins and memory prompts are starred out, and the "
        "summarizer is told not to record them.\n\n"
        "### Off\n"
        "Memory keeps the words as said, and the summarizer's \"No slurs\" rule "
        "is dropped. Mentions and Discord ids are always stripped either way.",
    )
    out["memory_access"] = (
        "Let users see their memory",
        "### What it does\n"
        "For every **user-<id>** channel in **bot memory**, that person gets a "
        "read-only permission on their own channel: they can see it and read its "
        "history, including the **context** thread with their pins. They cannot "
        "send, post in threads, react, or start threads.\n\n"
        "### Who is skipped\n"
        "People who are not on the home server. Invite them first, then run it "
        "again. #bot and other people's channels are never changed.\n\n"
        "### Command line\n"
        "`python -m core.memory_access --dry-run` (shows who, changes nothing) or "
        "`--yes` from eche_source, or `Eche.exe --unlock-memory --yes`. Running it "
        "twice is safe.",
    )
    out["reset_memory"] = (
        "Reset all memory",
        "### What it does\n"
        "Empties every memory pin Eche keeps on the home server: each person's "
        "**Context for** pin and **Eche with** pin, and every server's "
        "**Episodic** pin. The local rolling buffers (memories/user-*/ and each "
        "server's recent.txt) are cleared too.\n\n"
        "### What it does not do\n"
        "Channels and threads stay. Pins are edited, not deleted. Bank, items, "
        "workers, music, and reminders are not touched. Nobody is pinged.\n\n"
        "### When the bot is running\n"
        "The reset runs inside the bot on its own connection, and the counts show "
        "in the log. When it is stopped, the reset connects on its own.\n\n"
        "### Command line\n"
        "`python -m core.memory_wipe --yes` from eche_source, or "
        "`Eche.exe --reset-memory --yes`. Running it twice is safe.",
    )
    return out


FIELD_HELP.update(_cap_help())
