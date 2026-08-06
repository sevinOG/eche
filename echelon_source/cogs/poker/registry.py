from cogs.games.registry import register_game
from .poker import PokerGame

register_game("Poker", PokerGame)
