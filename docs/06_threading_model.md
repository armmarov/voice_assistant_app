# Threading Model

The daemon uses three thread types with clear ownership boundaries.

```mermaid
flowchart TD
    subgraph MAIN ["Main Thread"]
        RUN[daemon.run\nevent loop]
        SIGNAL[signal handler\nSIGINT / SIGTERM]
        SHUTDOWN[_shutdown\nmic.stop + player.terminate]
        RUN -->|stop event set| SHUTDOWN
        SIGNAL --> RUN
    end

    subgraph CAPTURE ["Capture Thread (daemon, background)"]
        LOOP[_capture_loop]
        PORC2[Porcupine.process]
        VAD2[VAD.is_speech]
        CB_WAKE[on_wake_word callback]
        CB_UTT[on_utterance callback]
        LOOP --> PORC2
        PORC2 -->|wake word| CB_WAKE
        PORC2 -->|listening| VAD2
        VAD2 -->|utterance done| CB_UTT
    end

    subgraph ACK ["Ack Thread (daemon, per wake word)"]
        PLAY_ACK[_play_ack\nTTS or beep]
        MUTE1[mic.mute]
        RESUME[mic.resume_listening]
        PLAY_ACK --> MUTE1 --> RESUME
    end

    subgraph PIPELINE ["Pipeline Thread (daemon, per utterance)"]
        PIPE[_pipeline]
        ASR2[ASR.transcribe]
        LLM2[LLM.chat]
        TTS2[TTS.synthesize]
        PLAY2[AudioPlayer.play]
        MUTE2[mic.mute]
        UNMUTE[mic.unmute]
        PIPE --> ASR2 --> LLM2 --> TTS2 --> MUTE2 --> PLAY2 --> UNMUTE
    end

    RUN -->|mic.start| CAPTURE
    CB_WAKE -->|Thread.start| ACK
    CB_UTT -->|Thread.start\nif not busy| PIPELINE
```

## Shared State & Synchronisation

```mermaid
flowchart LR
    subgraph SHARED ["Shared State"]
        BUSY["_busy : threading.Event\nguards pipeline + ack\nprevents utterance overlap"]
        STOP["_stop : threading.Event\nsignals main loop to exit"]
        MLOCK["_mute_lock : threading.Lock\nguards _muted + _resume_to_listening"]
        HIST["LLMClient._lock : threading.Lock\nguards conversation history"]
        PLOCK["AudioPlayer._lock : threading.Lock\nguards PyAudio output stream"]
    end

    CAPTURE -->|reads| MLOCK
    ACK -->|writes| MLOCK
    PIPELINE -->|writes| MLOCK
    PIPELINE -->|set/clear| BUSY
    ACK -->|set/clear| BUSY
    CAPTURE -->|reads| BUSY
    PIPELINE -->|reads/writes| HIST
    PIPELINE -->|acquires| PLOCK
    ACK -->|acquires| PLOCK
    MAIN -->|set| STOP
    MAIN -->|reads| STOP
```

## Thread Lifecycle

```mermaid
gantt
    title Thread activity timeline (example interaction)
    dateFormat ss
    axisFormat %S s

    section Main Thread
    Event loop (sleep 0.5s) : 00, 20s

    section Capture Thread
    IDLE - Porcupine scanning : 00, 03s
    Wake word detected        : 03, 01s
    LISTENING - VAD active    : 04, 05s
    IDLE - waiting next word  : 09, 11s

    section Ack Thread
    TTS / beep + playback     : 03, 02s

    section Pipeline Thread
    ASR transcribe            : 09, 02s
    LLM generate              : 11, 03s
    TTS synthesize            : 14, 02s
    AudioPlayer play          : 16, 03s
```

## Key Rules

| Rule | Reason |
|---|---|
| Only one pipeline thread at a time (`_busy`) | Prevents overlapping ASR/LLM/TTS calls and audio |
| `_busy` also set during ack playback | Prevents utterance captured during ack from starting pipeline |
| Capture thread never blocks on network | All HTTP calls happen in pipeline/ack threads |
| `AudioPlayer` has its own lock | Safe if ack and pipeline race to play (queued, not crashed) |
| `resume_listening()` vs `unmute()` | Ack → LISTENING, pipeline → IDLE |
