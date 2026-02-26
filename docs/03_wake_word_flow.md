# Wake Word Detection Flow

What happens from the moment the wake word is heard to when the robot is ready to listen.

```mermaid
sequenceDiagram
    participant CL as Capture Loop\n(capture thread)
    participant PORC as Porcupine
    participant DM as Daemon\n(ack thread)
    participant TTS as TTS Service
    participant PLY as AudioPlayer
    participant MIC as MicrophoneCapture

    loop IDLE — every 32ms (512 samples)
        CL ->> PORC: process(pcm_int16)
        PORC -->> CL: keyword_index = -1
    end

    CL ->> PORC: process(pcm_int16)
    PORC -->> CL: keyword_index >= 0 ✓

    CL ->> DM: on_wake_word()
    CL ->> CL: state = LISTENING\ntimeout_left = 10s

    DM ->> DM: start ack thread
    DM ->> DM: busy.set()
    DM ->> TTS: synthesize("Yes sir")

    alt TTS available
        TTS -->> DM: WAV bytes
    else TTS unavailable
        DM ->> DM: generate_beep_wav()\n880Hz, 200ms
    end

    DM ->> MIC: mute()
    DM ->> PLY: play(audio)
    PLY -->> DM: playback complete
    DM ->> MIC: resume_listening()\n→ state = LISTENING\n→ timeout refreshed
    DM ->> DM: busy.clear()

    Note over CL: Now in LISTENING state\nready to capture command
```

## Wake Word Configuration

```mermaid
flowchart TD
    CHECK{WAKE_WORD_MODEL_PATH\nset?}
    CUSTOM["pvporcupine.create()\nkeyword_paths = .ppn file\n'Hey Robot'"]
    BUILTIN["pvporcupine.create()\nkeywords = 'porcupine'\n⚠️ testing only"]
    READY[Porcupine ready\nframe_length = 512 samples]

    CHECK -->|yes| CUSTOM --> READY
    CHECK -->|no| BUILTIN --> READY
```

## Built-in Keywords Available for Testing

```
alexa        americano    blueberry    bumblebee
computer     grapefruit   grasshopper  hey google
hey siri     jarvis       ok google    picovoice
porcupine    terminator
```

> **Note:** Built-in keywords are for testing only. Use a custom `.ppn` file for production ("Hey Robot").
