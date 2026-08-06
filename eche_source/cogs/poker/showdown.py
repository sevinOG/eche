import discord

# ---------------------------------------------------------
# Hand evaluation
# ---------------------------------------------------------

def card_value(rank):
    if rank.isdigit():
        return int(rank)
    return {"J": 11, "Q": 12, "K": 13, "A": 14}[rank]


def evaluate_hand(cards):
    ranks = sorted([card_value(c[:-1]) for c in cards], reverse=True)
    high = ranks[0]

    # Simple pair vs high card logic
    if len(set(ranks)) < len(ranks):
        return (2, high)  # pair
    return (1, high)      # high card


# ---------------------------------------------------------
# Showdown logic
# ---------------------------------------------------------

async def do_showdown(lobby, community, pot):
    results = []

    for pid, state in lobby.player_states.items():
        cards = state["cards"] + community
        score, high = evaluate_hand(cards)
        results.append((pid, score, high))

    # Determine winner
    results.sort(key=lambda x: (x[1], x[2]), reverse=True)
    winner_pid, _, _ = results[0]
    winner_state = lobby.player_states[winner_pid]
    winner_member = winner_state["member"]

    # Pay out
    if not winner_state.get("is_dealer"):
        new_balance = winner_state["starting_balance"] + pot
        await lobby.save_callback(winner_member, new_balance)

    # Build result embed
    desc = "Showdown Results:\n\n"
    for pid, score, high in results:
        member = lobby.player_states[pid]["member"]
        desc += f"- {member.mention}: score {score}, high {high}\n"

    desc += f"\n🏆 Winner: {winner_member.mention}\nPot: {pot}"

    embed = discord.Embed(
        title="🃏 Poker Showdown",
        description=desc,
        color=discord.Color.green()
    )

    # Replace GUI with final embed
    await lobby.message.edit(embed=embed, view=None)

    # ---------------------------------------------------------
    # ⭐ Final text summary message (no GUI)
    # ---------------------------------------------------------

    players = ", ".join(
        state["member"].mention
        for state in lobby.player_states.values()
    )

    await lobby.message.channel.send(
        f"🃏 **Poker Game Concluded**\n"
        f"**Game Type:** Showdown\n"
        f"**Winner:** {winner_member.mention}\n"
        f"**Pot:** {pot}"
    )
