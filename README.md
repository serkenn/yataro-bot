# 彌太郎 Bot

Discordサーバー管理 & 音楽再生Bot。Python (discord.py) + Docker で動作。

## 機能

### 信用度チェック (自動)
新メンバーがサーバーに参加すると自動で投票を開始。
既存メンバーの **2/3以上** が「同意する」ボタンを押すと、指定ロールが付与される。

- 自分自身への投票・二重投票は防止
- 投票状況はリアルタイムで埋め込みメッセージに反映

### ステータス表示
Botのアクティビティに `👀 ○○人のサーバー | ○○人オンライン` を常時表示（1分ごと更新）。

### 画像転送
指定Botが投稿した画像（添付ファイル・埋め込み画像）を、指定チャンネルに自動転送。

### 音楽再生
YouTube検索・再生、キュー管理、アーティスト無限ループ再生など。
yt-dlp + ffmpeg を使用。

## コマンド一覧

### サーバー管理

| コマンド | 説明 |
|---------|------|
| `/status` | サーバーの現在の状況を表示（メンバー数、オンライン数、信用済み数など） |
| `/vote-status` | 保留中の信用度チェック投票一覧を表示 |

### 音楽

| コマンド | 説明 |
|---------|------|
| `/join` | ボイスチャンネルに参加 |
| `/leave` | ボイスチャンネルから退出 |
| `/play <query>` | YouTube検索・再生（複数結果は選択メニュー表示） |
| `/stop` | 再生停止 & キュークリア |
| `/skip` | 現在の曲をスキップ |
| `/pause` | 一時停止 |
| `/resume` | 再開 |
| `/volume <0-100>` | 音量設定 |
| `/artist` | アーティストチャンネルをシャッフル無限ループ再生 |

## セットアップ

### 1. Discord Developer Portal

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリケーションを作成
2. Bot を追加しトークンを取得
3. **Privileged Gateway Intents** を全て有効にする:
   - Server Members Intent
   - Message Content Intent
   - Presence Intent
4. OAuth2 URL Generator で `bot` + `applications.commands` スコープを選択し、サーバーに招待

### 2. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を編集:

```env
# 必須
DISCORD_TOKEN=your_bot_token_here

# 信用チェック後に付与するロールID
TRUST_ROLE_ID=123456789012345678

# 画像転送元のBot ID (0で無効)
FORWARD_FROM_BOT_ID=123456789012345678

# 画像転送先のチャンネルID (0で無効)
FORWARD_TO_CHANNEL_ID=123456789012345678

# 投票チャンネルID (0でシステムチャンネルを使用)
VOTE_CHANNEL_ID=123456789012345678

# /artist コマンド用のYouTubeチャンネルURL
ARTIST_CHANNEL_URL=https://www.youtube.com/@example/videos
```

> IDの取得方法: Discord設定 → 詳細設定 → 開発者モードを有効化 → 右クリック → 「IDをコピー」

### 3. Docker で起動

```bash
docker compose up -d --build
```

### ログ確認

```bash
docker compose logs -f
```

### 停止

```bash
docker compose down
```

## ファイル構成

```
.
├── bot.py               # メイン (信用度チェック, ステータス, 画像転送)
├── music.py             # 音楽再生機能
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example         # 環境変数テンプレート
└── .gitignore
```
