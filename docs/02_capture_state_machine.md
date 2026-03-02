# Microphone Capture — State Machine

`MicrophoneCapture` in `src/capture.py` runs a continuous loop with two states and a **conversation mode** flag.

## Conversation Mode

After the first wake word + reply cycle, the system enters **conversation mode**:
the user can keep asking questions without repeating the wake word.
Conversation mode ends after `CONVERSATION_TIMEOUT_MS` (default 5 minutes) of silence.

```
Hey Jarvis → "Yes sir" → Q1 → reply → Q2 (no wake word!) → reply → ... → 5 min silence → IDLE
```

## State Diagram

```mermaid
stateDiagram-v2
    [*] --> IDLE : start()

    IDLE --> IDLE : frame read\nmuted → discard frame\n(Porcupine still fed)
    IDLE --> IDLE : frame read\nwake word engine → no match
    IDLE --> LISTENING : wake word detected\non_wake_word() fired\ntimeout = WAKE_LISTEN_TIMEOUT

    LISTENING --> LISTENING : muted (ack playback)\n→ discard frames
    LISTENING --> LISTENING : resume_listening()\n(after ack)\n→ timeout = WAKE_LISTEN_TIMEOUT
    LISTENING --> LISTENING : resume_conversation()\n(after TTS reply)\n→ in_conversation = true\n→ timeout = CONVERSATION_TIMEOUT
    LISTENING --> LISTENING : is_speech = True\n→ silence_count = 0\n→ timeout refreshed
    LISTENING --> LISTENING : utterance too short\n→ voiced reset, keep listening

    LISTENING --> LISTENING : utterance complete\nAND in_conversation\n→ on_utterance(wav)\n→ stay LISTENING

    LISTENING --> IDLE : utterance complete\nAND NOT in_conversation\n→ on_utterance(wav)
    LISTENING --> IDLE : timeout expires\n→ in_conversation = false\n→ on_listen_timeout()

    [*] --> IDLE : stop()
```

## Conversation Flow

```mermaid
flowchart TD
    IDLE[IDLE\nwake word detection]
    WAKE{Wake word\ndetected?}
    ACK[Play 'Yes sir' + beep\nmic muted during ack]
    LISTEN[LISTENING\nVAD captures speech]
    PIPE[Pipeline\nASR → LLM → TTS]
    CONV_RESUME[resume_conversation\nin_conversation = true\ntimeout = 5 min]
    TIMEOUT{Conversation\ntimeout?}
    GOODBYE[Speak goodbye\nreturn to IDLE]

    IDLE --> WAKE
    WAKE -->|no| IDLE
    WAKE -->|yes| ACK --> LISTEN
    LISTEN -->|utterance| PIPE
    PIPE --> CONV_RESUME --> LISTEN
    LISTEN -->|timeout| TIMEOUT
    TIMEOUT -->|5 min silence| GOODBYE --> IDLE
```

## Frame Size Reconciliation

Wake word engines and WebRTC VAD require different frame sizes:

```mermaid
flowchart LR
    READ["stream.read(480 samples)\n= 960 bytes"]
    BUF["Porcupine buffer\n480 → 512 samples"]
    PORC["Porcupine.process()\n512 int16 samples"]
    VAD["VAD.is_speech()\n480 samples\n= 960 bytes"]
    OWW["OpenWakeWord.predict()\n480 samples\n(any chunk size)"]

    READ --> BUF --> PORC
    READ --> VAD
    READ --> OWW
```

## Key Timing Values

| Parameter | Value | Config key |
|---|---|---|
| VAD frame size | 30 ms / 480 samples | `MIC_CHUNK_MS` |
| Pre-speech padding | ~300 ms (10 frames) | `_PADDING_CHUNKS` |
| Silence to end utterance | 1200 ms | `VAD_SILENCE_MS` |
| Minimum utterance length | 2000 ms | `VAD_MIN_SPEECH_MS` |
| Listen timeout after wake word | 10000 ms | `WAKE_LISTEN_TIMEOUT_MS` |
| Conversation timeout | 300000 ms (5 min) | `CONVERSATION_TIMEOUT_MS` |
| VAD aggressiveness | 3 (most aggressive) | `VAD_AGGRESSIVENESS` |
