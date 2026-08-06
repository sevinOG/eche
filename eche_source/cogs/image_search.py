# image_search.py (IMAGE ONLY + BUTTONS + 60s TIMEOUT + BLACKOUT)

import discord
import aiohttp
import os
from discord.ext import commands
from discord.ui import View, Button

from core.debuglog import dprint

dprint(">>> [image_search] Module imported")

UNSPLASH_ACCESS = os.getenv("US_ACCESS_TOKEN")
UNSPLASH_SECRET = os.getenv("US_SECRET_TOKEN")

dprint(f">>> [image_search] UNSPLASH_ACCESS loaded: {bool(UNSPLASH_ACCESS)}")
dprint(f">>> [image_search] UNSPLASH_SECRET loaded: {bool(UNSPLASH_SECRET)}")


# =========================================================
# IMAGE NAVIGATOR VIEW (Next / Last / Random)
# =========================================================
class ImageNavigator(View):
    def __init__(self, results, query, index=0):
        super().__init__(timeout=60)  # <-- 1 minute timeout
        self.results = results
        self.query = query
        self.index = index
        self.message = None  # will be set after sending

    async def update(self, interaction):
        url = self.results[self.index]["urls"]["regular"]

        await interaction.response.edit_message(
            content=url,
            view=self
        )

    async def on_timeout(self):
        dprint(">>> [ImageNavigator] Timeout reached — disabling buttons")

        # Disable and darken all buttons
        for child in self.children:
            child.disabled = True
            child.style = discord.ButtonStyle.secondary  # <-- black/gray

        # Update the message to show disabled buttons
        try:
            await self.message.edit(view=self)
        except Exception as e:
            dprint(f">>> [ImageNavigator] Timeout edit failed: {e}")

    @discord.ui.button(label="Last", style=discord.ButtonStyle.secondary)
    async def last_button(self, interaction: discord.Interaction, button: Button):
        if self.index > 0:
            self.index -= 1
        await self.update(interaction)

    @discord.ui.button(label="Random", style=discord.ButtonStyle.primary)
    async def random_button(self, interaction: discord.Interaction, button: Button):
        import random
        self.index = random.randint(0, len(self.results) - 1)
        await self.update(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.index < len(self.results) - 1:
            self.index += 1
        await self.update(interaction)


# =========================================================
# MAIN COG
# =========================================================
class ImageSearch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        dprint(">>> [ImageSearch] Cog initialized")

    async def unsplash_multi(self, query: str, count=10):
        dprint(f">>> [unsplash_multi] Searching for: {query}")

        if not UNSPLASH_ACCESS:
            dprint(">>> [unsplash_multi] ERROR: Missing UNSPLASH_ACCESS")
            return None

        url = "https://api.unsplash.com/search/photos"
        params = {
            "query": query,
            "per_page": count,
            "orientation": "landscape",
            "client_id": UNSPLASH_ACCESS
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                dprint(f">>> [unsplash_multi] Status: {resp.status}")
                if resp.status != 200:
                    text = await resp.text()
                    dprint(f">>> [unsplash_multi] ERROR BODY: {text}")
                    return None
                data = await resp.json()

        results = data.get("results", [])
        dprint(f">>> [unsplash_multi] Found {len(results)} results")
        return results

    @commands.command(name="image")
    async def image(self, ctx, *, query: str):
        dprint(f">>> [image] Triggered by {ctx.author} | Query: {query}")

        results = await self.unsplash_multi(query)
        if not results:
            await ctx.send("No images found.")
            return

        first = results[0]
        first_url = first["urls"]["regular"]

        view = ImageNavigator(results, query, index=0)

        # Send message and store reference so timeout can edit it
        msg = await ctx.send(first_url, view=view)
        view.message = msg  # <-- store message reference


async def setup(bot):
    dprint(">>> [image_search] setup() called — adding Cog...")
    await bot.add_cog(ImageSearch(bot))
    dprint(">>> [image_search] Cog added successfully")