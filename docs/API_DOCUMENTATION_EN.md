# MYEG Voice Service API Documentation

This document describes the API interfaces for three services: ASR (Automatic Speech Recognition), TTS (Text-to-Speech), and LiveKit (Real-time Voice Communication).

---

## Table of Contents

- [1. ASR Service API](#1-asr-service-api)
- [2. TTS Service API](#2-tts-service-api)
- [3. OpenAI Compatible Interface](#3-openai-compatible-interface)
- [4. LiveKit Service API](#4-livekit-service-api)
- [5. Error Handling](#5-error-handling)
- [6. Test Scripts](#6-test-scripts)

---

## 1. ASR Service API

ASR (Automatic Speech Recognition) service provides speech recognition functionality, converting audio to text.

### Service Information

| Item | Value |
|------|-------|
| Port | 8005 (or 1203) |
| Protocol | HTTP REST |
| Default Address | `http://3.114.138.123:8005` |

### 1.1 Health Check

Check if the ASR service is running normally.

**Request**

```
GET /health
```

**Response**

```json
{
  "status": "ok",
  "service": "asr"
}
```

---

### 1.2 Speech Recognition

Convert audio to text.

**Request**

```
POST /asr
Content-Type: application/json
```

**Request Body Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `wav_base64` | string | Either one | Base64 encoded audio file |
| `wav_url` | string | Either one | URL address of audio file |

**Supported Audio Formats**

- WAV (recommended)
- MP3
- OGG
- FLAC
- WebM
- M4A

**Request Example**

```json
{
  "wav_base64": "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="
}
```

Or using URL:

```json
{
  "wav_url": "https://example.com/audio.wav"
}
```

**Response**

```json
{
  "text": "Hello, how can I help you?",
  "timing": {
    "audio_duration_s": 2.5,
    "decode_ms": 5.2,
    "convert_ms": 12.3,
    "infer_ms": 156.7,
    "total_ms": 174.2,
    "rtf": 0.063
  }
}
```

**Response Field Description**

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Recognition result text |
| `timing.audio_duration_s` | float | Audio duration (seconds) |
| `timing.decode_ms` | float | Audio decoding time (milliseconds) |
| `timing.convert_ms` | float | Format conversion time (milliseconds) |
| `timing.infer_ms` | float | Model inference time (milliseconds) |
| `timing.total_ms` | float | Total processing time (milliseconds) |
| `timing.rtf` | float | Real-time factor (inference time/audio duration, smaller is better) |

---

## 2. TTS Service API

TTS (Text-to-Speech) service provides speech synthesis functionality, converting text to speech.

### Service Information

| Item | Value |
|------|-------|
| Port | 8006 (or 1204) |
| Protocol | HTTP REST |
| Default Address | `http://3.114.138.123:8006` |
| Audio Sample Rate | 44100 Hz |
| Audio Format | 16-bit PCM / WAV |

### 2.1 Health Check

**Request**

```
GET /health
```

**Response**

```json
{
  "status": "ok",
  "service": "tts"
}
```

---

### 2.2 Get Available Voices

List all available preset voices.

**Request**

```
GET /voices
```

**Response**

```json
{
  "default": true,
  "liudao": true,
  "filrty": true,
  "zhiyu": true
}
```

> `true` means the voice is available, `false` means the voice configuration exists but lacks audio files.

---

### 2.3 Speech Synthesis (Basic Interface)

Convert text to speech using preset voices.

**Request**

```
POST /generate
Content-Type: application/json
```

**Request Body Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_text` | string | ✅ | - | Text to synthesize |
| `voice_type` | string | ❌ | `null` | Voice name (e.g., liudao, filrty) |
| `external_id` | string | ❌ | `null` | Registered prompt ID |
| `stream` | bool | ❌ | `false` | Whether to return streaming |
| `max_generate_length` | int | ❌ | `2000` | Maximum generation length |
| `temperature` | float | ❌ | `1.0` | Temperature parameter |
| `cfg_value` | float | ❌ | `2.5` | CFG control value |
| `do_normalize` | bool | ❌ | `false` | Whether to normalize text |

**Request Example**

```json
{
  "target_text": "Hello, nice to meet you!",
  "voice_type": "liudao",
  "stream": false,
  "cfg_value": 2.5
}
```

**Response**

- **Non-streaming (`stream: false`)**: Returns complete WAV file
  - Content-Type: `audio/wav`
  
- **Streaming (`stream: true`)**: Returns PCM audio stream
  - Content-Type: `audio/raw`
  - Format: 16-bit PCM, 44100 Hz, mono

---

### 2.4 Speech Synthesis (With Prompt / Voice Cloning)

Use custom reference audio for voice cloning synthesis.

**Request**

```
POST /generate_with_prompt
Content-Type: application/json
```

**Request Body Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_text` | string | ✅ | - | Text to synthesize |
| `voice_type` | string | ❌ | `null` | Use preset voice (either one with wav_base64) |
| `wav_base64` | string | ❌ | `null` | Base64 encoded reference audio |
| `wav_url` | string | ❌ | `null` | Reference audio URL |
| `prompt_text` | string | ❌ | `""` | Text corresponding to reference audio |
| `denoise` | bool | ❌ | `false` | Whether to denoise reference audio |
| `register` | bool | ❌ | `false` | Whether to persistently save prompt |
| `audio_format` | string | ❌ | `"wav"` | Reference audio format |
| `stream` | bool | ❌ | `false` | Whether to return streaming |
| `max_generate_length` | int | ❌ | `2000` | Maximum generation length |
| `temperature` | float | ❌ | `1.0` | Temperature parameter |
| `cfg_value` | float | ❌ | `2.5` | CFG control value |
| `do_normalize` | bool | ❌ | `false` | Whether to normalize text |

**Request Example**

```json
{
  "target_text": "This is the target text to synthesize",
  "wav_base64": "UklGRiQAAABXQVZFZm10...",
  "prompt_text": "Words spoken in reference audio",
  "denoise": true,
  "stream": false
}
```

---

### 2.5 Add Prompt

Register a new voice prompt for subsequent TTS requests.

**Request**

```
POST /add_prompt
Content-Type: application/json
```

**Request Body Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `wav_base64` | string | Either one | Base64 encoded reference audio |
| `wav_url` | string | Either one | Reference audio URL |
| `prompt_text` | string | ❌ | Text corresponding to reference audio |
| `denoise` | bool | ❌ | Whether to apply noise reduction |
| `audio_format` | string | ❌ | Audio format (wav/mp3) |

**Response**

```json
{
  "external_id": "abc123def456",
  "internal_id": "prompt_0"
}
```

> Subsequently, you can use `external_id` in the `/generate` interface through the `external_id` parameter to reference this voice.

---

### 2.6 Delete Prompt

Delete registered voice prompt.

**Request**

```
DELETE /prompt/{external_id}
```

**Response**

```json
{
  "status": "deleted",
  "external_id": "abc123def456"
}
```

---

## 3. OpenAI Compatible Interface

This service provides OpenAI API compatible interfaces, making it easy for applications already using OpenAI SDK to switch seamlessly.

### 3.1 Speech Recognition (Whisper Compatible)

Speech recognition interface compatible with OpenAI Whisper API.

**Request**

```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

**Request Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | file | ✅ | - | Audio file (supports flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm) |
| `model` | string | ❌ | `whisper-1` | Model name (for compatibility, actually uses SenseVoice) |
| `language` | string | ❌ | `auto` | Language code (e.g., zh, en) |
| `prompt` | string | ❌ | - | Prompt text (not yet supported) |
| `response_format` | string | ❌ | `json` | Response format: json, text, srt, verbose_json, vtt |
| `temperature` | float | ❌ | `0.0` | Temperature parameter |

**cURL Example**

```bash
curl -X POST "http://localhost:8005/v1/audio/transcriptions" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=json"
```

**Python Example (Using OpenAI SDK)**

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",  # If authentication is enabled
    base_url="http://localhost:8005/v1"
)

audio_file = open("audio.wav", "rb")
transcript = client.audio.transcriptions.create(
    model="whisper-1",
    file=audio_file
)
print(transcript.text)
```

**Response Example (json format)**

```json
{
  "text": "Hello, how can I help you?"
}
```

**Response Example (verbose_json format)**

```json
{
  "task": "transcribe",
  "language": "zh",
  "duration": 2.5,
  "text": "Hello, how can I help you?",
  "segments": [
    {
      "id": 0,
      "seek": 0,
      "start": 0.0,
      "end": 2.5,
      "text": "Hello, how can I help you?",
      "tokens": [],
      "temperature": 0.0,
      "avg_logprob": 0.0,
      "compression_ratio": 1.0,
      "no_speech_prob": 0.0
    }
  ]
}
```

---

### 3.2 Speech Synthesis (TTS Compatible)

Speech synthesis interface compatible with OpenAI TTS API.

**Request**

```
POST /v1/audio/speech
Content-Type: application/json
```

**Request Body Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `model` | string | ❌ | `tts-1` | Model name (tts-1 or tts-1-hd) |
| `input` | string | ✅ | - | Text to synthesize (max 4096 characters) |
| `voice` | string | ❌ | `alloy` | Voice name |
| `response_format` | string | ❌ | `wav` | Output format: wav, mp3, opus, aac, flac, pcm |
| `speed` | float | ❌ | `1.0` | Speech speed (0.25-4.0, not yet supported) |

**Supported Voices**

OpenAI standard voices (mapped to default voice):
- `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

Custom voices:
- Use `/voices` interface to get available preset voices
- Custom voices registered using `/add_prompt` interface

**cURL Example**

```bash
curl -X POST "http://localhost:8006/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "Hello, nice to meet you!",
    "voice": "alloy"
  }' \
  --output speech.mp3
```

**Python Example (Using OpenAI SDK)**

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",  # If authentication is enabled
    base_url="http://localhost:8006/v1"
)

response = client.audio.speech.create(
    model="tts-1",
    voice="alloy",
    input="Hello, nice to meet you!"
)

response.stream_to_file("output.mp3")
```

**Response**

Returns audio file, Content-Type based on requested format:
- `mp3`: `audio/mpeg`
- `wav`: `audio/wav`
- `pcm`: `audio/pcm` (16-bit, 44100 Hz, mono)
- `opus`: `audio/opus`
- `aac`: `audio/aac`
- `flac`: `audio/flac`

---

### 3.3 Get Available Voices

**Request**

```
GET /v1/audio/voices
```

**Response**

```json
{
  "voices": [
    {
      "voice_id": "alloy",
      "name": "Alloy",
      "type": "openai_compatible",
      "mapped_to": "default"
    },
    {
      "voice_id": "liudao",
      "name": "liudao",
      "type": "preset",
      "mapped_to": "liudao"
    }
  ]
}
```

---

### 3.4 Get Model List

**Request**

```
GET /v1/models
```

**Response (ASR Service)**

```json
{
  "object": "list",
  "data": [
    {
      "id": "whisper-1",
      "object": "model",
      "created": 1677610602,
      "owned_by": "myeg"
    }
  ]
}
```

**Response (TTS Service)**

```json
{
  "object": "list",
  "data": [
    {
      "id": "tts-1",
      "object": "model",
      "created": 1677610602,
      "owned_by": "myeg"
    },
    {
      "id": "tts-1-hd",
      "object": "model",
      "created": 1677610602,
      "owned_by": "myeg"
    }
  ]
}
```

---

## 4. LiveKit Service API

LiveKit provides real-time voice communication capabilities through WebRTC protocol.

### Service Information

| Item | Value |
|------|-------|
| Port | 8003 |
| Protocol | WebSocket / WebRTC |
| Default Address | `ws://3.114.138.123:8003` |
| API Key (Development) | `devkey` |
| API Secret (Development) | `secret` |

### 4.1 Generate Access Token

Clients need to use JWT tokens to connect to LiveKit rooms.

**Python Example**

```python
from livekit import api

def generate_token(room_name: str, participant_name: str) -> str:
    token = api.AccessToken(
        api_key="devkey",
        api_secret="secret"
    )
    token.with_identity(participant_name)
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    ))
    return token.to_jwt()
```

### 4.2 Connect to Room

**Python**

```python
from livekit import rtc

room = rtc.Room()
await room.connect("ws://localhost:7880", token)
```

**JavaScript**

```javascript
const room = new LivekitClient.Room();
await room.connect("ws://localhost:7880", token);
```

### 4.3 Publish Audio Track

**Audio Format Requirements**

| Parameter | Value |
|-----------|-------|
| Sample Rate | 16000 Hz |
| Channels | 1 (mono) |
| Bit Depth | 16-bit |
| Frame Size | 20ms (320 samples) |
| Format | PCM |

**Python Example**

```python
# Create audio source
audio_source = rtc.AudioSource(sample_rate=16000, num_channels=1)

# Create track
audio_track = rtc.LocalAudioTrack.create_audio_track("microphone", audio_source)

# Publish
options = rtc.TrackPublishOptions()
options.source = rtc.TrackSource.SOURCE_MICROPHONE
await room.local_participant.publish_track(audio_track, options)

# Send audio frame
frame = rtc.AudioFrame(
    data=audio_bytes,
    sample_rate=16000,
    num_channels=1,
    samples_per_channel=320,  # 20ms @ 16kHz
)
await audio_source.capture_frame(frame)
```

### 4.4 Subscribe to Remote Audio

**Python Example**

```python
@room.on("track_subscribed")
def on_track_subscribed(track, publication, participant):
    if track.kind == rtc.TrackKind.KIND_AUDIO:
        audio_stream = rtc.AudioStream(track)
        asyncio.create_task(receive_audio(audio_stream))

async def receive_audio(audio_stream):
    async for frame_event in audio_stream:
        audio_data = frame_event.frame.data
        # Process audio data
```

### 4.5 RPC Methods

Agent supports method calls through LiveKit RPC.

#### `new_conversation`

Start a new conversation, clear conversation history.

```python
await room.local_participant.perform_rpc(
    destination_identity=agent.identity,
    method="new_conversation",
    payload="",
)
```

---

## 5. Error Handling

### Unified Error Response Format

```json
{
  "code": 4000,
  "message": "Error description",
  "detail": {
    "error": "Detailed error information"
  },
  "error_type": "ValidationError"
}
```

### Common Error Codes

| HTTP Status Code | Error Code | Description |
|------------------|------------|-------------|
| 400 | 4000 | Request parameter error |
| 401 | 401 | Authentication failed |
| 404 | 4100 | Resource not found (e.g., voice does not exist) |
| 500 | 4300 | Service internal error (e.g., ASR/TTS failed) |
| 500 | 5000 | Generic server error |

---

## 6. Test Scripts

This directory provides three simple test scripts for quick testing of each service:

| Script | Description |
|--------|-------------|
| `test_asr.py` | ASR service single request test (supports native and OpenAI interfaces) |
| `test_tts.py` | TTS service single request test (supports native and OpenAI interfaces) |
| `test_livekit.py` | LiveKit service connection test |

### Usage Examples

#### Test ASR

```bash
# Native interface
python test_asr.py --audio test.wav

# OpenAI compatible interface
python test_asr.py --audio test.wav --openai
python test_asr.py --audio test.wav --openai --format verbose_json

# Use OpenAI SDK (requires openai library)
python test_asr.py --audio test.wav --openai-sdk
```

#### Test TTS

```bash
# Native interface
python test_tts.py --text "Hello, world"
python test_tts.py --text "Hello" --voice liudao --stream

# OpenAI compatible interface
python test_tts.py --text "Hello, world" --openai
python test_tts.py --text "Hello" --openai --voice Filrty --format mp3

# Use OpenAI SDK (requires openai library)
python test_tts.py --text "Hello" --openai-sdk

# List available voices
python test_tts.py --list-voices          # Native interface
python test_tts.py --list-voices --openai  # OpenAI interface
```

#### Test LiveKit

```bash
# Default wait 30 seconds, received audio saved to output_livekit.wav
python test_livekit.py --room test-room
python test_livekit.py --audio input.wav --room test-room --output response.wav
```

### Test Script Parameter Description

#### test_asr.py

| Parameter | Description |
|-----------|-------------|
| `--audio` | Local audio file path |
| `--url` | Remote audio URL (native interface only) |
| `--openai` | Use OpenAI compatible interface |
| `--openai-sdk` | Use OpenAI SDK |
| `--format` | Response format: json, text, srt, verbose_json, vtt |
| `--language` | Language code (e.g., zh, en) |
| `--api-key` | API Key (if authentication is enabled) |

#### test_tts.py

| Parameter | Description |
|-----------|-------------|
| `--text` | Text to synthesize |
| `--voice` | Voice name |
| `--stream` | Streaming output (native interface only) |
| `--openai` | Use OpenAI compatible interface |
| `--openai-sdk` | Use OpenAI SDK |
| `--format` | Output format: wav, mp3, pcm, opus, aac, flac (default wav) |
| `--list-voices` | List available voices |
| `--api-key` | API Key (if authentication is enabled) |

---

## Appendix

### Audio Format Reference

| Service | Sample Rate | Channels | Bit Depth | Format |
|---------|-------------|----------|-----------|--------|
| ASR Input | Any | Any | Any | WAV/MP3/OGG/FLAC |
| TTS Output | 44100 Hz | 1 | 16-bit | PCM/WAV |
| LiveKit Input | 16000 Hz | 1 | 16-bit | PCM |
| LiveKit Output | 44100 Hz | 1 | 16-bit | PCM |

### Environment Variables

| Variable | Default Value | Description |
|----------|---------------|-------------|
| `ASR_BASE_URL` | `http://localhost:1203` | ASR service address |
| `TTS_BASE_URL` | `http://localhost:1204` | TTS service address |
| `LIVEKIT_URL` | `ws://3.114.138.123:8003` | LiveKit service address |
| `LIVEKIT_API_KEY` | `devkey` | LiveKit API Key |
| `LIVEKIT_API_SECRET` | `secret` | LiveKit API Secret |
| `SAMPLE_RATE` | `44100` | TTS output sample rate |
