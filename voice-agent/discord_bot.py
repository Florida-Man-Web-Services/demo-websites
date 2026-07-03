"""Put the sales agent in a Discord voice call.

The bot joins your voice channel and role-plays a sales call: it listens to
whoever speaks, transcribes with Whisper (DeepInfra), replies with Claude,
and speaks through Sesame CSM. Great for demoing/testing the agent with real
conversation timing, no phone required.

Setup (see also shell.nix for the ffmpeg/opus system deps on NixOS):
  1. discord.com/developers -> New Application -> Bot:
       - copy the bot token into .env as DISCORD_BOT_TOKEN
       - enable the "Message Content Intent"
  2. Invite it to your server: OAuth2 URL with scope "bot" and permissions
     Connect + Speak + Send Messages (permissions integer 3147776).
  3. pip install -r requirements-discord.txt
  4. nix-shell --run '.venv/bin/python discord_bot.py'   (from voice-agent/)

In Discord (while you're in a voice channel):
  !call [slug]      start a simulated outbound call (default: ole-barn)
  !inbound [slug]   simulate the business calling back
  !hangup           end the call and disconnect
Then just talk — pause ~1 second and the agent answers.
"""

import asyncio
import io
import logging
import os
import time
import wave

import discord
import httpx
from discord.ext import commands, voice_recv

import agent as agent_mod
import config
import tts
from agent import CallState, run_turn
from businesses import by_slug

log = logging.getLogger("voice-agent.discord")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

WHISPER_URL = os.getenv(
    "WHISPER_URL", "https://api.deepinfra.com/v1/inference/openai/whisper-large-v3-turbo"
)
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

SILENCE_SECS = 1.0        # pause length that ends your turn
MIN_SPEECH_SECS = 0.4     # ignore blips shorter than this
PCM_RATE, PCM_CHANNELS, PCM_WIDTH = 48000, 2, 2  # what discord hands us

# Never send real SMS from the Discord simulator.
agent_mod._send_sms = lambda state, to: (
    log.info("[SIM] would text demo link to %s", to or state.caller_number),
    "SMS with the demo link sent.",
)[1]


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(PCM_CHANNELS)
        w.setsampwidth(PCM_WIDTH)
        w.setframerate(PCM_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def transcribe(pcm: bytes) -> str:
    resp = httpx.post(
        WHISPER_URL,
        headers={"Authorization": f"bearer {config.DEEPINFRA_API_KEY}"},
        files={"audio": ("speech.wav", pcm_to_wav(pcm), "audio/wav")},
        timeout=60,
    )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


class Call:
    """One active simulated call in one voice channel."""

    def __init__(self, state: CallState, text_channel):
        self.state = state
        self.text_channel = text_channel
        self.buffers: dict[int, bytearray] = {}
        self.last_packet: dict[int, float] = {}
        self.busy = asyncio.Lock()  # one turn at a time


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
calls: dict[int, Call] = {}  # guild id -> Call


def _load_opus():
    if discord.opus.is_loaded():
        return
    path = os.getenv("DISCORD_OPUS_LIB")  # set by shell.nix on NixOS
    if path:
        discord.opus.load_opus(path)
    else:
        discord.opus._load_default()


async def speak(vc, call: Call, text: str):
    await call.text_channel.send(f"🗣️ **Agent:** {text}")
    key = await asyncio.to_thread(tts.synthesize, text)
    done = asyncio.Event()
    vc.play(
        discord.FFmpegPCMAudio(str(tts.audio_path(key))),
        after=lambda err: bot.loop.call_soon_threadsafe(done.set),
    )
    await done.wait()


async def run_agent_turn(vc, call: Call, heard: str | None):
    async with call.busy:
        if heard:
            await call.text_channel.send(f"👤 *Heard:* {heard}")
        reply = await asyncio.to_thread(run_turn, call.state, heard)
        await speak(vc, call, reply)
        if call.state.ended:
            await call.text_channel.send("📞 *Agent hung up.*")
            calls.pop(call.text_channel.guild.id, None)
            await vc.disconnect()


async def silence_watcher(vc, call: Call):
    """End-of-turn detection: process a speaker's buffer after ~1s of silence."""
    bytes_per_sec = PCM_RATE * PCM_CHANNELS * PCM_WIDTH
    while vc.is_connected() and calls.get(call.text_channel.guild.id) is call:
        await asyncio.sleep(0.25)
        if call.busy.locked() or vc.is_playing():
            continue
        now = time.monotonic()
        for uid, buf in list(call.buffers.items()):
            if not buf or now - call.last_packet.get(uid, now) < SILENCE_SECS:
                continue
            pcm = bytes(buf)
            buf.clear()
            if len(pcm) < MIN_SPEECH_SECS * bytes_per_sec:
                continue
            try:
                heard = await asyncio.to_thread(transcribe, pcm)
            except Exception as e:
                await call.text_channel.send(f"⚠️ transcription failed: {e}")
                continue
            if heard:
                await run_agent_turn(vc, call, heard)


async def start_call(ctx, slug: str, direction: str):
    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.send("Join a voice channel first, then run the command again.")
        return
    business = by_slug(slug)
    if business is None:
        await ctx.send(f"Unknown business slug `{slug}` — see `outreach-data.csv`.")
        return
    if ctx.guild.id in calls:
        await ctx.send("A call is already active here — `!hangup` first.")
        return

    vc = await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
    state = CallState(
        call_sid=f"TEST-discord-{business.slug}",
        business=business,
        direction=direction,
        caller_number="discord",
    )
    call = Call(state, ctx.channel)
    calls[ctx.guild.id] = call

    def on_voice(user, data: voice_recv.VoiceData):
        if user is None or user.bot:
            return
        call.buffers.setdefault(user.id, bytearray()).extend(data.pcm)
        call.last_packet[user.id] = time.monotonic()

    vc.listen(voice_recv.BasicSink(on_voice))
    asyncio.create_task(silence_watcher(vc, call))

    await ctx.send(
        f"📞 **{direction.title()} call with {business.name}** ({business.category}) — "
        f"you're the owner. Speak naturally; pause ~1s to let the agent answer.\n"
        f"Demo: {business.demo_url}"
    )
    await run_agent_turn(vc, call, None)


@bot.command(name="call")
async def call_cmd(ctx, slug: str = "ole-barn"):
    await start_call(ctx, slug, "outbound")


@bot.command(name="inbound")
async def inbound_cmd(ctx, slug: str = "ole-barn"):
    await start_call(ctx, slug, "inbound")


@bot.command(name="hangup")
async def hangup_cmd(ctx):
    calls.pop(ctx.guild.id, None)
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("📞 Call ended.")


@bot.event
async def on_ready():
    log.info("logged in as %s — invite it and type !call in a text channel", bot.user)


if __name__ == "__main__":
    config.require("ANTHROPIC_API_KEY", "DEEPINFRA_API_KEY")
    if not DISCORD_BOT_TOKEN:
        raise SystemExit(
            "Set DISCORD_BOT_TOKEN in voice-agent/.env "
            "(discord.com/developers -> your app -> Bot -> Reset Token)"
        )
    _load_opus()
    bot.run(DISCORD_BOT_TOKEN)
