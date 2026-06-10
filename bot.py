"""
╔══════════════════════════════════════════════════════════╗
║              🎵  Discord Music Bot  v2.1 (Render)  🎵              ║
║   All-in-one bot.py  •  discord.py 2.x  •  yt-dlp        ║
╚══════════════════════════════════════════════════════════╝

คำสั่ง: /play /pause /resume /skip /stop /volume
        /queue /nowplaying /loop /shuffle /clear /setup

ปุ่ม:  ⏮️ ⏸️ ⏭️ ⏹️  |  🔉 🔊 🔁 🔀 📋

✅ เมื่อเพลงจบ (คิวหมด) → ลบ embed อัตโนมัติ + แจ้งเตือน 8 วิ
✅ /stop หรือกด ⏹️  → ลบ embed ทันที
"""

from __future__ import annotations

import asyncio
import os
import random
import traceback
from collections import deque

import discord
import yt_dlp
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ╔══════════════════════════════════════════╗
# ║          YT-DLP / FFmpeg Options         ║
# ╚══════════════════════════════════════════╝
YTDL_OPTS_SEARCH = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    "socket_timeout": 8,       # timeout ต่อ connection
    "retries": 2,
    "skip_download": True,
    "geo_bypass": True,
}
YTDL_OPTS_PLAYLIST = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "extract_flat": True,
    "playlistend": 50,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
}
YTDL_OPTS_STREAM = {
    "format": "bestaudio/best",
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "socket_timeout": 8,
    "retries": 2,
    "geo_bypass": True,
}
FFMPEG_OPTS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn",
}



# ╔══════════════════════════════════════════╗
# ║    🌐  Health Server (Render keep-alive) ║
# ╚══════════════════════════════════════════╝
async def start_health_server():
    """
    Web server เล็กๆ ให้ Render รู้ว่า process ยังทำงานอยู่
    UptimeRobot จะ ping URL นี้ทุก 5 นาที ป้องกันบอทหลับ
    """
    async def handle(request):
        return web.Response(text="🎵 Music Bot is alive!", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"  🌐  Health server: http://0.0.0.0:{port}")


# ╔══════════════════════════════════════════╗
# ║            Helper Utilities              ║
# ╚══════════════════════════════════════════╝
def fmt_duration(seconds) -> str:
    if not seconds:
        return "🔴 LIVE"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def vol_bar(vol: int, max_v: int = 150, length: int = 10) -> str:
    filled = max(0, min(length, round(vol / max_v * length)))
    return "▰" * filled + "▱" * (length - filled)


def _is_playlist(url: str) -> bool:
    return url.startswith("http") and ("list=" in url or "/playlist" in url)


def _is_url(query: str) -> bool:
    return query.startswith(("http://", "https://"))


def _make_webpage_url(entry: dict) -> str:
    if entry.get("webpage_url", "").startswith("http"):
        return entry["webpage_url"]
    if entry.get("url", "").startswith("http"):
        return entry["url"]
    vid = entry.get("id", "")
    return f"https://www.youtube.com/watch?v={vid}"


async def _safe_delete(msg) -> None:
    """ลบ message โดยไม่ error ถ้าลบไม่ได้"""
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass


# ╔══════════════════════════════════════════╗
# ║        Music Control Buttons (View)      ║
# ╚══════════════════════════════════════════╝
class MusicView(discord.ui.View):
    def __init__(self, cog: "MusicCog", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @property
    def st(self) -> dict:
        return self.cog.get_guild_state(self.guild_id)

    # ── Row 0 ──────────────────────────────────
    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        if not st["history"]:
            return await interaction.response.send_message("❌ ไม่มีเพลงก่อนหน้า", ephemeral=True)
        vc = interaction.guild.voice_client
        prev = st["history"].pop()
        if st["current"]:
            st["queue"].appendleft(st["current"])
        st["queue"].appendleft(prev)
        st["current"] = None
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        await interaction.response.defer()

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, row=0)
    async def btn_pause_resume(self, interaction: discord.Interaction, btn: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ ไม่ได้อยู่ในช่องเสียง", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            btn.emoji = "▶️"
            btn.style = discord.ButtonStyle.success
        elif vc.is_paused():
            vc.resume()
            btn.emoji = "⏸️"
            btn.style = discord.ButtonStyle.primary
        else:
            return await interaction.response.send_message("❌ ไม่มีเพลงที่เล่นอยู่", ephemeral=True)
        embed = self.cog.build_np_embed(self.st["current"], self.st)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_skip(self, interaction: discord.Interaction, btn: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ ข้ามเพลงแล้ว", ephemeral=True)
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่เล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def btn_stop(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        st["queue"].clear()
        st["current"] = None
        st["loop"] = False
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
        # ✅ ลบ embed ทันที
        await _safe_delete(st.get("np_msg"))
        st["np_msg"] = None
        embed = discord.Embed(description="⏹️ หยุดเล่นและออกจากช่องเสียงแล้ว", color=0xE74C3C)
        await interaction.response.send_message(embed=embed)

    # ── Row 1 ──────────────────────────────────
    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def btn_vol_down(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        new_vol = max(0, st["volume"] - 10)
        st["volume"] = new_vol
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = new_vol / 100
        if st["current"]:
            embed = self.cog.build_np_embed(st["current"], st)
            return await interaction.response.edit_message(embed=embed, view=self)
        await interaction.response.send_message(f"🔉 ระดับเสียง: **{new_vol}%**", ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def btn_vol_up(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        new_vol = min(150, st["volume"] + 10)
        st["volume"] = new_vol
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = new_vol / 100
        if st["current"]:
            embed = self.cog.build_np_embed(st["current"], st)
            return await interaction.response.edit_message(embed=embed, view=self)
        await interaction.response.send_message(f"🔊 ระดับเสียง: **{new_vol}%**", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def btn_loop(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        st["loop"] = not st["loop"]
        btn.style = discord.ButtonStyle.success if st["loop"] else discord.ButtonStyle.secondary
        if st["current"]:
            embed = self.cog.build_np_embed(st["current"], st)
            return await interaction.response.edit_message(embed=embed, view=self)
        status = "✅ เปิด" if st["loop"] else "❌ ปิด"
        await interaction.response.send_message(f"🔁 วนซ้ำ: {status}", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def btn_shuffle(self, interaction: discord.Interaction, btn: discord.ui.Button):
        st = self.st
        if len(st["queue"]) < 2:
            return await interaction.response.send_message("❌ ต้องมีอย่างน้อย 2 เพลงในคิว", ephemeral=True)
        q = list(st["queue"])
        random.shuffle(q)
        st["queue"] = deque(q)
        await interaction.response.send_message(f"🔀 สุ่มคิว **{len(q)}** เพลงแล้ว", ephemeral=True)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def btn_queue(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message(
            embed=self.cog.build_queue_embed(self.st), ephemeral=True
        )


# ╔══════════════════════════════════════════╗
# ║              Music Cog                   ║
# ╚══════════════════════════════════════════╝
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, dict] = {}

    # ── State ────────────────────────────────
    def get_guild_state(self, guild_id: int) -> dict:
        if guild_id not in self._states:
            self._states[guild_id] = {
                "queue":   deque(),
                "current": None,
                "volume":  50,
                "loop":    False,
                "history": deque(maxlen=20),
                "np_msg":  None,
                "text_ch": None,
            }
        return self._states[guild_id]

    # ── Embed Builders ───────────────────────
    def build_np_embed(self, song: dict, state: dict) -> discord.Embed:
        vol     = state.get("volume", 50)
        looping = state.get("loop", False)
        q_len   = len(state.get("queue", []))

        embed = discord.Embed(
            title="🎵 กำลังเล่นเพลง",
            description=f"### [{song['title']}]({song.get('webpage_url', '#')})",
            color=0x1DB954,
        )
        if thumb := song.get("thumbnail"):
            embed.set_thumbnail(url=thumb)

        embed.add_field(name="⏱️ ความยาว",      value=f"`{fmt_duration(song.get('duration'))}`", inline=True)
        embed.add_field(name="🎤 ช่อง",          value=(song.get("uploader") or "ไม่ทราบ")[:40],  inline=True)
        embed.add_field(name="🔁 วนซ้ำ",         value="✅ เปิด" if looping else "❌ ปิด",          inline=True)
        embed.add_field(name=f"🔊 เสียง {vol}%", value=f"`{vol_bar(vol)}`",                        inline=True)
        embed.add_field(name="📋 คิว",            value=f"**{q_len}** เพลงถัดไป",                   inline=True)
        if vc := song.get("view_count"):
            embed.add_field(name="👁️ ยอดชม",     value=f"{vc:,}",                                  inline=True)

        embed.set_footer(text="🎧 ควบคุมด้วยปุ่มด้านล่าง  •  Music Bot v2.0")
        return embed

    def build_queue_embed(self, state: dict) -> discord.Embed:
        current = state.get("current")
        queue: deque = state.get("queue", deque())
        embed = discord.Embed(title="📋 คิวเพลงทั้งหมด", color=0x9B59B6)
        if current:
            embed.add_field(
                name="▶️ กำลังเล่น",
                value=f"**{current['title']}**\n`{fmt_duration(current.get('duration'))}`",
                inline=False,
            )
            if thumb := current.get("thumbnail"):
                embed.set_thumbnail(url=thumb)
        if queue:
            lines = [
                f"`{i+1:2d}.` **{s['title']}** — `{fmt_duration(s.get('duration'))}`"
                for i, s in enumerate(list(queue)[:15])
            ]
            if len(queue) > 15:
                lines.append(f"_...และอีก **{len(queue)-15}** เพลง_")
            embed.add_field(name=f"🎶 ถัดไป ({len(queue)} เพลง)", value="\n".join(lines), inline=False)
        else:
            embed.description = "ไม่มีเพลงในคิว — ใช้ `/play` เพื่อเพิ่ม! 🎵"
        embed.set_footer(
            text=f"เสียง: {state.get('volume', 50)}%  •  วนซ้ำ: {'✅' if state.get('loop') else '❌'}"
        )
        return embed

    # ── Song Fetching ────────────────────────
    async def _fetch_songs(self, query: str) -> list[dict]:
        loop = asyncio.get_event_loop()
        if _is_playlist(query):
            dl  = yt_dlp.YoutubeDL(YTDL_OPTS_PLAYLIST)
            raw = await loop.run_in_executor(None, lambda: dl.extract_info(query, download=False))
            entries = raw.get("entries") or []
        elif _is_url(query):
            dl  = yt_dlp.YoutubeDL(YTDL_OPTS_SEARCH)
            raw = await loop.run_in_executor(None, lambda: dl.extract_info(query, download=False))
            entries = [raw]
        else:
            dl  = yt_dlp.YoutubeDL(YTDL_OPTS_SEARCH)
            raw = await loop.run_in_executor(
                None, lambda: dl.extract_info(f"ytsearch1:{query}", download=False)
            )
            entries = (raw.get("entries") or [raw])[:1]

        return [
            {
                "title":       e.get("title") or "Unknown",
                "webpage_url": _make_webpage_url(e),
                "thumbnail":   e.get("thumbnail"),
                "duration":    e.get("duration"),
                "uploader":    e.get("uploader") or e.get("channel") or "Unknown",
                "view_count":  e.get("view_count"),
            }
            for e in entries if e
        ]

    async def _get_stream_url(self, webpage_url: str) -> str:
        loop = asyncio.get_event_loop()
        dl   = yt_dlp.YoutubeDL(YTDL_OPTS_STREAM)
        data = await loop.run_in_executor(None, lambda: dl.extract_info(webpage_url, download=False))
        if "entries" in data:
            data = data["entries"][0]
        return data["url"]

    # ── Playback Engine ──────────────────────
    async def play_next(self, guild_id: int) -> None:
        st      = self.get_guild_state(guild_id)
        guild   = self.bot.get_guild(guild_id)
        channel = st.get("text_ch")
        vc      = guild.voice_client if guild else None

        if not vc or not guild:
            return

        # วนซ้ำ: ดัน current กลับหัวคิว
        if st["loop"] and st["current"]:
            st["queue"].appendleft(dict(st["current"]))

        # ╔══════════════════════════════════════╗
        # ║  คิวหมด: ลบ embed + แจ้งเตือน 8 วิ  ║
        # ╚══════════════════════════════════════╝
        if not st["queue"]:
            st["current"] = None
            # ✅ ลบ now-playing embed
            await _safe_delete(st.get("np_msg"))
            st["np_msg"] = None

            if channel:
                embed = discord.Embed(
                    description="✅ เพลงในคิวหมดแล้ว — ใช้ `/play` เพื่อเพิ่มเพลงใหม่ 🎵",
                    color=0x95A5A6,
                )
                notice = await channel.send(embed=embed)
                # ลบแจ้งเตือนหลัง 8 วินาที
                await asyncio.sleep(8)
                await _safe_delete(notice)
            return

        # ╔══════════════════════════════════════╗
        # ║          เล่นเพลงถัดไป               ║
        # ╚══════════════════════════════════════╝
        song = st["queue"].popleft()
        if st["current"]:
            st["history"].append(dict(st["current"]))
        st["current"] = song

        try:
            try:
                stream_url = await asyncio.wait_for(
                    self._get_stream_url(song["webpage_url"]), timeout=25.0
                )
            except asyncio.TimeoutError:
                if channel:
                    await channel.send(f"❌ โหลดไม่ได้ภายใน 25 วิ ข้ามเพลง **{song['title']}**")
                asyncio.run_coroutine_threadsafe(self.play_next(guild_id), self.bot.loop)
                return
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTS),
                volume=st["volume"] / 100,
            )

            def _after(err):
                if err:
                    print(f"[Music] Playback error: {err}")
                asyncio.run_coroutine_threadsafe(self.play_next(guild_id), self.bot.loop)

            vc.play(source, after=_after)

            # ลบ now-playing เก่าก่อนส่งอันใหม่
            await _safe_delete(st.get("np_msg"))

            embed = self.build_np_embed(song, st)
            view  = MusicView(self, guild_id)
            if channel:
                msg = await channel.send(embed=embed, view=view)
                st["np_msg"] = msg

        except Exception as exc:
            print(f"[Music] Error: {exc}")
            traceback.print_exc()
            if channel:
                await channel.send(
                    f"❌ เกิดข้อผิดพลาดขณะเล่น **{song['title']}**: `{str(exc)[:120]}`"
                )
            asyncio.run_coroutine_threadsafe(self.play_next(guild_id), self.bot.loop)

    # ╔════════════════════════════════════════╗
    # ║            Slash Commands              ║
    # ╚════════════════════════════════════════╝

    @app_commands.command(name="play", description="🎵 เล่นเพลงจาก YouTube หรือค้นหาชื่อเพลง")
    @app_commands.describe(query="ชื่อเพลง, URL วิดีโอ หรือ URL Playlist")
    async def cmd_play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message(
                "❌ กรุณาเข้าร่วมช่องเสียงก่อน!", ephemeral=True
            )
        await interaction.response.defer()

        vc_ch = interaction.user.voice.channel
        vc    = interaction.guild.voice_client
        if vc is None:
            vc = await vc_ch.connect()
        elif vc.channel != vc_ch:
            await vc.move_to(vc_ch)

        st = self.get_guild_state(interaction.guild_id)
        st["text_ch"] = interaction.channel

        try:
            songs = await asyncio.wait_for(self._fetch_songs(query), timeout=25.0)
        except asyncio.TimeoutError:
            return await interaction.followup.send("❌ หมดเวลาค้นหา กรุณาลองใหม่")
        except Exception as e:
            return await interaction.followup.send(f"❌ ค้นหาไม่สำเร็จ: `{e}`")

        if not songs:
            return await interaction.followup.send("❌ ไม่พบเพลงที่ค้นหา")

        for s in songs:
            st["queue"].append(s)

        if not vc.is_playing() and not vc.is_paused():
            await interaction.followup.send("⏳ กำลังโหลดเพลง...", ephemeral=True)
            await self.play_next(interaction.guild_id)
        else:
            if len(songs) == 1:
                s = songs[0]
                embed = discord.Embed(
                    title="➕ เพิ่มเพลงในคิว",
                    description=f"**{s['title']}**",
                    color=0x3498DB,
                )
                embed.add_field(name="⏱️ ความยาว", value=fmt_duration(s.get("duration")), inline=True)
                embed.add_field(name="📋 ตำแหน่ง", value=f"# {len(st['queue'])}", inline=True)
                if t := s.get("thumbnail"):
                    embed.set_thumbnail(url=t)
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="➕ เพิ่ม Playlist ในคิว",
                        description=f"เพิ่ม **{len(songs)}** เพลงจาก playlist",
                        color=0x3498DB,
                    )
                )

    @app_commands.command(name="pause", description="⏸️ หยุดเพลงชั่วคราว")
    async def cmd_pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message(
                embed=discord.Embed(description="⏸️ หยุดเพลงชั่วคราว", color=0xF39C12)
            )
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่น", ephemeral=True)

    @app_commands.command(name="resume", description="▶️ เล่นเพลงต่อ")
    async def cmd_resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message(
                embed=discord.Embed(description="▶️ เล่นเพลงต่อแล้ว", color=0x2ECC71)
            )
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่หยุดอยู่", ephemeral=True)

    @app_commands.command(name="skip", description="⏭️ ข้ามเพลงปัจจุบัน")
    async def cmd_skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(
                embed=discord.Embed(description="⏭️ ข้ามเพลงแล้ว", color=0x3498DB)
            )
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่เล่นอยู่", ephemeral=True)

    @app_commands.command(name="stop", description="⏹️ หยุดเล่นและออกจากช่องเสียง")
    async def cmd_stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        st = self.get_guild_state(interaction.guild_id)
        st["queue"].clear()
        st["current"] = None
        st["loop"]    = False
        # ✅ ลบ embed ทันที
        await _safe_delete(st.get("np_msg"))
        st["np_msg"] = None
        if vc:
            await vc.disconnect()
        await interaction.response.send_message(
            embed=discord.Embed(description="⏹️ หยุดเล่นและออกจากช่องเสียงแล้ว", color=0xE74C3C)
        )

    @app_commands.command(name="volume", description="🔊 ปรับระดับเสียง (0–150)")
    @app_commands.describe(level="ระดับเสียง 0–150  (ค่าเริ่มต้น: 50)")
    async def cmd_volume(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 150]
    ):
        vc = interaction.guild.voice_client
        st = self.get_guild_state(interaction.guild_id)
        st["volume"] = level
        if vc and vc.source:
            vc.source.volume = level / 100
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔊 ปรับระดับเสียง",
                description=f"`{vol_bar(level)}` **{level}%**",
                color=0xF1C40F,
            )
        )

    @app_commands.command(name="queue", description="📋 ดูรายการเพลงในคิว")
    async def cmd_queue(self, interaction: discord.Interaction):
        st = self.get_guild_state(interaction.guild_id)
        await interaction.response.send_message(embed=self.build_queue_embed(st))

    @app_commands.command(name="nowplaying", description="🎵 ดูเพลงที่กำลังเล่นอยู่")
    async def cmd_nowplaying(self, interaction: discord.Interaction):
        st = self.get_guild_state(interaction.guild_id)
        if not st.get("current"):
            return await interaction.response.send_message("❌ ไม่มีเพลงที่เล่นอยู่", ephemeral=True)
        embed = self.build_np_embed(st["current"], st)
        view  = MusicView(self, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="loop", description="🔁 เปิด/ปิด การวนซ้ำเพลง")
    async def cmd_loop(self, interaction: discord.Interaction):
        st = self.get_guild_state(interaction.guild_id)
        st["loop"] = not st["loop"]
        status = "✅ เปิด" if st["loop"] else "❌ ปิด"
        color  = 0x2ECC71 if st["loop"] else 0xE74C3C
        await interaction.response.send_message(
            embed=discord.Embed(title=f"🔁 วนซ้ำ: {status}", color=color)
        )

    @app_commands.command(name="shuffle", description="🔀 สุ่มลำดับเพลงในคิว")
    async def cmd_shuffle(self, interaction: discord.Interaction):
        st = self.get_guild_state(interaction.guild_id)
        q  = list(st["queue"])
        if len(q) < 2:
            return await interaction.response.send_message(
                "❌ ต้องมีอย่างน้อย 2 เพลงในคิว", ephemeral=True
            )
        random.shuffle(q)
        st["queue"] = deque(q)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔀 สุ่มคิวแล้ว",
                description=f"สุ่ม **{len(q)}** เพลง",
                color=0x1ABC9C,
            )
        )

    @app_commands.command(name="clear", description="🗑️ ล้างคิวเพลงทั้งหมด")
    async def cmd_clear(self, interaction: discord.Interaction):
        st    = self.get_guild_state(interaction.guild_id)
        count = len(st["queue"])
        st["queue"].clear()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑️ ล้างคิวแล้ว",
                description=f"ลบ **{count}** เพลงออกจากคิว",
                color=0xE67E22,
            )
        )

    @app_commands.command(name="setup", description="⚙️ วิธีติดตั้งและตั้งค่าบอท")
    async def cmd_setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ วิธีติดตั้ง Music Bot",
            description="ทำตามขั้นตอนด้านล่างเพื่อตั้งค่าบอท",
            color=0x5865F2,
        )
        embed.add_field(name="📦 1. ติดตั้ง Dependencies", value="```bash\npip install -r requirements.txt\n```", inline=False)
        embed.add_field(name="🎬 2. ติดตั้ง FFmpeg",        value="**Linux:** `sudo apt install ffmpeg`\n**Mac:** `brew install ffmpeg`\n**Windows:** [ffmpeg.org](https://ffmpeg.org)", inline=False)
        embed.add_field(name="🔑 3. ตั้งค่า Token",         value="```bash\ncp .env.example .env\n# ใส่ DISCORD_TOKEN=...\n```", inline=False)
        embed.add_field(name="🚀 4. รันบอท",                value="```bash\npython bot.py\n```", inline=False)
        embed.add_field(name="🎵 คำสั่งทั้งหมด",            value="`/play` `/pause` `/resume` `/skip` `/stop`\n`/volume` `/queue` `/nowplaying` `/loop` `/shuffle` `/clear`", inline=False)
        embed.set_footer(text="🎵 Music Bot v2.0  •  discord.py 2.x + yt-dlp")
        await interaction.response.send_message(embed=embed)


# ╔══════════════════════════════════════════╗
# ║              Bot Setup                   ║
# ╚══════════════════════════════════════════╝
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states    = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"\n{'═' * 46}")
    print(f"  🎵  {bot.user.name}  พร้อมใช้งานแล้ว!")
    print(f"  🆔  Bot ID  : {bot.user.id}")
    print(f"  🌐  Servers : {len(bot.guilds)}")
    print(f"{'═' * 46}\n")
    try:
        synced = await bot.tree.sync()
        print(f"  ✅  Synced {len(synced)} slash command(s)\n")
    except Exception as e:
        print(f"  ❌  Sync error: {e}\n")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="🎵 /play")
    )


@bot.event
async def on_voice_state_update(member, before, after):
    """ออกจากช่องเสียงเมื่อไม่มีคนอยู่ (30 วินาที)"""
    if member.bot:
        return
    vc = member.guild.voice_client
    if vc and len(vc.channel.members) == 1:
        await asyncio.sleep(30)
        if vc.is_connected() and len(vc.channel.members) == 1:
            await vc.disconnect()


async def main():
    async with bot:
        await bot.add_cog(MusicCog(bot))
        await start_health_server()   # ← Web server สำหรับ Render
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("❌ ไม่พบ DISCORD_TOKEN ใน .env")
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
