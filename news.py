import discord
from discord import app_commands
from discord.ext import tasks
import feedparser
import os
import json
import urllib.parse
from datetime import datetime, timezone

NEWS_FILE = "/app/data/news_config.json"
SENT_FILE = "/app/data/news_sent.json"


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_config() -> dict:
    """{ "guild_id": { "channel_id": "123", "keywords": ["キーワード1", ...] } }"""
    return _load_json(NEWS_FILE, {})


def _save_config(config: dict):
    _save_json(NEWS_FILE, config)


def _get_sent() -> list[str]:
    return _load_json(SENT_FILE, [])


def _save_sent(sent: list[str]):
    # 直近500件だけ保持
    _save_json(SENT_FILE, sent[-500:])


def _fetch_google_news(keyword: str) -> list[dict]:
    """Google News RSSからキーワードで記事を取得"""
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)

    articles = []
    for entry in feed.entries[:10]:
        articles.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published": entry.get("published", ""),
            "source": entry.get("source", {}).get("title", ""),
        })
    return articles


def setup_news_commands(bot):
    """ニュース監視コマンドをBotに登録"""

    @tasks.loop(minutes=15)
    async def check_news():
        """15分ごとにキーワードでニュースをチェック"""
        config = _get_config()
        sent = _get_sent()

        for guild_id, guild_config in config.items():
            channel_id = int(guild_config.get("channel_id", 0))
            keywords = guild_config.get("keywords", [])

            if not channel_id or not keywords:
                continue

            channel = bot.get_channel(channel_id)
            if not channel:
                continue

            for keyword in keywords:
                articles = _fetch_google_news(keyword)
                for article in articles:
                    article_id = article["url"]
                    if article_id in sent:
                        continue

                    sent.append(article_id)

                    embed = discord.Embed(
                        title=article["title"],
                        url=article["url"],
                        color=discord.Color.dark_teal(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.add_field(name="キーワード", value=keyword, inline=True)
                    if article["source"]:
                        embed.add_field(name="ソース", value=article["source"], inline=True)
                    if article["published"]:
                        embed.add_field(name="公開日", value=article["published"], inline=False)
                    embed.set_footer(text="Google News")

                    await channel.send(embed=embed)

        _save_sent(sent)

    @bot.listen("on_ready")
    async def news_on_ready():
        if not check_news.is_running():
            check_news.start()

    news_group = app_commands.Group(name="news", description="ニュース監視の設定")

    @news_group.command(name="channel", description="ニュース通知先チャンネルを設定")
    @app_commands.describe(channel="通知先チャンネル")
    async def news_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        if guild_id not in config:
            config[guild_id] = {"channel_id": "", "keywords": []}
        config[guild_id]["channel_id"] = str(channel.id)
        _save_config(config)
        await interaction.response.send_message(f"📰 ニュース通知先を {channel.mention} に設定しました。")

    @news_group.command(name="add", description="監視キーワードを追加")
    @app_commands.describe(keyword="監視するキーワード")
    async def news_add(interaction: discord.Interaction, keyword: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        if guild_id not in config:
            config[guild_id] = {"channel_id": "", "keywords": []}

        if keyword in config[guild_id]["keywords"]:
            await interaction.response.send_message(f"⚠️ 「{keyword}」は既に登録されています。", ephemeral=True)
            return

        config[guild_id]["keywords"].append(keyword)
        _save_config(config)
        await interaction.response.send_message(f"✅ キーワード「{keyword}」を追加しました。")

    @news_group.command(name="remove", description="監視キーワードを削除")
    @app_commands.describe(keyword="削除するキーワード")
    async def news_remove(interaction: discord.Interaction, keyword: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)

        if guild_id not in config or keyword not in config[guild_id]["keywords"]:
            await interaction.response.send_message(f"⚠️ 「{keyword}」は登録されていません。", ephemeral=True)
            return

        config[guild_id]["keywords"].remove(keyword)
        _save_config(config)
        await interaction.response.send_message(f"🗑️ キーワード「{keyword}」を削除しました。")

    @news_group.command(name="list", description="登録中のキーワード一覧を表示")
    async def news_list(interaction: discord.Interaction):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        guild_config = config.get(guild_id, {})
        keywords = guild_config.get("keywords", [])
        channel_id = guild_config.get("channel_id", "")

        if not keywords:
            await interaction.response.send_message("📭 キーワードは登録されていません。", ephemeral=True)
            return

        embed = discord.Embed(title="📰 ニュース監視設定", color=discord.Color.dark_teal())
        if channel_id:
            embed.add_field(name="通知チャンネル", value=f"<#{channel_id}>", inline=False)
        embed.add_field(name="キーワード", value="\n".join(f"• {kw}" for kw in keywords), inline=False)
        embed.set_footer(text="15分ごとにチェック")
        await interaction.response.send_message(embed=embed)

    bot.tree.add_command(news_group)
