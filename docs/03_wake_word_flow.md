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
    DM ->> MIC: resume_listening()\n→ state = LISTENING\n→ timeout = WAKE_LISTEN_TIMEOUT
    DM ->> DM: busy.clear()

    Note over CL: Now in LISTENING state\nready to capture command\n(after reply, enters conversation mode)
```

## Wake Word Configuration

```mermaid
flowchart TD
    ENGINE{WAKE_WORD_ENGINE?}

    subgraph PPN ["Porcupine"]
        PPN_CHECK{PORCUPINE_KEYWORD_PATH\nset?}
        PPN_CUSTOM["pvporcupine.create()\nkeyword_paths = .ppn file"]
        PPN_BUILTIN["pvporcupine.create()\nkeywords = 'jarvis'"]
        PPN_READY[Porcupine ready\nframe_length = 512]
        PPN_CHECK -->|yes| PPN_CUSTOM --> PPN_READY
        PPN_CHECK -->|no| PPN_BUILTIN --> PPN_READY
    end

    subgraph OWW ["OpenWakeWord"]
        OWW_CHECK{WAKE_WORD_MODEL_PATH\nset?}
        OWW_CUSTOM["Model(wakeword_models=[path])"]
        OWW_BUILTIN["Model(wakeword_models=['hey_jarvis'])"]
        OWW_READY[OpenWakeWord ready\nany chunk size]
        OWW_CHECK -->|yes| OWW_CUSTOM --> OWW_READY
        OWW_CHECK -->|no| OWW_BUILTIN --> OWW_READY
    end

    ENGINE -->|porcupine| PPN_CHECK
    ENGINE -->|openwakeword| OWW_CHECK
```

## Built-in Keywords Available for Testing

```
alexa        americano    blueberry    bumblebee
computer     grapefruit   grasshopper  hey google
hey siri     jarvis       ok google    picovoice
porcupine    terminator
```

> **Note:** Built-in keywords are for testing only. Use a custom `.ppn` file for production ("Hey Robot").
