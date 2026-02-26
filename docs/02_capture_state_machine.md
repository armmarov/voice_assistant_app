# Microphone Capture — State Machine

`MicrophoneCapture` in `src/capture.py` runs a continuous loop with two states.

```mermaid
stateDiagram-v2
    [*] --> IDLE : start()

    IDLE --> IDLE : frame read\nmuted → discard frame
    IDLE --> IDLE : frame read\nPorcupine → no match
    IDLE --> LISTENING : Porcupine detects\nwake word\non_wake_word() fired\ntimeout_left = 10s

    LISTENING --> IDLE : muted\n→ discard, reset
    LISTENING --> LISTENING : is_speech = True\n→ silence_count = 0\n→ timeout reset to 10s
    LISTENING --> LISTENING : is_speech = False\n→ silence_count++\n< silence_limit (1200ms)
    LISTENING --> LISTENING : resume flag set\n(after ack playback)\n→ timeout refreshed
    LISTENING --> IDLE : silence_count\n>= silence_limit (1200ms)\nAND voiced < 2000ms\n→ utterance too short
    LISTENING --> IDLE : silence_count\n>= silence_limit (1200ms)\nAND voiced >= 2000ms\n→ on_utterance(wav) fired
    LISTENING --> IDLE : timeout_left reaches 0\n(no speech for 10s)

    [*] --> IDLE : stop()
```

## Frame Size Reconciliation

Porcupine and WebRTC VAD require different frame sizes. Both are served from a single read:

```mermaid
flowchart LR
    READ["stream.read(512 samples)\n= 1024 bytes"]
    PORC["Porcupine.process()\n512 int16 samples\n= full 1024 bytes"]
    VAD["VAD.is_speech()\n480 samples\n= first 960 bytes only"]

    READ --> PORC
    READ -->|"frame[:960]"| VAD
```

## Key Timing Values

| Parameter | Value | Config key |
|---|---|---|
| VAD frame size | 30 ms / 480 samples | `MIC_CHUNK_MS` |
| Pre-speech padding | ~300 ms (10 frames) | `_PADDING_CHUNKS` |
| Silence to end utterance | 1200 ms | `VAD_SILENCE_MS` |
| Minimum utterance length | 2000 ms | `VAD_MIN_SPEECH_MS` |
| Listen timeout after wake word | 10000 ms | `WAKE_LISTEN_TIMEOUT_MS` |
| VAD aggressiveness | 3 (most aggressive) | `VAD_AGGRESSIVENESS` |
