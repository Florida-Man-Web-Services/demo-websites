# Voiceover scripts for silent Veo cuts

Record or TTS these; lay under the matching mp4. Keep VO dry and local.

## v1-group-chat-a411 (8s)

```
[0.0–2.5]  The group chat asked what's going on. Again.
[2.5–5.0]  Nobody had a plan.
[5.0–7.5]  A411 here.
[7.5–8.0]  (beat)
```

## v2-events-categories (8s)

```
[0.0–2.0]  Too many options.
[2.0–5.5]  AI 411 asks what you're into — then category counts when the list is long.
[5.5–8.0]  You pick. It drills in.
```

## v3-free-demo-shop (8s)

```
[0.0–2.5]  Great shop. Website? Not so much.
[2.5–6.0]  Request a free demo callback from AI 411.
[6.0–8.0]  You ask. We call back.
```

## v4-a411-mnemonic (8s)

```
[0.0–3.0]  (ambient only)
[3.0–5.5]  A411 here.
[5.5–8.0]  What's going on?
```

## Mix tip (ffmpeg when available)

```bash
# example once you have vo1.wav
ffmpeg -i v1-group-chat-a411.mp4 -i vo1.wav -c:v copy -c:a aac -shortest v1-with-vo.mp4
```
