# 260215_0643~260215_1838


import discord
from discord import app_commands # スラッシュコマンドの実装に必要
import os
from dotenv import load_dotenv
import datetime
# import csv
# import re
# from collections import deque # csvデータをキューとして扱うため
# from ffmpeg import _ffmpeg  # VCでの音声再生に必要（1gouのときはこっち）
import nacl
from discord.ext import voice_recv, tasks # Botに音声認識を行わせるため，discord.pyのtaskを使う（これにはloopがある）
import io # 特にメモリ上でWAVファイル作成時に使用
import wave # wavファイル関連
import speech_recognition as sr # 音声認識用ライブラリ?
import time # 話し時間計測のため
import gc # メモリ解放に関わる
import asyncio # 音声解析の非同期処理とその制限のため
import random # 再生音源のランダム化のため
import audioop # ステレオ→モノラル変換のため


# ------------------------------
# ↓ 変数定義
# ------------------------------


load_dotenv()

# discordボットの設定
# Intent系
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True  # メッセージの内容を読み取るために必要
intents.voice_states = True # ボイスチャンネルの状態を取得するために必要（intentに追加という処理）

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# 稼働鯖（ギルドスラコマ登録用）
work_sv_ids = [
    int(os.getenv("WORK_SV_ID_TEST")), # type: ignore
    int(os.getenv("WORK_SV_ID_SAGYO")), # type: ignore
    int(os.getenv("WORK_SV_ID_SHINT")) # type: ignore
]

# log用
# log_chid = int(os.getenv("OIIA_LOG_TXID")) # type: ignore

# 音声ファイル
# oiia
mp3_oiia = [
    "./mp3/oiia.mp3",
    "./mp3/oiia_lefttoright.mp3",
    "./mp3/oiia_righttoleft.mp3"
]
# o
mp3_o = [
    "./mp3/o.mp3",
    "./mp3/o_left.mp3",
    "./mp3/o_right.mp3",
    "./mp3/o_r.mp3",
    "./mp3/o_r_left.mp3",
    "./mp3/o_r_right.mp3"
]
# i
mp3_i = [
    "./mp3/i.mp3",
    "./mp3/i_left.mp3",
    "./mp3/i_right.mp3",
    "./mp3/i_r.mp3",
    "./mp3/i_r_left.mp3",
    "./mp3/i_r_right.mp3"
]
# a
mp3_a = [
    "./mp3/a.mp3",
    "./mp3/a_r.mp3",
    "./mp3/a_r_left.mp3",
    "./mp3/a_r_right.mp3"
]


# ------------------------------
# ↑ 変数定義
# ↓ クラス・その他の関数
# ------------------------------


# 音声認識のためのクラス
class VoiceSink(voice_recv.AudioSink):
    def __init__(self, client):
        super().__init__()

        self.bot = client
        self.recognizer = sr.Recognizer()
        self.buffers = {} # 音声バッファをユーザ毎に用意
        self.last_spoken = {} # 最後に発言した時間←無音判定用
        # self.active_speakers = set()
        self.timeout = 1.0 # [s]沈黙で話し終わりとみなす→メモリ解放
        self.user_cache = {} # ???
        self.semaphore = asyncio.Semaphore(3) # 同時に音声解析を行う音声の最大数をここで定義 とりあえず3にしてみる

    # このSinkがOpusデータを欲しがっているかどうか
    # 音声認識には生のPCMが必要なので False を返す
    def wants_opus(self) -> bool:
        return False

    def write(self, user, data):
        # 発話者がいなければ抜け出す
        if user is None:
            return

        # 発言したユーザのid
        user_id = user.id
        # 話し時間
        now = time.time()

        # 初めて喋る場合 or タイムアウト後の新規発言
        if user_id not in self.buffers:
            self.buffers[user_id] = bytearray()
            self.user_cache[user_id] = user # タイムアウト時に使用するため保持しておく
            print(f"【録音開始】{user.display_name}")

        # ステレオ→モノラル変換
        mono_pcm = audioop.tomono(data.pcm, 2, 0.5, 0.5) # (データ，サンプル幅=2byte-16bit, 左音量比, 右音量比)

        # 音声データをバッファへ追加
        self.buffers[user_id].extend(mono_pcm)
        # 最後の話し時刻を更新
        self.last_spoken[user_id] = now

        # 7秒以上の長すぎる音声は強制的に区切って解析用process_audio()へ渡してメモリ節約
        if len(self.buffers[user_id]) > 48000 * 2 * 1 * 7: # (Hz, 2byte=16bit, 1ch=mono, 7sec)
            self.process_audio(user)

    def process_audio(self, user):
        audio_data = self.buffers.pop(user.id, None)
        self.last_spoken.pop(user.id, None) # 最後に喋ったデータも消す
        self.user_cache.pop(user.id, None) # キャッシュも消す
        if not audio_data:
            return

        # 解析用非同期関数へ渡す
        self.bot.loop.create_task(self.async_process_audio(user, audio_data))


    # 音声解析をする非同期処理
    async def async_process_audio(self, user, audio_data):
        async with self.semaphore: # セマフォ

            # DiscordのVCの音声(48kHz, Stereo)をAPIが読める形式に変換（io.BytesIOでメモリ上でWAVファイルを作成）
            with io.BytesIO() as wav_io:
                with wave.open(wav_io, 'wb') as wav_file:
                    wav_file.setnchannels(1) # 1: mono
                    wav_file.setsampwidth(2) # 2byte: 16bit
                    wav_file.setframerate(48000) # 48kHzサンプリング周波数
                    wav_file.writeframes(audio_data)
                # wave.openが閉じられた後、wav_ioの中身をバイナリとして取得←?
                wav_data = wav_io.getvalue()

            # API通信(時間がかかる処理)を別スレッドへ
            text = await asyncio.to_thread(self.run_recognition, wav_data) # sr.recognize_googleが同期関数よりto_threadを使うべき

            # 結果の判定
            if text:
                print(f"【認識】{user.display_name}: {text}")
                # ------------------------------
                # 音声認識完了 → キーワードの検出 & 音声再生関数へ
                # ------------------------------
                await oiia_say(user.guild, text)


    # 実際にAPIをたたく関数
    def run_recognition(self, wav_data):
        with sr.AudioFile(io.BytesIO(wav_data)) as source:
            audio = self.recognizer.record(source)
            # Google Web Speech API 呼び出し（無料枠）
            try:
                return self.recognizer.recognize_google(audio, language="ja-JP") # type: ignore
            # エラー処理
            except sr.UnknownValueError:
                return None
            except sr.RequestError as e:
                print(f"【エラー】APIリクエスト失敗: {e}")
                return None



    # ------------------------------
    # タイムアウト時
    # ------------------------------
    def check_timeouts(self):
        now = time.time()
        # タイムアウトした（喋り終わったとみなす）ユーザーを抽出
        dead_ids = [
            uid for uid, ltime in self.last_spoken.items()
            if now - ltime > self.timeout and uid in self.buffers
        ]

        for uid in dead_ids:
            user = self.user_cache.get(uid)
            if user:
                # 解析へ送る
                print(f"【沈黙検知】{user.display_name} の解析を開始")
                self.process_audio(user)
                # キャッシュから削除
                self.user_cache.pop(uid, None)
            else:
                # 発言したユーザがいなくても消すようにしておく 不要かも
                self.buffers.pop(uid, None)
                self.last_spoken.pop(uid, None)

        # 明示的に解放
        if dead_ids:
            gc.collect()

    def cleanup(self):
        pass


# クリーンアップのタスク
@tasks.loop(seconds=2.0)
async def cleanup_loop():
    # VCに接続中のすべてのギルドでSinkをチェック
    for vc in client.voice_clients:
        if isinstance(vc, voice_recv.VoiceRecvClient) and vc.sink:
            # VoiceSink インスタンスの check_timeouts を実行
            if hasattr(vc.sink, 'check_timeouts'):
                vc.sink.check_timeouts() # type: ignore


# キーワード検出 & 音声再生用関数
async def oiia_say(guild, text):
    voice_client = guild.voice_client
    if not voice_client or voice_client.is_playing():
        return

    mp3_path = None
    # キーワード検出と再生するmp3ファイルの決定（oを優先したいのでこの順）
    if "バイバイ" in text or "ばいばい" in text or "バイバーイ" in text:
        await voice_client.disconnect() # type: ignore
    elif "お" in text and "い" in text and "あ" in text:
        mp3_path = random.choice(mp3_oiia)
    elif "お" in text:
        mp3_path = random.choice(mp3_o)
    elif "い" in text:
        mp3_path = random.choice(mp3_i)
    elif "あ" in text:
        mp3_path = random.choice(mp3_a)
    else:
        print(f">>> キーワード未検出")
        return

    # 再生
    if mp3_path and os.path.exists(mp3_path):
        print(f">>> 再生開始: {mp3_path}")
        source = discord.FFmpegPCMAudio(mp3_path)
        voice_client.play(source)


# ------------------------------
# ↑ クラス・その他の関数
# ↓ イベントハンドラ
# ------------------------------


# テキストメッセージに対してリアクション
# @client.event
# async def on_message(message: discord.Message):
#     # Bot自身のメッセージ除外
#     if message.author == client.user:
#         return

#     # メッセージに"oiia"が含まれている場合
#     if "oiia" in message.content.lower() and ":oiia:" not in message.content.lower():
#         try:
#             await message.add_reaction('emoji_name:1472533450186559712')
#         except discord.Forbidden:
#             print("エラー: リアクションを追加する権限がありません")
#         except discord.HTTPException as e:
#             print(f"エラー: リアクションの追加に失敗: {e}")

#     # "huh"
#     if "huh" in message.content.lower():
#         try:
#             await message.add_reaction('emoji_name:1472533450186559712')
#         except discord.Forbidden:
#             print("エラー: リアクションを追加する権限がありません")
#         except discord.HTTPException as e:
#             print(f"エラー: リアクションの追加に失敗: {e}")


# ------------------------------
# ↑ イベントハンドラ
# ↓ スラッシュコマンド
# ------------------------------


# VC参加
@tree.command(name="oiiajoin", description="OIIABotをVCに呼ぶ")
async def oiiajoin(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=True) # 以降, interaction.followupを使う

    # コマンド実行ユーザがVCに参加していない場合
    if interaction.user.voice is None: # type: ignore
        await interaction.followup.send("```VC参加した状態で実行してください\nOIIABotがどのVCに入ればよいか迷っています```")
        return

    # VC情報を変数へ入れておく
    target_vc = interaction.user.voice.channel # type: ignore
    voice_client: voice_recv.VoiceRecvClient = interaction.guild.voice_client # type: ignore # 型を明示的に

    # Botが既にどこかのVCにいる場合
    if voice_client:
        # ユーザと同じVCにいた場合
        if voice_client.channel == target_vc:  # type: ignore
            # [何もしない]
            await interaction.followup.send("```既に同じVCにいます```")
        # ユーザと違うVCにいた場合
        else:
            # [移動]
            await voice_client.move_to(target_vc) # type: ignore ★Move_toでもVoiceRecvClientは継続されるのでこの書き方でok
            # 万一、移動後にVoiceRecvClientが外れていたら付け直す
            if not voice_client.is_listening():
                voice_client.listen(VoiceSink(client)) # clientも渡す
            await interaction.followup.send(f"```{target_vc.name} へ移動しました```") # type: ignore

    # BotがどこのVCにも参加していない場合
    else:
        # [参加]
        # await target_vc.connect() # type: ignore # 従来の接続法
        vc = await target_vc.connect(cls=voice_recv.VoiceRecvClient) # type: ignore
        # クラス
        vc.listen(VoiceSink(client)) # clientも渡す
        await interaction.followup.send(f"{target_vc.name} に参加しました") # type: ignore


# VC退場
@tree.command(name="oiialeft", description="OIIABotをVCから退場させる")
async def oiialeft(interaction: discord.Interaction):
    # コマンド受付を知らせる
    await interaction.response.defer(thinking=True, ephemeral=True) # 以降, interaction.followupを使う

    # BotがVCに参加していない場合
    if interaction.guild.voice_client == None: # type: ignore
        await interaction.followup.send("```OIIABotはVCに参加していません```")
        return
    # BotがVCに参加している場合
    else:
        # 退場処理
        # ここではguild.voice_client.channelから情報を取得する
        await interaction.followup.send(f"```{interaction.guild.voice_client.channel}から退出しました```") # type: ignore
        await interaction.guild.voice_client.disconnect() # type: ignore


# 音声再生コマンド
# @tree.command(name="oiiaplay", description="mp3を再生する")
# async def oiiaplay(interaction: discord.Interaction):
#     global a

#     await interaction.response.defer(thinking=True)

#     voice_client = interaction.guild.voice_client # type: ignore

#     if not voice_client:
#         await interaction.followup.send("```先にVCに参加させてください（/oiiajoin）```")
#         return

#     # 再生中の場合は一度止める
#     if voice_client.is_playing(): # type: ignore
#         voice_client.stop() # type: ignore


#     if not os.path.exists(a):
#         # await interaction.followup.send(f"```{a} が見つかりません```")
#         return

#     try:
#         # 再生処理
#         source = discord.FFmpegPCMAudio(a)
#         voice_client.play(source) # type: ignore
#         await interaction.followup.send(f"♪ {a} を再生します")
#     except Exception as e:
#         # await interaction.followup.send(f"再生中にエラーが発生しました: {e}")



# ------------------------------
# ↑ スラッシュコマンド
# ↓ discordボットの初期化処理
# ------------------------------


@client.event
async def on_ready():
    # 周期実行タスクの開始（例: 2秒ごとに掃除）
    cleanup_loop.start()

    # 稼働鯖へのギルド同期を順に行う
    for guild_id in work_sv_ids:
        try:
            guild = discord.Object(id=guild_id)
            await tree.sync(guild=guild)
            print(f"サーバ (ID: {guild_id}) にコマンド同期成功")
        except Exception as e:
            print(f"サーバ (ID: {guild_id}) への同期に失敗: {e}")

    # ツリーコマンド動機
    await tree.sync()
    print('OIIABot is ready...')

# discordボット起動のトリガー
client.run(os.getenv("DISCORD_BOT_TOKEN")) # type: ignore