# Echo Cancellation

Two independent layers of echo protection — both can be active simultaneously.

```mermaid
flowchart TD
    SPK[🔊 Speaker output]
    AIR["Sound travels\nthrough air"]
    MIC[🎤 Microphone picks up\nspeaker echo + voice]

    subgraph OS ["OS Layer (PulseAudio)"]
        AEC["module-echo-cancel\nWebRTC AEC\nauto-detected at startup"]
    end

    subgraph APP ["App Layer (src/daemon.py)"]
        MUTE["Mic Mute\nMIC_MUTE_DURING_PLAYBACK"]
    end

    CLEAN[Clean audio\nno echo]
    PORC[Porcupine / VAD]

    SPK --> AIR --> MIC
    SPK -.->|reference signal\nfed directly| AEC
    MIC --> AEC
    AEC -->|echo removed| MUTE
    MUTE -->|frames discarded\nduring playback| CLEAN
    CLEAN --> PORC

    style AEC fill:#fffbe6,stroke:#f0c040
    style MUTE fill:#e6f0ff,stroke:#4080c0
```

## Strategy Comparison

```mermaid
flowchart LR
    subgraph MUTE_MODE ["Mic Mute (MIC_MUTE_DURING_PLAYBACK=true)"]
        M1[Robot starts speaking]
        M2[mute called\nframes discarded]
        M3[Playback finishes]
        M4[unmute called\nstate = IDLE or LISTENING]
        M1 --> M2 --> M3 --> M4
    end

    subgraph AEC_MODE ["WebRTC AEC (PulseAudio)"]
        A1[Robot starts speaking]
        A2[Speaker signal fed as\nreference to AEC chip]
        A3[AEC subtracts echo\nfrom mic in real-time]
        A4[Clean mic signal\npassed to app]
        A1 --> A2 --> A3 --> A4
    end
```

## Startup AEC Detection

```mermaid
flowchart TD
    INIT[VoiceAssistantDaemon.__init__]
    DETECT[_detect_aec\npactl list short modules]
    FOUND{module-echo-cancel\nin output?}
    ACTIVE["_aec_active = True\nlog: AEC detected"]
    INACTIVE["_aec_active = False\nlog: AEC not detected"]
    LOG["Logged on every playback:\nPlaying response (mute=True aec=True/False)"]

    INIT --> DETECT --> FOUND
    FOUND -->|yes| ACTIVE --> LOG
    FOUND -->|no| INACTIVE --> LOG
```

## Configuration

| Setting | Default | Description |
|---|---|---|
| `MIC_MUTE_DURING_PLAYBACK` | `true` | Mute mic in software during playback |
| AEC | auto-detected | Reads PulseAudio modules at startup — no manual flag |

## Recommended Combinations

| Mic Mute | AEC | Barge-in | Best for |
|---|---|---|---|
| ✓ | ✗ | No | Simple robot, no PulseAudio setup |
| ✗ | ✓ | Yes | PulseAudio AEC configured |
| ✓ | ✓ | No | Maximum robustness (recommended) |
| ✗ | ✗ | — | ⚠️ No protection — not recommended |
