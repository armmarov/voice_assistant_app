# Pipeline Flow

Full flow from captured utterance through ASR → LLM → TTS → playback.

```mermaid
sequenceDiagram
    participant CL as Capture Loop\n(capture thread)
    participant DM as Daemon\n(pipeline thread)
    participant ASR as ASR Service\nhttp://host:8005
    participant LLM as LLM Service\nhttp://localhost:11434
    participant TTS as TTS Service\nhttp://host:8006
    participant MIC as MicrophoneCapture
    participant PLY as AudioPlayer

    CL ->> CL: VAD detects end of utterance\n(1200ms silence, ≥ 2000ms speech)
    CL ->> CL: pcm_frames_to_wav()

    CL ->> DM: on_utterance(wav_bytes)

    alt busy.is_set()
        DM ->> DM: drop utterance\n(still playing previous response)
    else not busy
        DM ->> DM: busy.set()\nstart pipeline thread
    end

    DM ->> ASR: POST /asr\n{wav_base64: ...}
    ASR -->> DM: {text: "what time is it"}

    alt ASR empty result
        DM ->> DM: skip → busy.clear()
    end

    DM ->> LLM: POST /v1/chat/completions\n{model, messages, stream:false}
    LLM -->> DM: {choices[0].message.content}

    alt LLM no reply
        DM ->> DM: skip → busy.clear()
    end

    DM ->> TTS: POST /generate\n{target_text, voice_type}
    TTS -->> DM: WAV bytes (44100Hz mono)

    alt TTS no audio
        DM ->> DM: skip → busy.clear()
    end

    DM ->> MIC: mute()
    DM ->> PLY: play(wav_bytes)
    PLY -->> DM: playback complete
    DM ->> MIC: unmute() → state = IDLE
    DM ->> DM: busy.clear()

    Note over CL: Back to IDLE\nwaiting for wake word
```

## Pipeline Error Handling

```mermaid
flowchart TD
    START([on_utterance called])
    BUSY{busy?}
    DROP[Drop utterance\nlog: still playing]
    ASR[ASR transcribe]
    ASR_OK{text returned?}
    SKIP_ASR[Skip\nlog: empty result]
    LLM[LLM chat]
    LLM_OK{reply returned?}
    SKIP_LLM[Skip\nlog: no reply]
    TTS[TTS synthesize]
    TTS_OK{audio returned?}
    SKIP_TTS[Skip\nlog: no audio]
    PLAY[Play audio\nmic muted]
    DONE([busy.clear\nstate = IDLE])

    START --> BUSY
    BUSY -->|yes| DROP --> DONE
    BUSY -->|no| ASR
    ASR --> ASR_OK
    ASR_OK -->|no| SKIP_ASR --> DONE
    ASR_OK -->|yes| LLM
    LLM --> LLM_OK
    LLM_OK -->|no| SKIP_LLM --> DONE
    LLM_OK -->|yes| TTS
    TTS --> TTS_OK
    TTS_OK -->|no| SKIP_TTS --> DONE
    TTS_OK -->|yes| PLAY --> DONE
```

## HTTP Timeout Configuration

| Service | Timeout | Config key |
|---|---|---|
| ASR | 30s | `ASR_TIMEOUT` |
| LLM | 60s | `LLM_TIMEOUT` |
| TTS | 60s | `TTS_TIMEOUT` |
