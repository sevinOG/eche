# cogs/hire/heckle/hbuilder.py

import math

def build_heckle_prompt(target_mention: str, amount: float):
    """
    Build a Groq prompt for generating a heckle.
    The higher the amount, the longer and meaner the heckle.
    Returns (prompt, max_chars)
    """

    # Convert amount into a 0–1 intensity scale using log curve
    intensity = min(1.0, math.log10(max(amount, 1)) / 6)

    # Character budget scaling (20 chars → 2000 chars)
    max_chars = int(20 + intensity * (2000 - 20))

    # Length description for the model
    if max_chars < 30:
        length_desc = "extremely short (1–3 words)"
    elif max_chars < 80:
        length_desc = "very short (1 short sentence)"
    elif max_chars < 200:
        length_desc = "short (1–2 sentences)"
    elif max_chars < 600:
        length_desc = "medium length (a few sentences)"
    elif max_chars < 1200:
        length_desc = "long (a detailed paragraph)"
    else:
        length_desc = "very long (a dramatic rant up to the character limit)"

    # Tone scaling
    if intensity < 0.15:
        tone_desc = "light, playful teasing"
    elif intensity < 0.35:
        tone_desc = "mildly insulting but still humorous"
    elif intensity < 0.65:
        tone_desc = "mean-spirited, sharp, and cutting"
    elif intensity < 0.85:
        tone_desc = "harsh, aggressive, and creatively insulting"
    else:
        tone_desc = "brutal, relentless, creatively vicious, but still safe for Discord"

    # Build the final prompt
    prompt = f"""
You are an insult generator hired to heckle someone.

The target is: {target_mention}

Generate a heckle that:
- MUST mention the target directly
- Is {length_desc}
- Uses {tone_desc}
- Speak directly to the target only
- MUST stay under {max_chars} characters (hard limit)
- Do not break character
- Do not reveal your reasoning
- Do not apologize, just deliver the insult.

Heckle:
"""

    return prompt.strip(), max_chars
