# image_search.py (IMAGE ONLY + BUTTONS + 60s TIMEOUT + BLACKOUT + QUERY FIELD + EMBED)

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
        self.navigator.results = results or []
        self.navigator.query = query
        self.navigator.index = 0

        embed = self.navigator.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.navigator)


# =========================================================
# IMAGE NAVIGATOR VIEW
# =========================================================
class ImageNavigator(View):
    def __init__(self, cog, results, query, index=0):
        super().__init__(timeout=60)
        self.cog = cog
        self.results = results
        self.query = query
        self.index = index
        self.message = None

    def build_embed(self) -> discord.Embed:
        if not self.results:
            embed = discord.Embed(
                title="Image Search",
                description="**search here** → click the **Search / Edit** button below",
                color=discord.Color.blurple()
            )
            return embed

        photo = self.results[self.index]
        url = photo["urls"]["regular"]
        photographer = photo.get("user", {}).get("name", "Unknown")
        unsplash_link = photo.get("links", {}).get("html", "")

        embed = discord.Embed(
            title=f"Query: {self.query}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=url)
        embed.set_footer(text=f"{self.index + 1} / {len(self.results)}  •  Photo by {photographer}")
        if unsplash_link:
            embed.url = unsplash_link  # makes the title clickable

        return embed

    async def update(self, interaction: discord.Interaction):
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        dprint(">>> [ImageNavigator] Timeout reached — disabling buttons")

        for child in self.children:
            child.disabled = True
            child.style = discord.ButtonStyle.secondary

        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception as e:
            dprint(f">>> [ImageNavigator] Timeout edit failed: {e}")

    # ---------- Top row ----------
    @discord.ui.button(label="Search / Edit", style=discord.ButtonStyle.success, row=0)
    async def search_button(self, interaction: discord.Interaction, button: Button):
        modal = SearchModal(self)
        await interaction.response.send_modal(modal)

    # ---------- Bottom row ----------
    @discord.ui.button(label="Last", style=discord.ButtonStyle.secondary, row=1)
    async def last_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        if self.index > 0:
            self.index -= 1
            await self.update(interaction)
        else:
            await interaction.response.send_message("Already at the first image.", ephemeral=True)

    @discord.ui.button(label="Random", style=discord.ButtonStyle.primary, row=1)
    async def random_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        self.index = random.randint(0, len(self.results) - 1)
        await self.update(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if not self.results:
            await interaction.response.send_message("No results yet. Use **Search / Edit** first.", ephemeral=True)
            return

        if self.index < len(self.results) - 1:
            self.index += 1
            await self.update(interaction)
        else:
            await interaction.response.send_message("Already at the last image.", ephemeral=True)


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
        else:
            results = []
            query = ""

        view = ImageNavigator(self, results, query, index=0)
        embed = view.build_embed()

        msg = await ctx.send(embed=embed, view=view)
        view.message = msg


async def setup(bot):
    dprint(">>> [image_search] setup() called — adding Cog...")
    await bot.add_cog(ImageSearch(bot))
    dprint(">>> [image_search] Cog added successfully")