from cogs.games.showdown import run_showdown


async def do_showdown(lobby, community, pot):
    await run_showdown(
        lobby,
        community,
        pot,
        title="🃏 Poker Showdown",
        game_label="Poker",
    )
