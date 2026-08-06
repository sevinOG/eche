# music_player.py
# Handles in-memory queue + loop modes + playback signaling

import asyncio

class MusicPlayer:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.current = None
        self.play_next = asyncio.Event()

        # Loop modes
        self.loop_song = False
        self.loop_all = False

    async def add(self, item):
        await self.queue.put(item)

    async def get_next(self):
        return await self.queue.get()
