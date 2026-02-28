import io
import logging
import math
import re
import signal
import struct
import subprocess
import threading
import time
import wave

from . import config
from .asr import ASRClient
from .llm import LLMClient
from .tts import TTSClient
from .audio import AudioPlayer
from .capture import MicrophoneCapture

log = logging.getLogger(__name__)


class VoiceAssistantDaemon:
    """
    Orchestrates the full pipeline:
      Mic → Wake Word → VAD → ASR → LLM → TTS → Speaker
    """

    def __init__(self):
        self._asr    = ASRClient()
        self._llm    = LLMClient()
        self._tts    = TTSClient()
        self._player = AudioPlayer()
        self._mic    = MicrophoneCapture(
            on_utterance=self._handle_utterance,
            on_wake_word=self._handle_wake_word,
        )
        self._busy    = threading.Event()
        self._stop    = threading.Event()
        self._aec_active = self._detect_aec()

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        """Strip markdown, emojis, and symbols so TTS receives plain speech text."""
        # Remove markdown bold/italic markers
        text = re.sub(r'\*{1,3}', '', text)
        # Remove markdown headers (## Header)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove markdown bullet points (- item, * item)
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
        # Remove markdown links [text](url) → text
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
        # Remove inline code backticks
        text = re.sub(r'`([^`]*)`', r'\1', text)
        # Remove emojis (Unicode emoji ranges)
        text = re.sub(
            r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0000FE00-\U0000FE0F'
            r'\U0000200D\U00002702-\U000027B0\U0001FA00-\U0001FA6F'
            r'\U0001FA70-\U0001FAFF]+', '', text)
        # Remove remaining special symbols but keep basic punctuation
        text = re.sub(r'[^\w\s.,!?;:\'\"-/()]+', '', text)
        # Collapse multiple blank lines / whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        return text.strip()

    @staticmethod
    def _detect_aec() -> bool:
        """Auto-detect whether PulseAudio AEC (module-echo-cancel) is active."""
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True, text=True, timeout=3,
            )
            active = "module-echo-cancel" in result.stdout
            if active:
                log.info("AEC detected: PulseAudio module-echo-cancel is loaded.")
            else:
                log.info("AEC not detected: PulseAudio module-echo-cancel is not loaded.")
            return active
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.info("AEC not detected: pactl not available.")
            return False

    # ── wake word callback (runs in capture thread) ──────────────────────────

    def _handle_wake_word(self):
        # Play beep + ack phrase in a background thread.
        # Mic is muted during playback to prevent the ack audio from being
        # captured as the user's utterance, then resumes LISTENING.
        log.info("Wake word acknowledged.")
        threading.Thread(target=self._play_ack, daemon=True).start()

    def _play_ack(self):
        try:
            # Mute mic during ack playback so the audio doesn't
            # get captured by VAD as the user's utterance.
            self._mic.mute()

            # 1. "Yes sir" via TTS.
            if config.WAKE_WORD_ACK_PHRASE:
                audio = self._tts.synthesize(config.WAKE_WORD_ACK_PHRASE, timeout=3)
                if audio:
                    self._player.play(audio)

            # 2. Beep after "Yes sir" — signal "speak now".
            self._player.play(self._generate_beep_wav(freq=1200, duration_ms=100))
        except Exception as exc:
            log.warning("Ack playback failed: %s", exc)
        finally:
            # Resume LISTENING (not IDLE) so user can speak their command.
            self._mic.resume_listening()

    @staticmethod
    def _generate_beep_wav(freq: int = 880, duration_ms: int = 200, volume: float = 0.5) -> bytes:
        """Generate a simple sine-wave beep as WAV bytes — no TTS backend needed."""
        sample_rate = 44100
        n_samples   = int(sample_rate * duration_ms / 1000)
        samples     = [
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

    # ── utterance callback (runs in capture thread) ──────────────────────────

    def _handle_utterance(self, wav_bytes: bytes):
        if self._busy.is_set():
            log.debug("Still playing; utterance dropped.")
            return
        threading.Thread(target=self._pipeline, args=(wav_bytes,), daemon=True).start()

    _ERROR_PHRASE = "I'm sorry, my system is having a problem. Can you ask again?"

    def _pipeline(self, wav_bytes: bytes):
        self._busy.set()
        try:
            # 1. ASR
            log.info("ASR: transcribing (%d bytes) …", len(wav_bytes))
            t0 = time.time()
            user_text = self._asr.transcribe(wav_bytes)
            log.info("ASR: completed in %.0f ms", (time.time() - t0) * 1000)
            if not user_text:
                log.info("ASR: empty result, skipping.")
                self._speak_error("ASR returned empty result")
                return
            log.info("User said: %s", user_text)

            # 2. LLM
            log.info("LLM: generating reply …")
            t0 = time.time()
            reply = self._llm.chat(user_text)
            log.info("LLM: completed in %.0f ms", (time.time() - t0) * 1000)
            if not reply:
                log.warning("LLM: no reply.")
                self._speak_error("LLM returned no reply")
                return
            log.info("Assistant: %s", reply)

            # Clean reply for TTS — strip markdown, emojis, symbols.
            tts_text = self._clean_for_tts(reply)
            log.debug("TTS text: %s", tts_text)

            # 3+4. TTS stream → play simultaneously.
            # Audio starts on the first chunk so the user hears the response
            # before TTS finishes generating — no full-generation wait.
            log.info(
                "TTS: streaming + playing … (mute=%s aec=%s)",
                config.MIC_MUTE_DURING_PLAYBACK,
                self._aec_active,
            )
            if config.MIC_MUTE_DURING_PLAYBACK:
                self._mic.mute()
            try:
                self._player.play_stream(self._tts.synthesize_stream(tts_text))
            finally:
                # Low beep — signal "reply done, say hey jarvis again".
                self._player.play(self._generate_beep_wav(freq=660, duration_ms=150))
                log.info("TTS playback finished, resuming mic.")
                if config.MIC_MUTE_DURING_PLAYBACK:
                    self._mic.unmute()
        except Exception as exc:
            log.error("Pipeline error: %s", exc)
            self._speak_error(str(exc))
        finally:
            self._busy.clear()

    def _speak_error(self, reason: str):
        """Speak an apology so the user knows something went wrong."""
        log.info("Speaking error message (reason: %s)", reason)
        try:
            if config.MIC_MUTE_DURING_PLAYBACK:
                self._mic.mute()
            audio = self._tts.synthesize(self._ERROR_PHRASE, timeout=5)
            if audio:
                self._player.play(audio)
            self._player.play(self._generate_beep_wav(freq=660, duration_ms=150))
        except Exception as exc:
            log.warning("Could not speak error message: %s", exc)
            # Fall back to just a beep if TTS is also down.
            try:
                self._player.play(self._generate_beep_wav(freq=440, duration_ms=500))
            except Exception:
                pass
        finally:
            if config.MIC_MUTE_DURING_PLAYBACK:
                self._mic.unmute()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        log.info("Voice Assistant starting …")
        self._mic.start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal_handler)

        log.info("Listening. Press Ctrl-C to stop.")
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        finally:
            self._shutdown()

    def _signal_handler(self, signum, frame):
        log.info("Received signal %d, shutting down …", signum)
        self._stop.set()

    def _shutdown(self):
        log.info("Shutting down …")
        self._mic.stop()
        self._player.terminate()
        log.info("Voice Assistant stopped.")
