import discord
from discord import app_commands
import asyncio
import subprocess
import random
import os


class GuildMusicManager:
    """サーバーごとの音楽再生管理"""

    def __init__(self, voice_client: discord.VoiceClient, text_channel: discord.TextChannel):
        self.voice_client = voice_client
        self.text_channel = text_channel
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.volume: float = 0.5
        self.now_playing_msg: discord.Message | None = None
        self.artist_pool: list[dict] = []
        self.artist_loop_task: asyncio.Task | None = None
        self.disconnect_task: asyncio.Task | None = None

    async def search(self, query: str) -> list[dict]:
        """YouTube検索 (yt-dlp使用)"""
        if query.startswith("http://") or query.startswith("https://"):
            return await self._get_url_info(query)

        cmd = [
            "yt-dlp", "--default-search", "ytsearch5",
            "--print", "%(title)s\t%(webpage_url)s",
            "--no-download", "--flat-playlist", query
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()

        results = []
        for line in stdout.decode().strip().split("\n"):
            if "\t" in line:
                title, url = line.split("\t", 1)
                results.append({"title": title, "source": url})
        return results[:5]

    async def _get_url_info(self, url: str) -> list[dict]:
        cmd = [
            "yt-dlp", "--print", "%(title)s\t%(webpage_url)s",
            "--no-download", url
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        line = stdout.decode().strip().split("\n")[0]
        if "\t" in line:
            title, source = line.split("\t", 1)
            return [{"title": title, "source": source}]
        return [{"title": url, "source": url}]

    async def _get_stream_url(self, source_url: str) -> str | None:
        cmd = ["yt-dlp", "-f", "bestaudio", "--get-url", source_url]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        url = stdout.decode().strip().split("\n")[0]
        return url if url else None

    def enqueue(self, track: dict):
        self.queue.append(track)
        if not self.voice_client.is_playing() and not self.voice_client.is_paused():
            asyncio.get_event_loop().create_task(self._play_next())

    async def _play_next(self):
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.delete()
            except discord.NotFound:
                pass
            self.now_playing_msg = None

        if not self.queue:
            self.current = None
            return

        self.current = self.queue.pop(0)
        track = self.current

        try:
            if track.get("local_path"):
                source = discord.FFmpegPCMAudio(track["local_path"])
            else:
                stream_url = await self._get_stream_url(track["source"])
                if not stream_url:
                    await self.text_channel.send(f"⚠️ ストリームURL取得失敗: {track['title']}")
                    await self._play_next()
                    return
                source = discord.FFmpegPCMAudio(
                    stream_url,
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                )

            source = discord.PCMVolumeTransformer(source, volume=self.volume)

            def after_play(error):
                if error:
                    print(f"再生エラー: {error}")
                asyncio.run_coroutine_threadsafe(self._play_next(), asyncio.get_event_loop())

            self.voice_client.play(source, after=after_play)

            embed = discord.Embed(
                title="🎵 再生中",
                description=track["title"],
                color=discord.Color.green(),
            )
            if track.get("source"):
                embed.add_field(name="URL", value=track["source"], inline=False)
            embed.add_field(name="音量", value=f"{int(self.volume * 100)}%", inline=True)
            self.now_playing_msg = await self.text_channel.send(embed=embed)

        except Exception as e:
            await self.text_channel.send(f"⚠️ 再生エラー: {e}")
            await asyncio.sleep(2)
            await self._play_next()

    def change_volume(self, level: int):
        self.volume = max(0, min(100, level)) / 100
        if self.voice_client.source and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self.volume

    async def load_artist_tracks(self, channel_url: str) -> int:
        cmd = [
            "yt-dlp", "--flat-playlist",
            "--print", "%(title)s\t%(url)s",
            "--no-download", channel_url
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()

        tracks = []
        for line in stdout.decode().strip().split("\n"):
            if "\t" in line:
                title, url = line.split("\t", 1)
                tracks.append({"title": title, "source": url})

        random.shuffle(tracks)
        self.artist_pool = tracks
        return len(tracks)

    def _fill_from_pool(self, count: int = 5):
        for _ in range(min(count, len(self.artist_pool))):
            track = self.artist_pool.pop(0)
            self.queue.append(track)
            self.artist_pool.append(track)

    async def start_artist_loop(self, channel_url: str) -> int:
        count = await self.load_artist_tracks(channel_url)
        if count == 0:
            return 0
        self._fill_from_pool(5)

        if not self.voice_client.is_playing():
            await self._play_next()

        async def _loop():
            while True:
                await asyncio.sleep(10)
                if len(self.queue) < 3:
                    self._fill_from_pool(5)

        self.artist_loop_task = asyncio.create_task(_loop())
        return count

    def stop_artist_loop(self):
        if self.artist_loop_task:
            self.artist_loop_task.cancel()
            self.artist_loop_task = None
        self.artist_pool.clear()

    def skip(self):
        if self.voice_client.is_playing():
            self.voice_client.stop()

    def stop(self):
        self.queue.clear()
        self.stop_artist_loop()
        self.current = None
        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

    def pause(self):
        if self.voice_client.is_playing():
            self.voice_client.pause()

    def resume(self):
        if self.voice_client.is_paused():
            self.voice_client.resume()


# サーバーごとの音楽マネージャー
music_managers: dict[int, GuildMusicManager] = {}

ARTIST_CHANNEL_URL = os.getenv("ARTIST_CHANNEL_URL", "")


class SearchSelect(discord.ui.Select):
    """検索結果の選択メニュー"""

    def __init__(self, results: list[dict], guild_id: int):
        self.results = results
        self.guild_id = guild_id
        options = [
            discord.SelectOption(label=r["title"][:100], value=str(i))
            for i, r in enumerate(results)
        ]
        super().__init__(placeholder="曲を選択...", options=options)

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        track = self.results[idx]
        manager = music_managers.get(self.guild_id)
        if manager:
            manager.enqueue(track)
            await interaction.response.edit_message(
                content=f"🎵 キューに追加: **{track['title']}**", view=None
            )
        else:
            await interaction.response.edit_message(
                content="⚠️ Botがボイスチャンネルに接続していません。", view=None
            )


class SearchView(discord.ui.View):
    def __init__(self, results: list[dict], guild_id: int):
        super().__init__(timeout=30)
        self.add_item(SearchSelect(results, guild_id))


def setup_music_commands(bot):
    """音楽コマンドをBotに登録"""

    AUTO_DISCONNECT_SECONDS = 30

    @bot.event
    async def on_voice_state_update(member, before, after):
        """ボイスチャンネルにBotだけになったら自動切断"""
        guild_id = member.guild.id
        manager = music_managers.get(guild_id)
        if not manager:
            return

        vc = manager.voice_client
        if not vc or not vc.is_connected():
            return

        channel = vc.channel
        humans = [m for m in channel.members if not m.bot]

        if len(humans) == 0:
            async def _disconnect():
                await asyncio.sleep(AUTO_DISCONNECT_SECONDS)
                mgr = music_managers.get(guild_id)
                if mgr and mgr.voice_client and mgr.voice_client.is_connected():
                    humans_now = [m for m in mgr.voice_client.channel.members if not m.bot]
                    if len(humans_now) == 0:
                        mgr.stop()
                        await mgr.voice_client.disconnect()
                        del music_managers[guild_id]

            if manager.disconnect_task:
                manager.disconnect_task.cancel()
            manager.disconnect_task = asyncio.create_task(_disconnect())
        else:
            if manager.disconnect_task:
                manager.disconnect_task.cancel()
                manager.disconnect_task = None

    @bot.tree.command(name="join", description="ボイスチャンネルに参加")
    async def join_command(interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("⚠️ 先にボイスチャンネルに参加してください。", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        vc = await channel.connect()
        music_managers[interaction.guild.id] = GuildMusicManager(vc, interaction.channel)
        await interaction.response.send_message(f"🔊 {channel.name} に参加しました。")

    @bot.tree.command(name="leave", description="ボイスチャンネルから退出")
    async def leave_command(interaction: discord.Interaction):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ ボイスチャンネルに接続していません。", ephemeral=True)
            return

        manager.stop()
        await manager.voice_client.disconnect()
        del music_managers[interaction.guild.id]
        await interaction.response.send_message("👋 退出しました。")

    @bot.tree.command(name="play", description="YouTubeから曲を検索・再生")
    @app_commands.describe(query="検索キーワードまたはURL")
    async def play_command(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message("⚠️ 先にボイスチャンネルに参加してください。", ephemeral=True)
            return

        manager = music_managers.get(interaction.guild.id)
        if not manager:
            channel = interaction.user.voice.channel
            vc = await channel.connect()
            manager = GuildMusicManager(vc, interaction.channel)
            music_managers[interaction.guild.id] = manager

        await interaction.response.defer()
        results = await manager.search(query)

        if not results:
            await interaction.followup.send("⚠️ 結果が見つかりませんでした。")
            return

        if len(results) == 1:
            manager.enqueue(results[0])
            await interaction.followup.send(f"🎵 キューに追加: **{results[0]['title']}**")
        else:
            view = SearchView(results, interaction.guild.id)
            await interaction.followup.send("🔍 曲を選択してください:", view=view)

    @bot.tree.command(name="stop", description="再生を停止しキューをクリア")
    async def stop_music_command(interaction: discord.Interaction):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ 再生中の曲はありません。", ephemeral=True)
            return
        manager.stop()
        await interaction.response.send_message("⏹️ 停止しました。")

    @bot.tree.command(name="skip", description="現在の曲をスキップ")
    async def skip_command(interaction: discord.Interaction):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ 再生中の曲はありません。", ephemeral=True)
            return
        manager.skip()
        await interaction.response.send_message("⏭️ スキップしました。")

    @bot.tree.command(name="pause", description="再生を一時停止")
    async def pause_command(interaction: discord.Interaction):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ 再生中の曲はありません。", ephemeral=True)
            return
        manager.pause()
        await interaction.response.send_message("⏸️ 一時停止しました。")

    @bot.tree.command(name="resume", description="再生を再開")
    async def resume_command(interaction: discord.Interaction):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ 一時停止中の曲はありません。", ephemeral=True)
            return
        manager.resume()
        await interaction.response.send_message("▶️ 再開しました。")

    @bot.tree.command(name="volume", description="音量を設定 (0-100)")
    @app_commands.describe(level="音量 (0-100)")
    async def volume_command(interaction: discord.Interaction, level: int):
        manager = music_managers.get(interaction.guild.id)
        if not manager:
            await interaction.response.send_message("⚠️ ボイスチャンネルに接続していません。", ephemeral=True)
            return
        manager.change_volume(level)
        await interaction.response.send_message(f"🔊 音量を {level}% に設定しました。")

    @bot.tree.command(name="artist", description="アーティストチャンネルを無限ループ再生")
    async def artist_command(interaction: discord.Interaction):
        if not ARTIST_CHANNEL_URL:
            await interaction.response.send_message("⚠️ ARTIST_CHANNEL_URL が設定されていません。", ephemeral=True)
            return

        if not interaction.user.voice:
            await interaction.response.send_message("⚠️ 先にボイスチャンネルに参加してください。", ephemeral=True)
            return

        manager = music_managers.get(interaction.guild.id)
        if not manager:
            channel = interaction.user.voice.channel
            vc = await channel.connect()
            manager = GuildMusicManager(vc, interaction.channel)
            music_managers[interaction.guild.id] = manager

        await interaction.response.defer()
        count = await manager.start_artist_loop(ARTIST_CHANNEL_URL)
        if count:
            await interaction.followup.send(f"🎵 {count}曲をロードしました。シャッフル無限ループ再生を開始します！")
        else:
            await interaction.followup.send("⚠️ トラックが見つかりませんでした。")
