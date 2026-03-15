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

DEFAULT_CONFIG = {"channel_id": "", "keywords": [], "exclude_words": []}


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
    return _load_json(NEWS_FILE, {})


def _save_config(config: dict):
    _save_json(NEWS_FILE, config)


def _get_sent() -> list[str]:
    return _load_json(SENT_FILE, [])


def _save_sent(sent: list[str]):
    _save_json(SENT_FILE, sent[-500:])


def _build_query(keyword: str) -> str:
    """キーワードをGoogle News検索クエリに変換
    A+B → "A" "B" (AND検索: 両方含む記事のみ)
    A   → A (通常検索)
    """
    if "+" in keyword:
        parts = [p.strip() for p in keyword.split("+") if p.strip()]
        return " ".join(f'"{p}"' for p in parts)
    return keyword


def _matches_keyword(keyword: str, title: str) -> bool:
    """AND検索の場合、タイトルに全てのワードが含まれるか確認"""
    if "+" in keyword:
        parts = [p.strip().lower() for p in keyword.split("+") if p.strip()]
        title_lower = title.lower()
        return all(p in title_lower for p in parts)
    return True


def _is_excluded(title: str, exclude_words: list[str]) -> bool:
    """除外ワードがタイトルに含まれていたらTrue"""
    title_lower = title.lower()
    return any(w.lower() in title_lower for w in exclude_words)


def _fetch_google_news(keyword: str) -> list[dict]:
    """Google News RSSからキーワードで記事を取得"""
    query = _build_query(keyword)
    encoded = urllib.parse.quote(query)
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


def _ensure_config(config: dict, guild_id: str) -> dict:
    if guild_id not in config:
        config[guild_id] = {**DEFAULT_CONFIG}
    else:
        for key, val in DEFAULT_CONFIG.items():
            if key not in config[guild_id]:
                config[guild_id][key] = val
    return config


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
            exclude_words = guild_config.get("exclude_words", [])

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

                    # AND検索: タイトルに全ワードが含まれるか確認
                    if not _matches_keyword(keyword, article["title"]):
                        continue

                    # 除外ワードチェック
                    if _is_excluded(article["title"], exclude_words):
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
        config = _ensure_config(config, guild_id)
        config[guild_id]["channel_id"] = str(channel.id)
        _save_config(config)
        await interaction.response.send_message(f"📰 ニュース通知先を {channel.mention} に設定しました。")

    @news_group.command(name="add", description="監視キーワードを追加 (A+BでAND検索)")
    @app_commands.describe(keyword="監視するキーワード (例: AI+ロボット)")
    async def news_add(interaction: discord.Interaction, keyword: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        config = _ensure_config(config, guild_id)

        if keyword in config[guild_id]["keywords"]:
            await interaction.response.send_message(f"⚠️ 「{keyword}」は既に登録されています。", ephemeral=True)
            return

        config[guild_id]["keywords"].append(keyword)
        _save_config(config)

        if "+" in keyword:
            parts = keyword.split("+")
            desc = " AND ".join(f"「{p.strip()}」" for p in parts)
            await interaction.response.send_message(f"✅ {desc} の両方を含む記事を監視します。")
        else:
            await interaction.response.send_message(f"✅ キーワード「{keyword}」を追加しました。")

    @news_group.command(name="remove", description="監視キーワードを削除")
    @app_commands.describe(keyword="削除するキーワード")
    async def news_remove(interaction: discord.Interaction, keyword: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)

        if guild_id not in config or keyword not in config[guild_id].get("keywords", []):
            await interaction.response.send_message(f"⚠️ 「{keyword}」は登録されていません。", ephemeral=True)
            return

        config[guild_id]["keywords"].remove(keyword)
        _save_config(config)
        await interaction.response.send_message(f"🗑️ キーワード「{keyword}」を削除しました。")

    @news_group.command(name="exclude", description="除外ワードを追加 (このワードを含む記事を無視)")
    @app_commands.describe(word="除外するワード")
    async def news_exclude(interaction: discord.Interaction, word: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        config = _ensure_config(config, guild_id)

        if word in config[guild_id]["exclude_words"]:
            await interaction.response.send_message(f"⚠️ 「{word}」は既に除外リストにあります。", ephemeral=True)
            return

        config[guild_id]["exclude_words"].append(word)
        _save_config(config)
        await interaction.response.send_message(f"🚫 「{word}」を含む記事を除外します。")

    @news_group.command(name="unexclude", description="除外ワードを解除")
    @app_commands.describe(word="解除するワード")
    async def news_unexclude(interaction: discord.Interaction, word: str):
        config = _get_config()
        guild_id = str(interaction.guild.id)

        if guild_id not in config or word not in config[guild_id].get("exclude_words", []):
            await interaction.response.send_message(f"⚠️ 「{word}」は除外リストにありません。", ephemeral=True)
            return

        config[guild_id]["exclude_words"].remove(word)
        _save_config(config)
        await interaction.response.send_message(f"✅ 「{word}」の除外を解除しました。")

    @news_group.command(name="list", description="登録中のキーワード・除外ワード一覧を表示")
    async def news_list(interaction: discord.Interaction):
        config = _get_config()
        guild_id = str(interaction.guild.id)
        guild_config = config.get(guild_id, {})
        keywords = guild_config.get("keywords", [])
        exclude_words = guild_config.get("exclude_words", [])
        channel_id = guild_config.get("channel_id", "")

        if not keywords and not exclude_words:
            await interaction.response.send_message("📭 設定がありません。", ephemeral=True)
            return

        embed = discord.Embed(title="📰 ニュース監視設定", color=discord.Color.dark_teal())
        if channel_id:
            embed.add_field(name="通知チャンネル", value=f"<#{channel_id}>", inline=False)
        if keywords:
            kw_list = []
            for kw in keywords:
                if "+" in kw:
                    parts = kw.split("+")
                    kw_list.append(f"• {' AND '.join(p.strip() for p in parts)}")
                else:
                    kw_list.append(f"• {kw}")
            embed.add_field(name="キーワード", value="\n".join(kw_list), inline=False)
        if exclude_words:
            embed.add_field(name="除外ワード", value="\n".join(f"• {w}" for w in exclude_words), inline=False)
        embed.set_footer(text="15分ごとにチェック")
        await interaction.response.send_message(embed=embed)

    bot.tree.add_command(news_group)
