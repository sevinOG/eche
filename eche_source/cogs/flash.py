import discord
from discord.ext import commands
import aiohttp
import base64
import os

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# 1. UPDATED: Using the standard Gemini generation endpoint
# We use gemini-2.5-flash-image which is the current industry workhorse for speed/quality
MODEL_ID = "gemini-2.5-flash-image"
BASE_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GOOGLE_API_KEY}"

class Flash(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _call_gemini_image(self, payload):
        """Helper to handle the API request and parse the multimodal response."""
        async with aiohttp.ClientSession() as session:
            async with session.post(BASE_URL, json=payload) as resp:
                data = await resp.json()

        # Check for errors in the response
        if "candidates" not in data:
            print(f"API Error: {data}")
            return None

        # Gemini returns images as "parts" in the response
        parts = data["candidates"][0]["content"]["parts"]
        for part in parts:
            if "inline_data" in part:
                return base64.b64decode(part["inline_data"]["data"])
        return None

    # ---------------------------------------------------------
    # TEXT → IMAGE (GENERATE)
    # ---------------------------------------------------------
    async def flash_generate(self, ctx, prompt: str):
        msg = await ctx.send("⚡ Generating image...")

        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE"]
            }
        }

        img_bytes = await self._call_gemini_image(payload)
        
        if not img_bytes:
            return await msg.edit(content="❌ Failed to generate image. Check your prompt or API key.")

        await msg.delete()
        await ctx.send(file=discord.File(fp=img_bytes, filename="generated.png"))

    # ---------------------------------------------------------
    # IMAGE → EDIT (IMAGE-TO-IMAGE)
    # ---------------------------------------------------------
    async def flash_edit(self, ctx, prompt: str, image_bytes: bytes):
        msg = await ctx.send("⚡ Modifying image...")

        img_b64 = base64.b64encode(image_bytes).decode()

        # In Gemini, you just send the text and the image part together
        payload = {
            "contents": [{
                "parts": [
                    {"text": f"Modify this image based on: {prompt}"},
                    {
                        "inline_data": {
                            "mime_type": "image/png", 
                            "data": img_b64
                        }
                    }
                ]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE"]
            }
        }

        out_bytes = await self._call_gemini_image(payload)

        if not out_bytes:
            return await msg.edit(content="❌ Failed to edit image.")

        await msg.delete()
        await ctx.send(file=discord.File(fp=out_bytes, filename="edited.png"))

    @commands.command()
    async def flash(self, ctx, *, prompt: str):
        # Handle attachments or replies automatically
        if ctx.message.attachments:
            image_bytes = await ctx.message.attachments[0].read()
            return await self.flash_edit(ctx, prompt, image_bytes)

        if ctx.message.reference:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if ref.attachments:
                image_bytes = await ref.attachments[0].read()
                return await self.flash_edit(ctx, prompt, image_bytes)

        return await self.flash_generate(ctx, prompt)

async def setup(bot):
    await bot.add_cog(Flash(bot))