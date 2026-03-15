import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
from datetime import datetime, timezone
from music import setup_music_commands
from news import setup_news_commands

# 設定
TOKEN = os.getenv("DISCORD_TOKEN")
TRUST_ROLE_ID = int(os.getenv("TRUST_ROLE_ID", "0"))
FORWARD_FROM_BOT_ID = int(os.getenv("FORWARD_FROM_BOT_ID", "0"))
FORWARD_TO_CHANNEL_ID = int(os.getenv("FORWARD_TO_CHANNEL_ID", "0"))
VOTE_CHANNEL_ID = int(os.getenv("VOTE_CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 投票管理: {user_id: {"message_id": int, "approved": set(), "total_voters": int}}
pending_votes = {}


class TrustVoteView(discord.ui.View):
    """信用度チェック投票ボタン"""

    def __init__(self, target_user_id: int, total_voters: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id
        self.total_voters = total_voters

    @discord.ui.button(label="同意する ✓", style=discord.ButtonStyle.green, custom_id="trust_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        vote_data = pending_votes.get(self.target_user_id)
        if not vote_data:
            await interaction.response.send_message("この投票は既に終了しています。", ephemeral=True)
            return

        if interaction.user.id == self.target_user_id:
            await interaction.response.send_message("自分自身には投票できません。", ephemeral=True)
            return

        if interaction.user.id in vote_data["approved"]:
            await interaction.response.send_message("既に投票済みです。", ephemeral=True)
            return

        vote_data["approved"].add(interaction.user.id)
        approved_count = len(vote_data["approved"])
        required = vote_data["required"]

        await interaction.response.send_message(
            f"投票しました！ ({approved_count}/{required})", ephemeral=True
        )

        # 投票状況を更新
        message = interaction.message
        target_member = interaction.guild.get_member(self.target_user_id)
        if target_member:
            embed = build_vote_embed(target_member, approved_count, required)
            await message.edit(embed=embed, view=self)

        # 2/3以上が同意したらロール付与
        if approved_count >= required:
            if target_member:
                role = interaction.guild.get_role(TRUST_ROLE_ID)
                if role:
                    await target_member.add_roles(role)
                    embed = build_vote_embed(target_member, approved_count, required, completed=True)
                    self.stop()
                    await message.edit(embed=embed, view=None)
                    await message.channel.send(
                        f"🎉 {target_member.mention} が信用されました！ロール「{role.name}」を付与しました。"
                    )
            del pending_votes[self.target_user_id]


def build_vote_embed(member: discord.Member, approved: int, required: int, completed: bool = False):
    """投票状況の埋め込みメッセージを作成"""
    if completed:
        embed = discord.Embed(
            title="✅ 信用度チェック完了",
            description=f"{member.mention} は信用されました！",
            color=discord.Color.green(),
        )
    else:
        embed = discord.Embed(
            title="🔍 信用度チェック",
            description=f"新メンバー {member.mention} が参加しました。\n信用できると思う方は「同意する」ボタンを押してください。",
            color=discord.Color.orange(),
        )

    embed.add_field(name="同意数", value=f"{approved} / {required}", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"必要同意数: メンバーの2/3 ({required}人)")
    return embed


@bot.event
async def on_ready():
    print(f"✅ {bot.user} としてログインしました")
    await bot.tree.sync()
    update_status.start()


@bot.event
async def on_member_join(member: discord.Member):
    """新メンバー参加時に信用度チェックを開始"""
    if member.bot:
        return

    channel = None
    if VOTE_CHANNEL_ID:
        channel = bot.get_channel(VOTE_CHANNEL_ID)
    if not channel:
        channel = member.guild.system_channel
    if not channel:
        return

    # Botと対象ユーザーを除いた投票可能メンバー数
    eligible_voters = [
        m for m in member.guild.members if not m.bot and m.id != member.id
    ]
    total_voters = len(eligible_voters)

    if total_voters == 0:
        return

    # 2/3 (切り上げ)
    required = -(-total_voters * 2 // 3)

    pending_votes[member.id] = {
        "approved": set(),
        "total_voters": total_voters,
        "required": required,
    }

    embed = build_vote_embed(member, 0, required)
    view = TrustVoteView(member.id, total_voters)
    msg = await channel.send(embed=embed, view=view)
    pending_votes[member.id]["message_id"] = msg.id


@bot.event
async def on_message(message: discord.Message):
    """特定Botの画像を特定チャンネルに転送"""
    if FORWARD_FROM_BOT_ID and FORWARD_TO_CHANNEL_ID:
        if message.author.id == FORWARD_FROM_BOT_ID:
            images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
            # 埋め込み画像もチェック
            embed_images = [e.image.url for e in message.embeds if e.image]

            if images or embed_images:
                target_channel = bot.get_channel(FORWARD_TO_CHANNEL_ID)
                if target_channel:
                    embed = discord.Embed(
                        title="📷 画像転送",
                        description=f"<#{message.channel.id}> から転送",
                        color=discord.Color.blue(),
                        timestamp=datetime.now(timezone.utc),
                    )

                    for img in images:
                        embed.set_image(url=img.url)
                        await target_channel.send(embed=embed)
                        embed = discord.Embed(color=discord.Color.blue())

                    for url in embed_images:
                        embed.set_image(url=url)
                        await target_channel.send(embed=embed)
                        embed = discord.Embed(color=discord.Color.blue())

    await bot.process_commands(message)


@tasks.loop(minutes=1)
async def update_status():
    """ステータスを更新 (メンバー数などを表示)"""
    total_members = 0
    total_online = 0

    for guild in bot.guilds:
        total_members += guild.member_count or 0
        total_online += sum(
            1 for m in guild.members
            if m.status != discord.Status.offline and not m.bot
        )

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{total_members}人のサーバー | {total_online}人オンライン",
    )
    await bot.change_presence(activity=activity)


@bot.tree.command(name="status", description="サーバーの現在の状況を表示")
async def status_command(interaction: discord.Interaction):
    guild = interaction.guild
    online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
    total = sum(1 for m in guild.members if not m.bot)

    embed = discord.Embed(title="📊 サーバー状況", color=discord.Color.blurple())
    embed.add_field(name="総メンバー数", value=str(total), inline=True)
    embed.add_field(name="オンライン", value=str(online), inline=True)
    embed.add_field(name="保留中の投票", value=str(len(pending_votes)), inline=True)

    trust_role = guild.get_role(TRUST_ROLE_ID)
    if trust_role:
        trusted = len(trust_role.members)
        embed.add_field(name="信用済みメンバー", value=str(trusted), inline=True)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="vote-status", description="現在の投票状況を確認")
async def vote_status_command(interaction: discord.Interaction):
    if not pending_votes:
        await interaction.response.send_message("現在保留中の投票はありません。", ephemeral=True)
        return

    embed = discord.Embed(title="📋 保留中の投票一覧", color=discord.Color.orange())
    for user_id, data in pending_votes.items():
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else f"ID: {user_id}"
        approved = len(data["approved"])
        required = data["required"]
        embed.add_field(name=name, value=f"{approved}/{required} 同意", inline=False)

    await interaction.response.send_message(embed=embed)


setup_music_commands(bot)
setup_news_commands(bot)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。")
        exit(1)
    bot.run(TOKEN)
