WHITELISTED_BOTS = {
    974297735559806986, 1486240665951142079,  # genai, sevin
}

def allow_bot(bot_id):
    WHITELISTED_BOTS.add(bot_id)

def is_allowed_bot(bot_id):
    return bot_id in WHITELISTED_BOTS
