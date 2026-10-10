import random

import discord

# One shuffled deck for the whole table. Hole cards are dealt two at a time
# with pop(), then the board. Nothing is reused.

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

_RANK_VALUE = {"J": 11, "Q": 12, "K": 13, "A": 14}
_SINGULAR = {
    14: "Ace", 13: "King", 12: "Queen", 11: "Jack", 10: "Ten",
    9: "Nine", 8: "Eight", 7: "Seven", 6: "Six", 5: "Five",
    4: "Four", 3: "Three", 2: "Two",
}
_PLURAL = {
    14: "Aces", 13: "Kings", 12: "Queens", 11: "Jacks", 10: "Tens",
    9: "Nines", 8: "Eights", 7: "Sevens", 6: "Sixes", 5: "Fives",
    4: "Fours", 3: "Threes", 2: "Twos",
}


def fresh_deck() -> list[str]:
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    if len(deck) != 52 or len(set(deck)) != 52:
        raise RuntimeError("deck is not 52 unique cards")
    return deck


def deal_table(deck: list[str], seat_count: int) -> tuple[list[list[str]], list[str]]:
    """Deal seat_count private hands, then five board cards, from one deck."""
    if seat_count < 1 or seat_count * 2 + 5 > len(deck):
        raise RuntimeError("not enough cards left to deal")
    seen: set[str] = set()
    holes: list[list[str]] = []
    for _ in range(seat_count):
        pair = [deck.pop(), deck.pop()]
        if pair[0] == pair[1] or pair[0] in seen or pair[1] in seen:
            raise RuntimeError("duplicate hole card")
        seen.update(pair)
        holes.append(pair)
    board = [deck.pop() for _ in range(5)]
    if len(set(board)) != 5 or any(card in seen for card in board):
        raise RuntimeError("duplicate community card")
    return holes, board


def parse_card(card: str) -> tuple[int, str]:
    rank = card[:-1]
    suit = card[-1]
    value = int(rank) if rank.isdigit() else _RANK_VALUE[rank]
    return value, suit


def rank_five(combo: tuple[tuple[int, str], ...]) -> tuple:
    """Category then kickers. Higher tuples win. Ace plays low only in a wheel."""
    values = sorted((value for value, _ in combo), reverse=True)
    flush = len({suit for _, suit in combo}) == 1
    counts: dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    groups = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    pattern = tuple(count for _, count in groups)
    ordered = tuple(value for value, _ in groups)
    unique = sorted(set(values), reverse=True)
    straight_high = None
    if len(unique) == 5:
        if unique[0] - unique[4] == 4:
            straight_high = unique[0]
        elif unique == [14, 5, 4, 3, 2]:
            straight_high = 5
    if straight_high and flush:
        return (8, straight_high)
    if pattern == (4, 1):
        return (7,) + ordered
    if pattern == (3, 2):
        return (6,) + ordered
    if flush:
        return (5,) + tuple(values)
    if straight_high:
        return (4, straight_high)
    if pattern == (3, 1, 1):
        return (3,) + ordered
    if pattern == (2, 2, 1):
        return (2,) + ordered
    if pattern == (2, 1, 1, 1):
        return (1,) + ordered
    return (0,) + tuple(values)


def best_hand(cards: list[str]) -> tuple:
    """Best five-card hand inside the hole cards plus the board."""
    from itertools import combinations

    parsed = [parse_card(card) for card in cards]
    if len(parsed) < 5:
        values = tuple(sorted((value for value, _ in parsed), reverse=True))
        return (0,) + values
    best = None
    for combo in combinations(parsed, 5):
        rank = rank_five(combo)
        if best is None or rank > best:
            best = rank
    return best


def hand_label(rank: tuple) -> str:
    kind = rank[0]
    if kind == 8:
        return f"Straight flush, {_SINGULAR[rank[1]]} high"
    if kind == 7:
        return f"Four of a kind, {_PLURAL[rank[1]]}"
    if kind == 6:
        return f"Full house, {_PLURAL[rank[1]]} over {_PLURAL[rank[2]]}"
    if kind == 5:
        return f"Flush, {_SINGULAR[rank[1]]} high"
    if kind == 4:
        return f"Straight, {_SINGULAR[rank[1]]} high"
    if kind == 3:
        return f"Three of a kind, {_PLURAL[rank[1]]}"
    if kind == 2:
        return f"Two pair, {_PLURAL[rank[1]]} and {_PLURAL[rank[2]]}"
    if kind == 1:
        return f"Pair of {_PLURAL[rank[1]]}, {_SINGULAR[rank[2]]} kicker"
    return f"{_SINGULAR[rank[1]]} high"


def can_cover(balance, bet) -> bool:
    """Same stake rule as ?bet: a positive bank must cover it, a debt maxes at 70."""
    try:
        balance = float(balance)
        bet = float(bet)
    except (TypeError, ValueError):
        return False
    if bet <= 0:
        return False
    if balance >= bet:
        return True
    return balance < 0 and bet <= 70


def ending_balances(states: dict, ranks: dict, pot: int) -> tuple[dict, list]:
    """Absolute bank after the hand. The dealer has no bank row.

    Each seated player has already put `bet` into the pot. Winners split it.
    A tied dealer keeps the house share; it is not paid to a player.
    """
    if not ranks:
        return {}, []
    best = max(ranks.values())
    winner_ids = [pid for pid, rank in ranks.items() if rank == best]
    count = len(winner_ids)
    base = int(pot) // count
    extra = int(pot) % count
    shares = {
        pid: base + (1 if index < extra else 0)
        for index, pid in enumerate(winner_ids)
    }
    endings = {}
    for pid, state in states.items():
        if state.get("is_dealer"):
            continue
        stake = state["bet"]
        endings[pid] = state["starting_balance"] - stake + shares.get(pid, 0)
    return endings, winner_ids


async def prepare_table(lobby):
    """Seat solvent players, deal unique holes and a board.

    Returns (community, pot, notes). community is None when the table cannot start.
    """
    bet = lobby.betvalue
    notes: list[str] = []
    seated = []
    for member in list(lobby.players.values()):
        if member is None or getattr(member, "bot", False):
            continue
        try:
            bal, _ = await lobby.load_callback(member)
        except Exception:
            notes.append(f"Could not read the bank for {member.mention}.")
            continue
        if not can_cover(bal, bet):
            notes.append(f"{member.mention} cannot cover **{bet}**.")
            continue
        seated.append((member.id, member, bal, False))

    if not seated:
        notes.append("Nobody at the table can cover that bet.")
        return None, 0, notes

    if len(seated) < 2:
        guild = getattr(lobby.ctx, "guild", None)
        dealer = getattr(guild, "me", None)
        if dealer is None:
            notes.append("A second player is needed, and the dealer is not in this server.")
            return None, 0, notes
        seated.append((dealer.id, dealer, 0, True))
        notes.append("Not enough players joined. Starting heads-up vs the dealer.")

    deck = fresh_deck()
    holes, community = deal_table(deck, len(seated))
    pot = 0
    lobby.player_states = {}
    for (pid, member, bal, is_dealer), cards in zip(seated, holes):
        stake = int(bet)
        pot += stake
        lobby.player_states[pid] = {
            "member": member,
            "starting_balance": bal,
            "bet": stake,
            "cards": cards,
            "active": True,
            "is_dealer": is_dealer,
        }
    return community, pot, notes


async def send_hole_cards(lobby, notes: list[str]) -> None:
    for state in lobby.player_states.values():
        if state.get("is_dealer"):
            continue
        cards = state["cards"]
        try:
            dm = await state["member"].create_dm()
            await dm.send(
                f"🃏 Your hole cards:\n**{cards[0]}  {cards[1]}**\n"
                "The five board cards are shared by the table. These two are only yours."
            )
        except Exception:
            notes.append(f"Could not DM {state['member'].mention}.")


def seat_label(state) -> str:
    """Humans are mentioned. The dealer is named so the bot does not ping itself."""
    if state.get("is_dealer"):
        return "Dealer"
    member = state.get("member")
    return getattr(member, "mention", None) or "Player"


def table_text(lobby, community, pot, notes, headline: str) -> str:
    lines = [
        headline,
        "",
        "Players:",
    ]
    for state in lobby.player_states.values():
        lines.append(f"- {seat_label(state)}")
    lines.append("")
    lines.append(f"Board: {' '.join(community)}")
    lines.append(f"Pot: {pot}")
    lines.append("")
    lines.append("Each player was dealt two different hole cards from one shuffled deck.")
    lines.extend(notes)
    return "\n".join(lines)


async def run_showdown(lobby, community, pot, *, title: str, game_label: str) -> None:
    ranks = {}
    for pid, state in lobby.player_states.items():
        ranks[pid] = best_hand(list(state["cards"]) + list(community))
    endings, winner_ids = ending_balances(lobby.player_states, ranks, pot)
    for pid, new_balance in endings.items():
        member = lobby.player_states[pid]["member"]
        await lobby.save_callback(member, new_balance)

    ranked = sorted(ranks.items(), key=lambda item: item[1], reverse=True)
    lines = [f"Board: {' '.join(community)}", ""]
    for pid, rank in ranked:
        state = lobby.player_states[pid]
        mark = "🏆 " if pid in winner_ids else ""
        holes = " ".join(state["cards"])
        lines.append(f"{mark}{seat_label(state)}: **{holes}** — {hand_label(rank)}")
    lines.append("")
    names = ", ".join(seat_label(lobby.player_states[pid]) for pid in winner_ids)
    if len(winner_ids) == 1:
        lines.append(f"Winner: {names}")
    else:
        lines.append(f"Split pot: {names}")
    lines.append(f"Pot: {pot}")

    embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.green())
    try:
        await lobby.message.edit(embed=embed, view=None)
    except Exception:
        pass
    try:
        await lobby.message.channel.send(
            f"🃏 **{game_label} concluded**\n"
            f"**Winner:** {names}\n"
            f"**Pot:** {pot}"
        )
    except Exception:
        pass


async def do_holdem(game, community, pot):
    await run_showdown(
        game,
        community,
        pot,
        title="🃏 Hold'em Showdown",
        game_label="Hold'em",
    )
