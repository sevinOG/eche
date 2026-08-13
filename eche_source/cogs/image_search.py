# image_search.py (IMAGE ONLY + BUTTONS + 60s TIMEOUT + BLACKOUT + QUERY FIELD)

import discord
import aiohttp
import os
import random
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

from core.debuglog import dprint

dprint(">>> [image_search] Module imported")

UNSPLASH_ACCESS = os.getenv("US_ACCESS_TOKEN")
UNSPLASH_SECRET = os.getenv("US_SECRET_TOKEN")

dprint(f">>> [image_search] UNSPLASH_ACCESS loaded: {bool(UNSPLASH_ACCESS)}")
dprint(f">>> [image_search] UNSPLASH_SECRET loaded: {bool(UNSPLASH_SECRET)}")


# =========================================================
# SEARCH MODAL (add / amend query)
# =========================================================
class SearchModal(Modal, title="Image Search"):
    query_input = TextInput(
        label="Search Query",
        placeholder="e.g. mountains, cats, neon city...",
        required=True,
        max_length=100,
        style=discord.TextStyle.short
    )

    def __init__(self, navigator: "ImageNavigator"):
        super().__init__()
        self.navigator = navigator
        if navigator.query:
            self.query_input.default = navigator.query

    async def on_submit(self, interaction: discord.Interaction):
        query = self.query_input.value.strip()
        if not query:
            await interaction.response.send_message("Query cannot be empty.", ephemeral=True)
            return

        dprint(f">>> [SearchModal] New/updated query: {query}")

        results = await self.navigator.cog.unsplash_multi(query)
        if not results:
            # Keep the view alive and show a clear message
            self.navigator.results = []
            self.navigator.query = query
            self.navigator.index = 0
            await interaction.response.edit_message(
                content="No images found for that query. Try another search.",
                view=self.navigator
            )
            return

        self.navigator.results = results
        self.navigator.query = query
        self.navigator.index = 0

        url = results[0]["urls"]["regular"]
        await interaction.response.edit_message(content=url, view=self.navigator)


# =========================================================
# IMAGE NAVIGATOR VIEW (Next / Last / Random + Search)
# =========================================================
class ImageNavigator(View):
    def __init__(self, cog, results, query, index=0):
        super().__init__(timeout=60)  # <-- 1 minute timeout
        self.cog = cog
        self.results = results
        self.query = query
        self.index = index
        self.message = None  # will be set after sending

    async def update(self, interaction: discord.Interaction):
        if not self.results:
            content = "No images found. Use **Search / Edit** to try a new query."
        else:
            content = self.results[self.index]["urls"]["regular"]

        await interaction.response.edit_message(content=content, view=self)

    async def on_timeout(self):
        dprint(">>> [ImageNavigator] Timeout reached — disabling buttons")

        # Disable and darken all buttons
        for child in self.children:
            child.disabled = True
            child.style = discord.ButtonStyle.secondary  # <-- black/gray

        # Update the message to show disabled buttons
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception as e:
            dprint(f">>> [ImageNavigator] Timeout edit failed: {e}")

    @discord.ui.button(label="Last", style=discord.ButtonStyle.secondary, row=0)
    async def last_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        if self.index > 0:
            self.index -= 1
            await self.update(interaction)
        else:
            await interaction.response.defer()  # already at first image

    @discord.ui.button(label="Random", style=discord.ButtonStyle.primary, row=0)
    async def random_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        self.index = random.randint(0, len(self.results) - 1)
        await self.update(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        if self.index < len(self.results) - 1:
            self.index += 1
            await self.update(interaction)
        else:
            await interaction.response.defer()  # already at last image

    @discord.ui.button(label="Search / Edit", style=discord.ButtonStyle.success, row=1)
    async def search_button(self, interaction: discord.Interaction, button: Button):
        modal = SearchModal(self)
        await interaction.response.send_modal(modal)


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
    async def image(self, ctx, *, query: str = None):
        dprint(f">>> [image] Triggered by {ctx.author} | Query: {query}")

        if query:
            results = await self.unsplash_multi(query)
            if not results:
                await ctx.send("No images found.")
                return
            content = results[0]["urls"]["regular"]
        else:
            # No query → open UI only, wait for user to enter one via the modal
            results = []
            query = ""
            content = "Click **Search / Edit** to enter a query and find images."

        view = ImageNavigator(self, results, query, index=0)

        # Send message and store reference so timeout can edit it
        msg = await ctx.send(content, view=view)
        view.message = msg  # <-- store message reference


async def setup(bot):
    dprint(">>> [image_search] setup() called — adding Cog...")
    await bot.add_cog(ImageSearch(bot))
    dprint(">>> [image_search] Cog added successfully")