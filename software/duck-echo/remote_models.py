"""Provider-neutral contracts and strict audio/text bounds for remote dialogue.

The LLM adapter speaks the OpenAI-compatible chat-completions contract, the TTS
adapter speaks the project's /tts contract, and the ASR adapter posts the
captured phrase to the same service's /asr contract; all three are verified
against the project endpoints. The on-board Vosk recognizer remains available
as an offline fallback.
"""
import array
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave

RATE = 48000
TTS_RATE = 22050
MAX_PHRASE_SECONDS = 10
MAX_TRANSCRIPT_CHARS = 1000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 12000
TARGET_REPLY_CHARS = 120
MAX_REPLY_CHARS = 200
MAX_JSON_BYTES = 64 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 30
MAX_REQUEST_BYTES = 128 * 1024
LOOPBACK_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost'})


class RemoteCancelled(Exception):
    """The local generation was cancelled; this is not a provider failure."""


class RemoteProviderError(Exception):
    """Provider failure carrying no URL, credential, or payload material."""


class RequestControl:
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def check(self):
        if self.cancelled():
            raise RemoteCancelled()


def _bounded_text(value, limit, label):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text')
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError(f'{label} must contain 1..{limit} characters')
    return value


def validate_transcript(text):
    return _bounded_text(text, MAX_TRANSCRIPT_CHARS, 'transcript')


def validate_reply_text(text):
    return _bounded_text(text, MAX_REPLY_CHARS, 'reply')


def validate_phrase_pcm(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError('phrase must be PCM bytes')
    data = bytes(data)
    if not data or len(data) % 4 or len(data) > MAX_PHRASE_SECONDS * RATE * 4:
        raise ValueError('phrase must be nonempty, frame-aligned, and at most 10 seconds')
    return data


def _stereo_from_mono(data):
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    stereo = array.array('h', (sample for sample in samples for _ in range(2)))
    if sys.byteorder != 'little':
        stereo.byteswap()
    return stereo.tobytes()


def decode_tts_audio(data, channels=2):
    """Return 48 kHz S16_LE stereo PCM; accept raw mono/stereo or PCM WAV."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError('TTS audio must be bytes')
    data = bytes(data)
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError('TTS audio exceeds the 8 MiB bound')
    if data.startswith(b'RIFF'):
        try:
            with wave.open(io.BytesIO(data), 'rb') as sound:
                channels, width, rate, frames = sound.getparams()[:4]
                if rate != RATE or width != 2 or channels not in (1, 2):
                    raise ValueError('TTS WAV must be 48 kHz S16_LE mono/stereo')
                if frames > MAX_AUDIO_SECONDS * RATE:
                    raise ValueError('TTS audio exceeds 30 seconds')
                data = sound.readframes(frames)
        except (wave.Error, EOFError) as error:
            raise ValueError('invalid TTS PCM WAV') from error
    if channels not in (1, 2) or len(data) % (2 * channels):
        raise ValueError('TTS raw audio must be frame-aligned mono/stereo S16_LE')
    if not data or len(data) // (2 * channels) > MAX_AUDIO_SECONDS * RATE:
        raise ValueError('TTS audio exceeds 30 seconds')
    return _stereo_from_mono(data) if channels == 1 else data


def _service_endpoint(base_url, path):
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError('provider base_url is required')
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('provider base_url must be an absolute http(s) URL')
    if parsed.scheme == 'http' and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError('plain HTTP is only allowed for loopback provider endpoints')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('provider base_url must not carry credentials, query, or fragment')
    return base_url.strip().rstrip('/') + path


def _chat_endpoint(base_url):
    return _service_endpoint(base_url, '/chat/completions')


def _response_rate(value):
    """Trust only a plausible X-Sample-Rate; the service documents 22050 Hz."""
    try:
        rate = int(str(value).strip())
    except (TypeError, ValueError):
        return TTS_RATE
    return rate if 8000 <= rate <= RATE else TTS_RATE


def resample_pcm(data, source_rate, target_rate=RATE):
    """Linearly resample mono S16_LE PCM into target_rate mono S16_LE."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError('PCM must be bytes')
    data = bytes(data)
    if not data or len(data) % 2:
        raise ValueError('PCM must be nonempty frame-aligned S16_LE')
    if not 8000 <= source_rate <= RATE or not 8000 <= target_rate <= RATE:
        raise ValueError('sample rates must be 8000..48000')
    if source_rate == target_rate:
        return data
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    source_frames = len(samples)
    target_frames = max(1, round(source_frames * target_rate / source_rate))
    last = source_frames - 1
    output = array.array('h', bytes(2 * target_frames))
    step = (source_rate << 16) // target_rate
    position = 0
    for index in range(target_frames):
        source = position >> 16
        if source >= last:
            output[index] = samples[last]
        else:
            weight = position & 0xFFFF
            output[index] = samples[source] + (
                (samples[source + 1] - samples[source]) * weight >> 16)
        position += step
    if sys.byteorder != 'little':
        output.byteswap()
    return output.tobytes()


class OpenAIChatLlm:
    """One-shot OpenAI-compatible chat completion with strict bounds.

    Cancellation is checked before sending, between 8 KiB response reads, and
    after the response arrives. Requests are never retried, so a failed call
    cannot produce a duplicated spoken answer.
    """

    def __init__(self, base_url, model, api_key, max_tokens=MAX_REPLY_CHARS,
                 temperature=0.3, max_response_bytes=MAX_JSON_BYTES):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('LLM api_key is required')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('LLM model is required')
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_REPLY_CHARS:
            raise ValueError(f'LLM max_tokens must be 1..{MAX_REPLY_CHARS}')
        if not 0 <= float(temperature) <= 2:
            raise ValueError('LLM temperature must be 0..2')
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError('LLM response bound must be positive')
        self.url = _chat_endpoint(base_url)
        self.model = model.strip()
        self._api_key = api_key.strip()
        self.max_tokens = max_tokens
        self.temperature = float(temperature)
        self.max_response_bytes = max_response_bytes

    def complete(self, messages, cancelled=lambda: False, timeout=20):
        control = RequestControl(cancelled)
        control.check()
        if not isinstance(messages, list) or not messages:
            raise ValueError('LLM messages must be a nonempty list')
        for message in messages:
            if (not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant')
                    or not isinstance(message.get('content'), str)):
                raise ValueError('LLM messages must carry system/user/assistant text content')
        body = json.dumps({
            'model': self.model, 'messages': messages, 'stream': False,
            'max_tokens': self.max_tokens, 'temperature': self.temperature,
        }, ensure_ascii=False).encode('utf-8')
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError(f'LLM request exceeds {MAX_REQUEST_BYTES} bytes')
        request = urllib.request.Request(self.url, data=body, headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {self._api_key}',
        })
        control.check()
        chunks = []
        size = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                while True:
                    control.check()
                    block = response.read(8192)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_response_bytes:
                        raise RemoteProviderError('LLM response exceeds the 64 KiB bound')
                    chunks.append(block)
        except urllib.error.HTTPError as error:
            raise RemoteProviderError(f'LLM HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RemoteProviderError(f'LLM transport {type(error).__name__}') from None
        control.check()
        return self._parse(b''.join(chunks))

    @staticmethod
    def _parse(raw):
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteProviderError('LLM response is not JSON') from None
        choices = data.get('choices') if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RemoteProviderError('LLM response has no choices')
        message = choices[0].get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise RemoteProviderError('LLM response has empty content')
        return content.strip()


class HttpTts:
    """POST /tts and return 48 kHz mono S16_LE PCM for the playback path.

    The service answers with 22050 Hz mono raw PCM, so every reply is resampled
    here; callers must still pass the result through decode_tts_audio for the
    stereo duplication and duration bounds.
    """

    def __init__(self, base_url, api_key, voice=None, speed=1.0,
                 max_audio_bytes=MAX_AUDIO_BYTES):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('TTS api_key is required')
        if voice is not None and (not isinstance(voice, str) or not voice.strip()):
            raise ValueError('TTS voice must be nonempty text when set')
        if not 0.5 <= float(speed) <= 2.0:
            raise ValueError('TTS speed must be 0.5..2.0')
        if not isinstance(max_audio_bytes, int) or max_audio_bytes <= 0:
            raise ValueError('TTS audio bound must be positive')
        self.url = _service_endpoint(base_url, '/tts')
        self._api_key = api_key.strip()
        self.voice = voice.strip() if voice else None
        self.speed = float(speed)
        self.max_audio_bytes = max_audio_bytes

    def synthesize(self, text, cancelled=lambda: False, timeout=20):
        control = RequestControl(cancelled)
        text = validate_reply_text(text)
        control.check()
        payload = {'text': text, 'format': 'pcm_s16le', 'speed': self.speed}
        if self.voice is not None:
            payload['voice'] = self.voice
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.url, data=body, headers={
            'Content-Type': 'application/json',
            'Accept': 'audio/L16',
            'Authorization': f'Bearer {self._api_key}',
        })
        control.check()
        chunks = []
        size = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                rate = _response_rate(response.headers.get('X-Sample-Rate'))
                while True:
                    control.check()
                    block = response.read(8192)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_audio_bytes:
                        raise RemoteProviderError('TTS audio exceeds the 8 MiB bound')
                    chunks.append(block)
        except urllib.error.HTTPError as error:
            raise RemoteProviderError(f'TTS HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RemoteProviderError(f'TTS transport {type(error).__name__}') from None
        control.check()
        audio = b''.join(chunks)
        if audio.startswith(b'RIFF'):
            raise RemoteProviderError('TTS returned WAV for a raw PCM request')
        return resample_pcm(audio, rate)


class HttpAsr:
    """POST the captured phrase to /asr and return the transcript text.

    The phrase is already the validate_phrase_pcm contract (48 kHz stereo
    S16_LE, frame-aligned, at most 10 seconds), so it is posted unchanged with
    X-Sample-Rate/X-Channels; the service downmixes and resamples to 16 kHz.
    """

    def __init__(self, base_url, api_key, rate=RATE, channels=2,
                 max_response_bytes=MAX_JSON_BYTES):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('ASR api_key is required')
        if rate != RATE or channels not in (1, 2):
            raise ValueError('ASR accepts 48 kHz mono/stereo S16_LE only')
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError('ASR response bound must be positive')
        self.url = _service_endpoint(base_url, '/asr')
        self._api_key = api_key.strip()
        self.rate, self.channels = rate, channels
        self.max_response_bytes = max_response_bytes

    def transcribe(self, phrase, cancelled=lambda: False, timeout=20):
        control = RequestControl(cancelled)
        phrase = validate_phrase_pcm(phrase)
        control.check()
        request = urllib.request.Request(self.url, data=phrase, headers={
            'Content-Type': 'audio/L16',
            'Accept': 'application/json',
            'X-Sample-Rate': str(self.rate),
            'X-Channels': str(self.channels),
            'Authorization': f'Bearer {self._api_key}',
        })
        control.check()
        chunks = []
        size = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                while True:
                    control.check()
                    block = response.read(8192)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_response_bytes:
                        raise RemoteProviderError('ASR response exceeds the 64 KiB bound')
                    chunks.append(block)
        except urllib.error.HTTPError as error:
            raise RemoteProviderError(f'ASR HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RemoteProviderError(f'ASR transport {type(error).__name__}') from None
        control.check()
        try:
            data = json.loads(b''.join(chunks).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RemoteProviderError('ASR response is not JSON') from None
        text = data.get('text') if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise RemoteProviderError('ASR response has no text')
        return text
