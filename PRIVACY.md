# Privacy

Eche is designed to keep **secrets and tokens on your machine**. This document describes what stays local and what may leave your PC when you opt into a feature.

## Tokens and secrets (never leave your machine for “our” servers)

Eche has **no cloud account** and **no Eche backend**.

| Data | Where it lives | Leaves the machine? |
|------|----------------|---------------------|
| Discord bot token | Local config / DPAPI-protected store under your install folder | Only to **Discord** when the bot connects (required for Discord) |
| Groq / other LLM API keys | Local settings / env | Only to the **provider you configure** (e.g. Groq) when AI replies run |
| Unsplash keys (`US_ACCESS_TOKEN`, etc.) | Local env / settings | Only to **Unsplash** when you use the image search command |
| Home server / guild IDs | Local settings / env | Used only by your bot process |

**We do not collect telemetry, crash reports, or analytics.** There is no Eche analytics endpoint.

Do **not** paste tokens into GitHub Issues, Discord public channels, or screenshots.

## Local memory and context

Depending on your setup, Eche may store:

- Conversation **context** and **summaries** (channels/files under `context/`, `memories/`)
- Bot “self-memory” style notes
- Logs under `logs/`
- Optional cookies (e.g. music) under `cookies/`

These are **local files** (or Discord channels your bot creates in **your** servers). They are not uploaded to an Eche service. Anyone with access to that Discord server or your install folder can read what you store there—treat memory channels as sensitive.

## What third parties may receive

| Feature | Provider | Typical data sent |
|---------|----------|-------------------|
| Discord bot online | Discord | Messages/events your bot is allowed to see; token for auth |
| AI chat (if enabled) | e.g. Groq | Prompt text you/the bot builds (may include recent context) + API key |
| Image search (`?image`) | Unsplash | Search query + access key |
| Music / downloads (if used) | YouTube / other via yt-dlp | URLs and public media metadata |

Disable a feature (or omit its API key) if you do not want that traffic.

## Portable installs

Copying the whole app folder (onedir: `Eche.exe` + `_internal/` + `config/`) copies **local secrets and memory** with it. Scrub `config/`, `logs/`, `context/`, `memories/`, and `.env` before sharing a folder.

## Questions

Prefer **GitHub Issues** on [sevinOG/eche](https://github.com/sevinOG/eche) for privacy questions. Do not include tokens in issues.
