"""
LiveKit Voice Assistant Daemon
──────────────────────────────
After wake word detection, connects to a LiveKit room and streams mic audio
to a server-side agent that handles ASR / LLM / TTS.  Agent audio is played
back through the speaker.  Disconnects after an inactivity timeout (no agent
audio for LIVEKIT_INACTIVITY_TIMEOUT_S seconds) and returns to IDLE.

State machine:
  IDLE ──(wake word)──> CONNECTING ──(room joined)──> STREAMING
    ^                                                      |
    |              timeout / disconnect                    |
    +──────────────────────────────────────────────────────+
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import queue
import signal
import struct
import threading
import time
import wave
from typing import Optional

import numpy as np
import pyaudio

from livekit import api, rtc

from . import config
from .audio import AudioPlayer
from .capture import MicrophoneCapture

log = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────
_MIC_SAMPLE_RATE = config.MIC_SAMPLE_RATE        # 16 000 Hz
_MIC_FRAME_SAMPLES = config.MIC_CHUNK_SAMPLES    # 480 (30 ms)
_LK_FRAME_SAMPLES = 320                          # 20 ms @ 16 kHz — LiveKit default


class LiveKitVoiceAssistantDaemon:
    """Orchestrates: Wake Word → LiveKit room → agent audio → speaker."""

    _GREETING = "Hi, I am Jarvis your robot assistant. Please ask me if you have any question."

    def __init__(self):
        self._mic = MicrophoneCapture(
            on_utterance=lambda _wav: None,   # not used in livekit mode
            on_wake_word=self._handle_wake_word,
        )
        self._player = AudioPlayer()
        self._stop_event = threading.Event()

        # Async event loop runs in a background thread.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

        # Mic frames are pushed here by the capture thread (PASSTHROUGH callback)
        # and consumed by an async coroutine that publishes them to LiveKit.
        self._frame_q: queue.Queue[bytes | None] = queue.Queue(maxsize=200)

        # LiveKit objects (created per-session)
        self._room: rtc.Room | None = None
        self._audio_source: rtc.AudioSource | None = None

        # State
        self._state = "IDLE"   # IDLE | CONNECTING | STREAMING
        self._last_agent_audio = 0.0

        # Speaker stream for agent audio (persistent during STREAMING)
        self._spk_pa: pyaudio.PyAudio | None = None
        self._spk_stream: pyaudio.Stream | None = None
        self._spk_lock = threading.Lock()

    # ── async event loop ─────────────────────────────────────────────────────

    def _start_loop(self):
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()
        log.info("Async event loop started.")

    def _run_coro(self, coro):
        """Schedule a coroutine on the background loop; return a concurrent.futures.Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── wake word callback (capture thread) ──────────────────────────────────

    def _handle_wake_word(self):
        log.info("Wake word detected — connecting to LiveKit room.")
        # Play ack in a background thread, then start the LiveKit session.
        threading.Thread(target=self._ack_and_connect, daemon=True).start()

    def _ack_and_connect(self):
        self._mic.mute()
        try:
            from .tts import TTSClient
            tts = TTSClient()
            if config.WAKE_WORD_ACK_PHRASE:
                audio = tts.synthesize(config.WAKE_WORD_ACK_PHRASE, timeout=3)
                if audio:
                    self._player.play(audio)
            self._player.play(self._generate_beep_wav(freq=1200, duration_ms=100))
        except Exception as exc:
            log.warning("Ack playback failed: %s", exc)

        # Start LiveKit session (don't unmute — passthrough will take over).
        self._state = "CONNECTING"
        fut = self._run_coro(self._livekit_session())
        try:
            fut.result()  # block until session ends
        except Exception as exc:
            log.error("LiveKit session error: %s", exc)
            self._speak_error("I couldn't connect to the server. I'll go back to sleep. "
                              "Just say hey Jarvis to wake me up again.")
        finally:
            self._state = "IDLE"
            self._mic.stop_passthrough()
            self._mic.unmute()
            self._close_speaker_stream()
            log.info("Returned to IDLE.")

    # ── LiveKit session ──────────────────────────────────────────────────────

    async def _livekit_session(self):
        # Generate access token with metadata for the server-side agent.
        metadata = json.dumps({
            "llm_url": config.LLM_BASE_URL + "/chat/completions",
            "llm_model": config.LLM_MODEL,
            "llm_token": config.LLM_API_KEY,
            "system_prompt": config.LLM_SYSTEM_PROMPT,
            "greeting": config.WAKE_WORD_ACK_PHRASE,
            "voice": config.TTS_VOICE,
        })
        token = (
            api.AccessToken(
                api_key=config.LIVEKIT_API_KEY,
                api_secret=config.LIVEKIT_API_SECRET,
            )
            .with_identity(config.LIVEKIT_PARTICIPANT_NAME)
            .with_name(config.LIVEKIT_PARTICIPANT_NAME)
            .with_metadata(metadata)
            .with_grants(api.VideoGrants(
                room_join=True,
                room=config.LIVEKIT_ROOM_NAME,
                can_publish=True,
                can_subscribe=True,
            ))
            .to_jwt()
        )

        self._room = rtc.Room()

        # Register event handlers.
        self._room.on("track_subscribed")(self._on_track_subscribed)
        self._room.on("disconnected")(self._on_disconnected)

        log.info("Connecting to LiveKit: %s room=%s", config.LIVEKIT_URL, config.LIVEKIT_ROOM_NAME)
        await self._room.connect(config.LIVEKIT_URL, token)
        log.info("Connected to LiveKit room.")

        # Publish mic audio track.
        self._audio_source = rtc.AudioSource(
            sample_rate=_MIC_SAMPLE_RATE,
            num_channels=1,
        )
        track = rtc.LocalAudioTrack.create_audio_track("microphone", self._audio_source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self._room.local_participant.publish_track(track, options)
        log.info("Mic audio track published.")

        # Switch capture to passthrough — raw mic frames flow to _frame_q.
        self._drain_queue()
        self._mic.resume_listening()        # unmute first
        self._mic.start_passthrough(self._on_raw_frame)

        self._state = "STREAMING"
        self._last_agent_audio = time.monotonic()

        # Run publisher + watchdog concurrently.
        publish_task = asyncio.ensure_future(self._publish_loop())
        watchdog_task = asyncio.ensure_future(self._inactivity_watchdog())

        done, pending = await asyncio.wait(
            [publish_task, watchdog_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        # Disconnect.
        await self._room.disconnect()
        self._room = None
        self._audio_source = None
        log.info("Disconnected from LiveKit room.")

    # ── mic frame callback (capture thread → queue) ──────────────────────────

    def _on_raw_frame(self, frame: bytes):
        try:
            self._frame_q.put_nowait(frame)
        except queue.Full:
            pass  # drop frame if queue is full

    def _drain_queue(self):
        while not self._frame_q.empty():
            try:
                self._frame_q.get_nowait()
            except queue.Empty:
                break

    # ── publish loop (async, runs in background event loop) ──────────────────

    async def _publish_loop(self):
        """Read mic frames from queue, reframe 480→320 samples, push to LiveKit."""
        loop = asyncio.get_event_loop()
        buf = np.array([], dtype=np.int16)

        def _get_frame():
            try:
                return self._frame_q.get(timeout=0.05)
            except queue.Empty:
                return None

        while self._state == "STREAMING":
            # Run blocking queue get in executor to avoid blocking the event loop.
            frame = await loop.run_in_executor(None, _get_frame)
            if frame is None:
                continue

            pcm = np.frombuffer(frame, dtype=np.int16)
            buf = np.concatenate([buf, pcm])

            while len(buf) >= _LK_FRAME_SAMPLES:
                chunk = buf[:_LK_FRAME_SAMPLES]
                buf = buf[_LK_FRAME_SAMPLES:]

                audio_frame = rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=_MIC_SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=_LK_FRAME_SAMPLES,
                )
                await self._audio_source.capture_frame(audio_frame)

    # ── inactivity watchdog ──────────────────────────────────────────────────

    async def _inactivity_watchdog(self):
        timeout = config.LIVEKIT_INACTIVITY_TIMEOUT_S
        while self._state == "STREAMING":
            await asyncio.sleep(2.0)
            elapsed = time.monotonic() - self._last_agent_audio
            if elapsed >= timeout:
                log.info("No agent audio for %ds — disconnecting.", int(elapsed))
                return

    # ── track subscription (agent audio) ─────────────────────────────────────

    def _on_disconnected(self, reason=None):
        log.info("Disconnected from LiveKit room (reason=%s).", reason)
        self._state = "IDLE"

    def _on_track_subscribed(self, track: rtc.Track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        log.info("Subscribed to audio track from %s", participant.identity)
        asyncio.ensure_future(self._receive_agent_audio(track))

    async def _receive_agent_audio(self, track: rtc.Track):
        """Receive agent audio frames and write them to the speaker."""
        # Agent TTS output is 44100 Hz mono 16-bit per API docs.
        stream = rtc.AudioStream(track, sample_rate=44100, num_channels=1)
        self._open_speaker_stream(44100, 1)

        async for event in stream:
            self._last_agent_audio = time.monotonic()
            pcm_data = bytes(event.frame.data)
            with self._spk_lock:
                if self._spk_stream and self._spk_stream.is_active():
                    try:
                        self._spk_stream.write(pcm_data)
                    except Exception as exc:
                        log.warning("Speaker write error: %s", exc)

        await stream.aclose()
        log.info("Agent audio stream ended.")

    # ── speaker stream management ────────────────────────────────────────────

    def _open_speaker_stream(self, sample_rate: int, channels: int):
        with self._spk_lock:
            if self._spk_stream is not None:
                return  # already open
            self._spk_pa = pyaudio.PyAudio()
            open_kwargs = dict(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                output=True,
                frames_per_buffer=1024,
            )
            if config.SPK_DEVICE_INDEX >= 0:
                open_kwargs["output_device_index"] = config.SPK_DEVICE_INDEX
            self._spk_stream = self._spk_pa.open(**open_kwargs)
            log.info("Speaker stream opened (%d Hz, %d ch).", sample_rate, channels)

    def _close_speaker_stream(self):
        with self._spk_lock:
            if self._spk_stream is not None:
                try:
                    self._spk_stream.stop_stream()
                    self._spk_stream.close()
                except Exception:
                    pass
                self._spk_stream = None
            if self._spk_pa is not None:
                try:
                    self._spk_pa.terminate()
                except Exception:
                    pass
                self._spk_pa = None
            log.info("Speaker stream closed.")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _speak_error(self, message: str):
        """Speak an error message via TTS before returning to IDLE."""
        try:
            from .tts import TTSClient
            tts = TTSClient()
            audio = tts.synthesize(message, timeout=5)
            if audio:
                self._player.play(audio)
        except Exception as exc:
            log.warning("Could not speak error message: %s", exc)
        try:
            self._player.play(self._generate_beep_wav(freq=440, duration_ms=500))
        except Exception:
            pass

    @staticmethod
    def _generate_beep_wav(freq: int = 880, duration_ms: int = 200, volume: float = 0.5) -> bytes:
        sample_rate = 44100
        n_samples = int(sample_rate * duration_ms / 1000)
        samples = [
            int(volume * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            for i in range(n_samples)
        ]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"{n_samples}h", *samples))
        return buf.getvalue()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def run(self):
        log.info("Voice Assistant starting (LiveKit mode) …")
        self._start_loop()

        # Play greeting before activating the mic.
        try:
            from .tts import TTSClient
            tts = TTSClient()
            audio = tts.synthesize(self._GREETING, timeout=5)
            if audio:
                self._player.play(audio)
                self._player.play(self._generate_beep_wav(freq=660, duration_ms=150))
                log.info("Greeting played.")
                time.sleep(1)
        except Exception as exc:
            log.warning("Could not play greeting: %s", exc)

        self._mic.start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal_handler)

        log.info("Listening for wake word. Press Ctrl-C to stop.")
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        finally:
            self._shutdown()

    def _signal_handler(self, signum, _frame):
        log.info("Received signal %d, shutting down …", signum)
        self._stop_event.set()

    def _shutdown(self):
        log.info("Shutting down …")
        self._mic.stop()
        self._close_speaker_stream()
        self._player.terminate()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=3)
        log.info("Voice Assistant stopped.")
