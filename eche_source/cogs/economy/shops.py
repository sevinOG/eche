# economy/shops.py

import discord
import os
import io
from discord.ext import commands

HOME_SERVER_ID = int(os.getenv("HOME_SERVER_ID", "0"))

OWNED_CHANNEL = "owned-items"
HOSTED_CHANNEL = "hosted-items"


class ConfirmPurchaseView(discord.ui.View):
    def __init__(self, cog, ctx, buyer, seller, item_name, price, item_entry, seller_hosted_file):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.buyer = buyer
        self.seller = seller
        self.item_name = item_name
        self.price = price
        self.item_entry = item_entry
        self.seller_hosted_file = seller_hosted_file
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.seller.id:
            await interaction.response.send_message("Only the seller can respond to this.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
                await self.ctx.send("⏳ Sale request expired — no response from the seller.")
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Accept Sale", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Re-check balances and existence
        buyer_balance = await self.cog.get_balance(self.buyer)
        if buyer_balance < self.price:
            await interaction.response.send_message("❌ Buyer no longer has enough coins.", ephemeral=True)
            self.stop()
            return

        # Reload seller hosted items to ensure item still exists
        items = await self.cog.load_items(self.seller_hosted_file)
        if self.item_entry not in items:
            await interaction.response.send_message("❌ Item is no longer hosted.", ephemeral=True)
            self.stop()
            return

        # Bank transfer
        await self.cog.set_balance(self.buyer, buyer_balance - self.price)
        seller_balance = await self.cog.get_balance(self.seller)
        await self.cog.set_balance(self.seller, seller_balance + self.price)

        # Remove from seller hosted
        items.remove(self.item_entry)
        await self.cog.save_items(self.seller_hosted_file, "HOSTED ITEMS", items)

        # Move message to buyer owned
        owned_file, _ = await self.cog.ensure_shop_files(self.buyer)
        owned_items = await self.cog.load_items(owned_file)

        msg_id = int(self.item_entry.split(" | ")[2])
        original_msg = await self.seller_hosted_file.channel.fetch_message(msg_id)

        files = []
        for attachment in original_msg.attachments:
            data = await attachment.read()
            files.append(discord.File(io.BytesIO(data), filename=attachment.filename))

        await owned_file.channel.send(content=original_msg.content, files=files)

        owned_items.append(self.item_name)
        await self.cog.save_items(owned_file, "OWNED ITEMS", owned_items)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.message.edit(view=self)

        await interaction.response.send_message(
            f"✅ Sale completed: **{self.item_name}** sold to **{self.buyer.display_name}** "
            f"for **{self.price:,} coins**."
        )
        await self.ctx.send(
            f"✅ {self.buyer.mention} bought **{self.item_name}** from {self.seller.mention} "
            f"for **{self.price:,} coins**."
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self.message.edit(view=self)
        await interaction.response.send_message("❌ Sale declined.", ephemeral=True)
        await self.ctx.send(
            f"❌ {self.seller.mention} declined the sale of **{self.item_name}** to {self.buyer.mention}."
        )
        self.stop()


class Shops(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------- BANK HELPERS --------
    async def get_balance(self, member):
        bank = self.bot.get_cog("Bank")
        return await bank.get_balance(member)

    async def set_balance(self, member, amount):
        bank = self.bot.get_cog("Bank")
        return await bank.set_balance(member, amount)

    # -------- INTERNAL FILE HELPERS --------
    async def _find_or_make_pin(self, channel, header_prefix: str, seed: str):
        """Return a pin whose content starts with header_prefix; create if missing."""
        pins = await channel.pins()
        for p in pins:
            if (p.content or "").startswith(header_prefix):
                return p
        msg = await channel.send(seed)
        await msg.pin()
        return msg

    async def ensure_shop_files(self, member):
        """
        Shop inventory lives on the home server under memory-{user}/owned-items.
        Both OWNED ITEMS and HOSTED ITEMS pins share that single channel —
        we no longer create a separate #hosted-items channel.
        """
        guild = self.bot.get_guild(HOME_SERVER_ID)
        if guild is None:
            return None, None

        category = discord.utils.get(guild.categories, name=f"memory-{member.id}")
        if category is None:
            category = await guild.create_category(f"memory-{member.id}")

        # Prefer legacy #hosted-items if it already exists; otherwise one #owned-items channel
        legacy_hosted = discord.utils.get(category.text_channels, name=HOSTED_CHANNEL)
        owned_channel = discord.utils.get(category.text_channels, name=OWNED_CHANNEL)
        if owned_channel is None:
            owned_channel = await category.create_text_channel(OWNED_CHANNEL)

        owned_file = await self._find_or_make_pin(
            owned_channel, "OWNED ITEMS", "OWNED ITEMS\n"
        )

        if legacy_hosted is not None:
            hosted_file = await self._find_or_make_pin(
                legacy_hosted, "HOSTED ITEMS", "HOSTED ITEMS\n"
            )
        else:
            # New installs: host pin lives next to owned pin (no extra channel)
            hosted_file = await self._find_or_make_pin(
                owned_channel, "HOSTED ITEMS", "HOSTED ITEMS\n"
            )

        return owned_file, hosted_file

    async def load_items(self, file_message):
        lines = file_message.content.splitlines()
        return lines[1:]

    async def save_items(self, file_message, header, items):
        content = header + "\n" + "\n".join(items)
        await file_message.edit(content=content)

    # -------- ROOT: ?shop --------
    @commands.group(name="shop", invoke_without_command=True)
    async def shop(self, ctx):
        await self.ensure_shop_files(ctx.author)

        embed = discord.Embed(
            title="🛒 Shop System",
            description=(
                "**Commands:**\n"
                "• `?shop` — Menu\n"
                "• `?shop owned`\n"
                "• `?shop hosted`\n"
                "• `?shop createitem \"Name\" price`\n"
                "• `?shop buy item price`\n"
                "• `?shop sell item`\n"
                "• `?shop give item @user`\n"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    # -------- ?shop owned --------
    @shop.command(name="owned")
    async def shop_owned(self, ctx):
        owned_file, _ = await self.ensure_shop_files(ctx.author)
        items = await self.load_items(owned_file)

        if not items:
            return await ctx.send("🛒 You don't own any items.")

        embed = discord.Embed(
            title="🧰 Owned Items",
            description="\n".join(f"• {i}" for i in items),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    # -------- ?shop hosted --------
    @shop.command(name="hosted")
    async def shop_hosted(self, ctx):
        _, hosted_file = await self.ensure_shop_files(ctx.author)
        items = await self.load_items(hosted_file)

        if not items:
            return await ctx.send("📦 You are not hosting any items.")

        embed = discord.Embed(
            title="📦 Hosted Items",
            description="\n".join(f"• {i}" for i in items),
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)

    # -------- ?shop createitem "Name" price --------
    @shop.command(name="createitem")
    async def shop_createitem(self, ctx, *, args: str = None):
        if args is None:
            return await ctx.send('Usage: `?shop createitem "Item Name" price`')

        if not args.startswith('"'):
            return await ctx.send('❌ Name must be in quotes.')

        try:
            end_quote = args.index('"', 1)
            item_name = args[1:end_quote]
        except ValueError:
            return await ctx.send("❌ Missing closing quote.")

        remaining = args[end_quote + 1:].strip()
        if not remaining:
            return await ctx.send("❌ Missing price.")

        try:
            price = int(remaining)
        except ValueError:
            return await ctx.send("❌ Price must be a number.")

        if price < 10000:
            return await ctx.send("❌ Minimum price is 10,000.")

        _, hosted_file = await self.ensure_shop_files(ctx.author)
        items = await self.load_items(hosted_file)

        for entry in items:
            if entry.split(" | ")[0].lower() == item_name.lower():
                return await ctx.send("❌ You already have an item with that name.")

        files = []
        for attachment in ctx.message.attachments:
            data = await attachment.read()
            files.append(discord.File(io.BytesIO(data), filename=attachment.filename))

        forwarded = await hosted_file.channel.send(
            content=f"📦 **{item_name}**\nPrice: **{price:,} coins**",
            files=files
        )

        new_entry = f"{item_name} | {price} | {forwarded.id}"
        items.append(new_entry)
        await self.save_items(hosted_file, "HOSTED ITEMS", items)

        await ctx.send(f"✅ Item **{item_name}** created and hosted for **{price:,} coins**.")

    # -------- ?shop buy item price (with confirmation buttons) --------
    @shop.command(name="buy")
    async def shop_buy(self, ctx, *, args: str = None):
        if args is None:
            return await ctx.send("Usage: `?shop buy itemname price`")

        parts = args.split()
        if len(parts) < 2:
            return await ctx.send("Usage: `?shop buy itemname price`")

        item_name = parts[0]
        try:
            offered_price = int(parts[1])
        except ValueError:
            return await ctx.send("❌ Price must be a number.")

        guild = self.bot.get_guild(HOME_SERVER_ID)

        seller = None
        seller_hosted_file = None
        item_entry = None
        real_price = None

        for member in guild.members:
            _, hosted_file = await self.ensure_shop_files(member)
            items = await self.load_items(hosted_file)

            for entry in items:
                name, price, msg_id = entry.split(" | ")
                if name.lower() == item_name.lower():
                    seller = member
                    seller_hosted_file = hosted_file
                    item_entry = entry
                    real_price = int(price)
                    break

            if seller:
                break

        if seller is None:
            return await ctx.send("❌ That item is not hosted by anyone.")

        if offered_price != real_price:
            return await ctx.send(f"❌ Correct price is **{real_price:,}** coins.")

        if seller.id == ctx.author.id:
            return await ctx.send("❌ You cannot buy your own item.")

        buyer_balance = await self.get_balance(ctx.author)
        if buyer_balance < real_price:
            return await ctx.send("❌ You do not have enough coins.")

        view = ConfirmPurchaseView(
            cog=self,
            ctx=ctx,
            buyer=ctx.author,
            seller=seller,
            item_name=item_name,
            price=real_price,
            item_entry=item_entry,
            seller_hosted_file=seller_hosted_file
        )

        msg = await ctx.send(
            f"{seller.mention} {ctx.author.mention}\n"
            f"**{item_name}** — Purchase Request\n"
            f"Price: **{real_price:,} coins**",
            view=view
        )
        view.message = msg

    # -------- ?shop sell item (remove from owned) --------
    @shop.command(name="sell")
    async def shop_sell(self, ctx, *, item: str = None):
        if item is None:
            return await ctx.send("Usage: `?shop sell itemname`")

        owned_file, _ = await self.ensure_shop_files(ctx.author)
        items = await self.load_items(owned_file)

        if item not in items:
            return await ctx.send("❌ You don't own that item.")

        items.remove(item)
        await self.save_items(owned_file, "OWNED ITEMS", items)

        await ctx.send(f"🗑️ Removed **{item}** from your owned items.")

    # -------- ?shop give item @user (user-level transfer) --------
    @shop.command(name="give")
    async def shop_give(self, ctx, item_name: str = None, member: discord.Member = None):
        if item_name is None or member is None:
            return await ctx.send("Usage: `?shop give itemname @user`")

        if member.id == ctx.author.id:
            return await ctx.send("❌ You already are that user.")

        # Search ONLY invoking user's inventories
        owned_file, hosted_file = await self.ensure_shop_files(ctx.author)
        owned_items = await self.load_items(owned_file)
        hosted_items = await self.load_items(hosted_file)

        original_msg = None
        from_hosted = False
        item_entry = None

        if item_name in owned_items:
            owned_items.remove(item_name)
            await self.save_items(owned_file, "OWNED ITEMS", owned_items)
        else:
            for entry in hosted_items:
                name, price, msg_id = entry.split(" | ")
                if name.lower() == item_name.lower():
                    from_hosted = True
                    item_entry = entry
                    hosted_items.remove(entry)
                    await self.save_items(hosted_file, "HOSTED ITEMS", hosted_items)
                    msg_id_int = int(msg_id)
                    original_msg = await hosted_file.channel.fetch_message(msg_id_int)
                    break

            if not from_hosted:
                return await ctx.send("❌ You don't own or host that item.")

        target_owned_file, _ = await self.ensure_shop_files(member)
        target_owned_items = await self.load_items(target_owned_file)

        if original_msg:
            files = []
            for attachment in original_msg.attachments:
                data = await attachment.read()
                files.append(discord.File(io.BytesIO(data), filename=attachment.filename))

            await target_owned_file.channel.send(content=original_msg.content, files=files)
        else:
            await target_owned_file.channel.send(f"📦 **{item_name}** (Gifted from {ctx.author.display_name})")

        target_owned_items.append(item_name)
        await self.save_items(target_owned_file, "OWNED ITEMS", target_owned_items)

        await ctx.send(
            f"✅ Transferred **{item_name}** from **{ctx.author.display_name}** "
            f"to **{member.display_name}**."
        )

    # -------- OWNER UTILS (optional) --------
    @commands.command(name="shoprebuild")
    @commands.is_owner()
    async def shop_rebuild(self, ctx, member: discord.Member = None):
        if member is None:
            return await ctx.send("Usage: `?shoprebuild @user`")

        owned_file, hosted_file = await self.ensure_shop_files(member)
        await self.save_items(owned_file, "OWNED ITEMS", [])
        await self.save_items(hosted_file, "HOSTED ITEMS", [])

        await ctx.send(f"🔧 Rebuilt shop files for {member.display_name}.")


async def setup(bot):
    await bot.add_cog(Shops(bot))
