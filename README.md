# Full-duplex LiveKit voice agent

This agent listens while it speaks, so the user can interrupt an answer naturally. LiveKit calls this behavior **interruptions** or **barge-in** rather than exposing a separate `full_duplex` switch.

The session in `agent.py` uses:

- concurrent room audio input and output;
- streaming, word-aligned speech recognition;
- voice activity and turn detection;
- adaptive interruption detection, which separates real interruptions from short backchannels such as “uh-huh”;
- false-interruption recovery, so playback can resume after a cough or brief noise; and
- AI-coustics input enhancement.

## Run locally

Install the locked dependencies and start the console client:

```powershell
uv sync
uv run python agent.py console
```

For a LiveKit room in development mode, run:

```powershell
uv run python agent.py dev
```

The local `.env` must contain the LiveKit credentials required by LiveKit Inference. It is ignored by Git and must not be committed.

## Test barge-in

1. Ask the agent for a long explanation.
2. While it is speaking, say: “Stop. Give me only the short answer.”
3. The current audio should stop, and the next response should follow the interruption.
4. During another answer, say “uh-huh.” The adaptive detector should normally treat that as a backchannel instead of cancelling the answer.

`AEC_WARMUP_SECONDS` in `agent.py` protects the first half-second of the first reply from speaker echo. If the agent interrupts itself when using laptop speakers, increase it toward `3.0`. Use `None` for immediate greeting interruption only when headphones or reliable client-side WebRTC echo cancellation are available. Input noise cancellation does not replace echo cancellation in the browser or phone client.

See LiveKit's [turn handling guide](https://docs.livekit.io/agents/logic/turns/), [adaptive interruption guide](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/), and [turn-handling options](https://docs.livekit.io/reference/agents/turn-handling-options/).
