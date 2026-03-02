# Voice Assistant Daemon

A lightweight Python voice assistant for robotics/IoT edge deployments.

**Pipeline:** Mic → Wake Word → VAD → ASR → LLM → TTS → Speaker

---

## Requirements

### Dev / build machine
- Python 3.8
- `gcc`, `patchelf` (for Nuitka)
- All Python deps installed automatically by `make`

Run once to install all build dependencies:
```bash
make setup-build-deps
```

### Robot (one-time setup)

> The binary is self-contained — no Python needed on the robot.
> Only system-level audio and user group setup is required.

---

## Robot Setup (one-time)

### 1. Check audio hardware

```bash
# List playback devices (speaker)
aplay -l

# List capture devices (mic)
arecord -l
```

If no soundcards found, load the USB audio driver:
```bash
sudo modprobe snd-usb-audio

# Make it persistent
echo "snd-usb-audio" | sudo tee /etc/modules-load.d/audio.conf
```

### 2. Install runtime dependency

```bash
sudo apt-get update
sudo apt-get install -y libportaudio2
```

Or via Makefile (from dev machine):
```bash
make setup-robot ROBOT_HOST=192.168.0.63
```

### 3. Add user to audio group

```bash
sudo usermod -aG audio $USER
# Log out and back in for this to take effect
groups | grep audio   # verify
```

### 4. Set audio volume

```bash
# List available controls
amixer -c 0 scontrols   # mic card
amixer -c 1 scontrols   # speaker card

# Set volume using the control name shown above
amixer -c 0 set Capture 80%    # mic gain
amixer -c 1 set PCM 90%        # speaker volume

# Save settings across reboots
sudo alsactl store 0
sudo alsactl store 1
```

### 5. Find PyAudio device indices

The binary needs to know which device index corresponds to your mic and speaker:

```bash
python3 -c "
import pyaudio
pa = pyaudio.PyAudio()
for i in range(pa.get_device_count()):
    d = pa.get_device_info_by_index(i)
    print(f'[{i}] {d[\"name\"]}  in={d[\"maxInputChannels\"]} out={d[\"maxOutputChannels\"]}')
pa.terminate()
"
```

Set the indices in your `.env` file:
```ini
MIC_DEVICE_INDEX=0   # card with maxInputChannels > 0
SPK_DEVICE_INDEX=1   # card with maxOutputChannels > 0
```

### 6. Test mic and speaker

```bash
# Record 5 seconds
arecord -D plughw:0,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/test.wav

# Play back
aplay -D plughw:1,0 /tmp/test.wav
```

### 7. (Optional) PulseAudio AEC setup

For acoustic echo cancellation — see [Echo Cancellation](#echo-cancellation) section below.

---

## Build & Deploy

```bash
# 1. Install build deps (one-time, dev machine)
make setup-build-deps

# 2. Compile binary
make build

# 3. Copy binary + service file to robot
make deploy ROBOT_HOST=192.168.0.63

# 4. Install and start the systemd service on the robot
make install ROBOT_HOST=192.168.0.63
```

All `make` targets:

```
make build              Compile voice_assistant binary with Nuitka
make run                Run from source (dev/testing)
make deploy             Build + SCP binary + assets to robot
make install            Install + enable systemd service on robot
make service-start      Start service on robot
make service-stop       Stop service on robot
make service-logs       Tail service logs on robot
make setup-build-deps   Install system build deps locally (one-time)
make setup-robot        Install libportaudio2 on robot (one-time)
make clean              Remove Nuitka build artifacts
make clean-all          Remove build artifacts + venv
```

Override the robot target on any command:
```bash
make deploy ROBOT_USER=ubuntu ROBOT_HOST=192.168.0.63 ROBOT_DIR=/opt/myrobot
```

---

## Quick Start (dev / source run)

### 1. Create your `.env` file

```bash
cp .env.example .env
```

All variables have sensible defaults. The default wake word engine is **OpenWakeWord** (free, no key needed).

If you prefer **Porcupine** (faster detection), register at [https://console.picovoice.ai/](https://console.picovoice.ai/) and set:

```ini
WAKE_WORD_ENGINE=porcupine
PORCUPINE_ACCESS_KEY=<your-access-key>
```

### 2. Run from source

```bash
make run
```

The assistant starts in **IDLE** mode. Say the wake word to activate it, then speak your command.

---

## Wake Word Setup

Two wake word engines are supported. Set `WAKE_WORD_ENGINE` in `.env`:

| Engine | Speed | Cost | Setup |
|---|---|---|---|
| `openwakeword` (default) | Slower | Free | No key needed |
| `porcupine` | Fast | Free tier available | Requires access key |

### Option A: OpenWakeWord (default)

Built-in models (no download needed): `hey_jarvis`, `alexa`, `hey_mycroft`, `hey_rhasspy`

```ini
WAKE_WORD_ENGINE=openwakeword
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.5
```

Custom models: set `WAKE_WORD_MODEL_PATH` to a local `.onnx` or `.tflite` file.

### Option B: Porcupine

Built-in keywords: `porcupine`, `bumblebee`, `alexa`, `jarvis`, `computer`, `hey google`, etc.

1. Register at [https://console.picovoice.ai/](https://console.picovoice.ai/) and copy your **AccessKey**
2. Set in `.env`:

```ini
WAKE_WORD_ENGINE=porcupine
PORCUPINE_ACCESS_KEY=<your-key>
PORCUPINE_KEYWORD=jarvis
PORCUPINE_SENSITIVITY=0.5
```

### Porcupine — Custom keyword ("Hey Robot")

1. Log in to [https://console.picovoice.ai/](https://console.picovoice.ai/)
2. Go to **Wake Word** → create a new model → type `Hey Robot`
3. Select platform: **Linux** → download the `.ppn` file
4. Set in `.env`:

```ini
WAKE_WORD_ENGINE=porcupine
PORCUPINE_ACCESS_KEY=<your-key>
PORCUPINE_KEYWORD_PATH=/opt/voice_assistant/hey-robot_en_linux_v3_0_0.ppn
```

---

## Echo Cancellation

Both strategies are independent and can run simultaneously for maximum robustness.

### Strategy 1: Mic Mute (default: enabled)

Set in `.env` (enabled by default, no change needed unless disabling):
```ini
MIC_MUTE_DURING_PLAYBACK=true
```

- Mic is muted in software during TTS playback
- Simple, no extra setup
- Limitation: no barge-in (cannot interrupt the robot mid-speech)

### Strategy 2: WebRTC AEC via PulseAudio (auto-detected)

No config flag needed — the app detects AEC automatically at startup by checking
whether `module-echo-cancel` is loaded in PulseAudio:

```
[INFO] src.daemon: AEC detected: PulseAudio module-echo-cancel is loaded.
[INFO] src.daemon: AEC not detected: PulseAudio module-echo-cancel is not loaded.
```

**One-time setup on the robot:**

```bash
# 1. Install PulseAudio
sudo apt-get install -y pulseaudio pulseaudio-utils

# 2. Edit PulseAudio config
sudo nano /etc/pulse/default.pa
```

Add at the bottom (replace hw:0,0 / hw:1,0 with your actual card indices from `arecord -l` / `aplay -l`):

```
load-module module-echo-cancel \
    source_master=alsa_input.hw_0_0 \
    sink_master=alsa_output.hw_1_0 \
    aec_method=webrtc \
    rate=16000 \
    source_name=aec_mic \
    sink_name=aec_speaker
set-default-source aec_mic
set-default-sink aec_speaker
```

```bash
# 3. Restart PulseAudio
pulseaudio -k && pulseaudio --start

# 4. Verify AEC devices appear
pactl list short sources | grep aec
pactl list short sinks   | grep aec
```

### Recommended combination (best of both)

`.env`:
```ini
MIC_MUTE_DURING_PLAYBACK=true
```
Plus PulseAudio AEC configured at OS level — auto-detected by the app at startup.

| Strategy | Setup | Barge-in | Best for |
|---|---|---|---|
| Mic mute only | None | No | Simple deployments |
| AEC only | PulseAudio config | Yes | When barge-in needed |
| Both (recommended) | PulseAudio config | No | Maximum robustness |

---

## How It Works

See `docs/` for full Mermaid diagrams:

| File | Description |
|---|---|
| `docs/01_system_overview.md` | Full stack — mic hardware to speaker hardware |
| `docs/02_capture_state_machine.md` | IDLE / LISTENING state machine |
| `docs/03_wake_word_flow.md` | Wake word detection and ack sequence |
| `docs/04_pipeline_flow.md` | ASR → LLM → TTS pipeline |
| `docs/05_echo_cancellation.md` | Mute + AEC layers |
| `docs/06_threading_model.md` | Thread layout and synchronisation |

### State Machine (summary)

```
                    ┌─────────────────────────────────────────────────┐
                    │          Conversation Mode (5 min timeout)      │
                    │                                                 │
IDLE ── wake word ──► LISTENING ── utterance ──► ASR → LLM → TTS ──► LISTENING
  ▲  (OWW/Porcupine)   (VAD)                                    (keep talking)
  │                       │
  │                  5 min silence
  │                       │
  └───── goodbye ─────────┘
```

After the wake word, the system enters **conversation mode**: users can keep asking
questions without repeating the wake word. After 5 minutes of silence (configurable
via `CONVERSATION_TIMEOUT_MS`), the system says goodbye and returns to IDLE.

---

## Configuration

All settings live in `.env` (copy from `.env.example`). Every variable can also
be set as a regular environment variable — env vars always override `.env` values.

**Wake word:**

| Variable | Default | Description |
|---|---|---|
| `WAKE_WORD_ENGINE` | `openwakeword` | Engine: `openwakeword` or `porcupine` |
| `WAKE_WORD_MODEL` | `hey_jarvis` | OpenWakeWord built-in model name |
| `WAKE_WORD_MODEL_PATH` | `""` | Path to custom OWW model file |
| `WAKE_WORD_THRESHOLD` | `0.5` | OpenWakeWord detection threshold (0.0 – 1.0) |
| `PORCUPINE_ACCESS_KEY` | `""` | Picovoice access key (required for Porcupine) |
| `PORCUPINE_KEYWORD` | `jarvis` | Porcupine built-in keyword |
| `PORCUPINE_KEYWORD_PATH` | `""` | Path to custom `.ppn` file |
| `PORCUPINE_SENSITIVITY` | `0.5` | Porcupine sensitivity (0.0 – 1.0) |
| `WAKE_LISTEN_TIMEOUT_MS` | `10000` | ms to wait for speech after wake word |
| `CONVERSATION_TIMEOUT_MS` | `300000` | ms of silence before ending conversation (5 min) |
| `WAKE_WORD_ACK_PHRASE` | `Yes sir` | Phrase spoken after wake word; empty = beep only |

**Services:**

| Variable | Default | Description |
|---|---|---|
| `ASR_BASE_URL` | `http://3.114.138.123:8005` | ASR service base URL |
| `TTS_BASE_URL` | `http://3.114.138.123:8006` | TTS service base URL |
| `TTS_VOICE` | `default` | Voice: `default`, `liudao`, `filrty`, `zhiyu` |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | `nokey` | LLM API key |
| `LLM_MODEL` | `llama3` | Model name |
| `LLM_MAX_TOKENS` | `150` | Max reply tokens (limits response length) |
| `LLM_SYSTEM_PROMPT` | *(see config.py)* | System prompt |

**Audio & VAD:**

| Variable | Default | Description |
|---|---|---|
| `MIC_DEVICE_INDEX` | `-1` | PyAudio input device index; -1 = system default |
| `SPK_DEVICE_INDEX` | `-1` | PyAudio output device index; -1 = system default |
| `MIC_MUTE_DURING_PLAYBACK` | `true` | Mute mic in software during TTS playback |
| `VAD_AGGRESSIVENESS` | `3` | WebRTC VAD level 0–3 (3 = most aggressive) |
| `VAD_SILENCE_MS` | `1200` | Silence duration (ms) that ends an utterance |
| `VAD_MIN_SPEECH_MS` | `2000` | Minimum speech length (ms); shorter clips ignored |

**Timeouts & logging:**

| Variable | Default | Description |
|---|---|---|
| `ASR_TIMEOUT` | `30` | ASR HTTP timeout (seconds) |
| `TTS_TIMEOUT` | `60` | TTS HTTP timeout (seconds) |
| `LLM_TIMEOUT` | `60` | LLM HTTP timeout (seconds) |
| `LOG_FILE` | `/var/log/voice_assistant.log` | Log file path |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Running as a systemd Service

The service runs the compiled binary at `/opt/voice_assistant/voice_assistant`.
All configuration is read from `/opt/voice_assistant/.env` on the robot.

### Automated (via Makefile)

```bash
# 1. Fill in your .env locally first
cp .env.example .env
editor .env   # set WAKE_WORD_ENGINE, device indices, service URLs, etc.

# 2. Build, copy binary + .env to robot, install service
make deploy ROBOT_HOST=192.168.0.63
make install ROBOT_HOST=192.168.0.63

# 3. Tail logs
make service-logs ROBOT_HOST=192.168.0.63
```

`make deploy` automatically copies `.env` to `/opt/voice_assistant/.env` on the robot
and sets permissions to `600` (owner-read-only).

### Manual (on the robot)

```bash
# Copy and edit config
cp /opt/voice_assistant/.env.example /opt/voice_assistant/.env
nano /opt/voice_assistant/.env   # set your keys

# Install service
sudo cp /opt/voice_assistant/voice_assistant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voice_assistant
sudo systemctl start voice_assistant
journalctl -u voice_assistant -f
```

### Updating config without redeploying the binary

Just edit `.env` on the robot and restart the service:

```bash
ssh ubuntu@<robot> "nano /opt/voice_assistant/.env"
ssh ubuntu@<robot> "sudo systemctl restart voice_assistant"
```

---

## Testing

Individual service tests and a full end-to-end integration test:

```bash
# Individual services
python tests/test_asr.py                   # ASR health + transcription
python tests/test_tts.py                   # TTS health + synthesis
python tests/test_llm.py                   # LLM health + chat

# End-to-end pipeline (no mic needed)
python tests/test_e2e.py                   # TTS → ASR → LLM → TTS → ASR round-trip
python tests/test_e2e.py --output-dir /tmp/e2e_results   # save WAV files
python tests/test_e2e.py --skip-wake-word                # skip wake word test
python tests/test_e2e.py --questions "what time is it"   # custom questions
```

---

## File Structure

```
voice_assistant.py          # Entry point — logging setup + run daemon
config.py                   # Redirect shim → src/config.py
requirements.txt            # Python dependencies
Makefile                    # Build, deploy, and service management
voice_assistant.service     # systemd unit file (runs compiled binary)
.env.example                # Configuration template — copy to .env and fill in values
.env                        # Your local config (git-ignored, never committed)
API_DOCUMENTATION_EN.md     # ASR / TTS API reference
src/
├── config.py               # All configuration with env var overrides
├── asr.py                  # ASRClient
├── llm.py                  # LLMClient
├── tts.py                  # TTSClient
├── audio.py                # AudioPlayer + pcm_frames_to_wav
├── capture.py              # MicrophoneCapture (wake word + VAD state machine)
└── daemon.py               # VoiceAssistantDaemon (pipeline orchestrator)
tests/
├── test_asr.py             # ASR service test
├── test_tts.py             # TTS service test
├── test_llm.py             # LLM service test
└── test_e2e.py             # End-to-end integration test
docs/
├── 01_system_overview.md
├── 02_capture_state_machine.md
├── 03_wake_word_flow.md
├── 04_pipeline_flow.md
├── 05_echo_cancellation.md
└── 06_threading_model.md
dist/voice_assistant        # Compiled binary (after make build)
```
