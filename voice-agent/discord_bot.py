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

import array
import asyncio
import io
import logging
import os
import re
import time
import wave

import discord
import httpx
from discord.ext import commands, voice_recv
from discord.ext.voice_recv import opus as vr_opus

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

SILENCE_SECS = float(os.getenv("TURN_SILENCE_SECS", "1.0"))  # pause that ends your turn
MIN_SPEECH_SECS = 0.5     # ignore blips shorter than this
MAX_SPEECH_SECS = 30      # cap a single utterance
PCM_RATE, PCM_CHANNELS, PCM_WIDTH = 48000, 2, 2  # what discord hands us
WHISPER_RATE = 16000      # downsample before upload: 6x smaller, same accuracy

# A single malformed voice packet raises OpusError inside voice_recv's router
# thread, killing it — after which the bot is permanently deaf ("it just
# stopped responding"). Drop the bad packet instead of dying.
_orig_pop_data = vr_opus.PacketDecoder.pop_data


def _safe_pop_data(self, **kwargs):
    try:
        return _orig_pop_data(self, **kwargs)
    except Exception as e:
        log.warning("dropped undecodable voice packet: %s", e)
        return None


vr_opus.PacketDecoder.pop_data = _safe_pop_data

# Never send real SMS from the Discord simulator.
agent_mod._send_sms = lambda state, to: (
    log.info("[SIM] would text demo link to %s", to or state.caller_number),
    "SMS with the demo link sent.",
)[1]


def pcm_to_whisper_wav(pcm: bytes) -> bytes:
    """48kHz stereo int16 -> 16kHz mono WAV (Whisper resamples to 16k anyway)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    mono_16k = samples[0::2][::3]  # left channel, every 3rd sample
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(PCM_WIDTH)
        w.setframerate(WHISPER_RATE)
        w.writeframes(mono_16k.tobytes())
    return buf.getvalue()


def transcribe(pcm: bytes) -> str:
    resp = httpx.post(
        WHISPER_URL,
        headers={"Authorization": f"bearer {config.DEEPINFRA_API_KEY}"},
        files={"audio": ("speech.wav", pcm_to_whisper_wav(pcm), "audio/wav")},
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
        self.mic_open = False  # half-duplex: only capture while the agent listens

    def clear_audio(self):
        self.buffers.clear()
        self.last_packet.clear()


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


async def _play_file(vc, path) -> None:
    done = asyncio.Event()
    vc.play(
        discord.FFmpegPCMAudio(str(path)),
        after=lambda err: bot.loop.call_soon_threadsafe(done.set),
    )
    await done.wait()


async def run_agent_turn(vc, call: Call, heard: str | None):
    """Fully pipelined turn: Claude streams sentences -> each one goes to TTS
    the moment it completes -> finished audio plays in order. Sentence one is
    usually playing while Claude is still writing sentence three.
    """
    async with call.busy:
        call.mic_open = False  # stop capturing while we think and talk
        if heard:
            await call.text_channel.send(f"👤 *Heard:* {heard}")

        loop = asyncio.get_running_loop()
        synth_queue: asyncio.Queue = asyncio.Queue()  # (sentence, synth_task) in order

        def on_sentence(sentence: str):  # called from run_turn's worker thread
            def enqueue():
                synth_queue.put_nowait(
                    (sentence, asyncio.create_task(asyncio.to_thread(tts.synthesize, sentence)))
                )
            loop.call_soon_threadsafe(enqueue)

        async def produce():
            try:
                return await asyncio.to_thread(run_turn, call.state, heard, on_sentence)
            finally:
                loop.call_soon_threadsafe(synth_queue.put_nowait, None)

        producer = asyncio.create_task(produce())

        while True:
            item = await synth_queue.get()
            if item is None:
                break
            sentence, synth_task = item
            try:
                key = await synth_task
                await _play_file(vc, tts.audio_path(key))
            except Exception as e:
                log.warning("TTS failed for %r: %s", sentence, e)
                await call.text_channel.send(f"🗣️ *(voice failed)* {sentence}")

        reply = await producer
        await call.text_channel.send(f"🗣️ **Agent:** {reply}")

        if call.state.ended:
            await call.text_channel.send("📞 *Agent hung up.*")
            calls.pop(call.text_channel.guild.id, None)
            await vc.disconnect()
            return
        # Fresh start for the caller's turn: drop anything captured while the
        # agent was speaking (echo, backchannel, cross-talk), then open the mic.
        await asyncio.sleep(0.3)
        call.clear_audio()
        call.mic_open = True


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
            call.clear_audio()  # one turn per pause, even with multiple speakers
            if len(pcm) < MIN_SPEECH_SECS * bytes_per_sec:
                break
            try:
                heard = await asyncio.to_thread(transcribe, pcm)
            except Exception as e:
                await call.text_channel.send(f"⚠️ transcription failed: {e}")
                break
            if heard:
                await run_agent_turn(vc, call, heard)
            break


async def start_call(ctx, slug: str, direction: str):
    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.send("Join a voice channel first, then run the command again.")
        return
    business = by_slug(slug)
    if business is None:
        import difflib

        from businesses import all_businesses

        close = difflib.get_close_matches(
            slug, [b.slug for b in all_businesses()], n=5, cutoff=0.4
        )
        hint = ("Did you mean: " + ", ".join(f"`{s}`" for s in close)) if close else \
            "Slugs are the generated-sites filenames, e.g. `hayes-jewelry-ltd`."
        await ctx.send(f"Unknown business slug `{slug}`. {hint}")
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
        if not call.mic_open:  # half-duplex: deaf while the agent thinks/talks
            return
        if user.id not in call.buffers:
            log.info("hearing %s", user)
        buf = call.buffers.setdefault(user.id, bytearray())
        if len(buf) < MAX_SPEECH_SECS * PCM_RATE * PCM_CHANNELS * PCM_WIDTH:
            buf.extend(data.pcm)
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


async def tts_keepalive():
    """Keep the Sesame model loaded and the stock openers cached."""
    await asyncio.to_thread(tts.prewarm_phrases, agent_mod.OPENERS)
    log.info("opener phrases cached")
    while True:
        try:
            await asyncio.to_thread(tts.warm)
        except Exception as e:
            log.warning("tts keepalive failed: %s", e)
        await asyncio.sleep(240)


@bot.event
async def on_ready():
    log.info("logged in as %s — invite it and type !call in a text channel", bot.user)
    if not getattr(bot, "_keepalive_started", False):
        bot._keepalive_started = True
        asyncio.create_task(tts_keepalive())


if __name__ == "__main__":
    config.require("ANTHROPIC_API_KEY", "DEEPINFRA_API_KEY")
    if not DISCORD_BOT_TOKEN:
        raise SystemExit(
            "Set DISCORD_BOT_TOKEN in voice-agent/.env "
            "(discord.com/developers -> your app -> Bot -> Reset Token)"
        )
    _load_opus()
    bot.run(DISCORD_BOT_TOKEN)
