import array
import io
import json
import unittest
import urllib.error
import wave
from unittest import mock

import remote_models as models


def pcm(frames, channels=2):
    return array.array('h', [0] * (frames * channels)).tobytes()


def wav_bytes(rate=48000, channels=1, sample_width=2, frames=480):
    output = io.BytesIO()
    with wave.open(output, 'wb') as sound:
        sound.setparams((channels, sample_width, rate, 0, 'NONE', 'not compressed'))
        sound.writeframes(bytes(frames * channels * sample_width))
    return output.getvalue()


class RequestControlTests(unittest.TestCase):
    def test_cancellation_is_checked_without_retrying(self):
        control = models.RequestControl(lambda: True)
        with self.assertRaises(models.RemoteCancelled):
            control.check()


class AudioValidationTests(unittest.TestCase):
    def test_phrase_is_bounded_nonempty_stereo_s16le_48k(self):
        self.assertEqual(models.validate_phrase_pcm(pcm(480)), pcm(480))
        for invalid in (b'', b'\x00', pcm(480) + b'\x00', pcm(480001)):
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                models.validate_phrase_pcm(invalid)

    def test_raw_mono_is_duplicated_and_stereo_is_unchanged(self):
        mono = array.array('h', [1, -2, 3]).tobytes()
        stereo = array.array('h', [1, 1, -2, -2, 3, 3]).tobytes()
        self.assertEqual(models.decode_tts_audio(mono, channels=1), stereo)
        self.assertEqual(models.decode_tts_audio(stereo, channels=2), stereo)

    def test_pcm_wav_is_unwrapped_and_mono_is_duplicated(self):
        mono = array.array('h', [5, -5]).tobytes()
        encoded = wav_bytes(frames=2)
        # Replace the two zero samples with deterministic non-zero samples.
        encoded = encoded[:-4] + mono
        expected = array.array('h', [5, 5, -5, -5]).tobytes()
        self.assertEqual(models.decode_tts_audio(encoded), expected)
        self.assertEqual(models.decode_tts_audio(wav_bytes(channels=2)), pcm(480))

    def test_rejects_wrong_wav_format_channel_count_and_partial_frames(self):
        for encoded in (wav_bytes(rate=44100), wav_bytes(sample_width=1),
                        wav_bytes(channels=3)):
            with self.subTest(encoded=encoded[:12]), self.assertRaises(ValueError):
                models.decode_tts_audio(encoded)
        with self.assertRaises(ValueError):
            models.decode_tts_audio(b'\x00', channels=1)
        with self.assertRaises(ValueError):
            models.decode_tts_audio(b'', channels=2)

    def test_rejects_oversized_or_overlong_audio(self):
        with self.assertRaises(ValueError):
            models.decode_tts_audio(bytes(models.MAX_AUDIO_BYTES + 1), channels=1)
        with self.assertRaises(ValueError):
            models.decode_tts_audio(pcm(48000 * 31, channels=1), channels=1)
        with self.assertRaises(ValueError):
            models.decode_tts_audio(wav_bytes(frames=48000 * 31))


class TextValidationTests(unittest.TestCase):
    def test_transcript_and_reply_are_trimmed_and_bounded(self):
        self.assertEqual(models.validate_transcript(' 你好 '), '你好')
        self.assertEqual(models.validate_reply_text(' 回答 '), '回答')
        for function, limit in ((models.validate_transcript, models.MAX_TRANSCRIPT_CHARS),
                                (models.validate_reply_text, models.MAX_REPLY_CHARS)):
            with self.subTest(function=function.__name__, kind='empty'):
                with self.assertRaises(ValueError):
                    function('  ')
            with self.subTest(function=function.__name__, kind='long'):
                with self.assertRaises(ValueError):
                    function('字' * (limit + 1))


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def read(self, size=-1):
        block, self.payload = self.payload[:size], self.payload[size:]
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def chat_payload(text):
    return json.dumps({'choices': [{'message': {'role': 'assistant', 'content': text}}]}).encode()


class OpenAIChatLlmTests(unittest.TestCase):
    def setUp(self):
        self.llm = models.OpenAIChatLlm(
            'https://ollama.example.test/v1', 'qwen3-vl:4b-instruct', 'sk-test')
        self.messages = [{'role': 'system', 'content': '简短回答'},
                         {'role': 'user', 'content': '你好'}]

    def test_request_matches_openai_chat_contract(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.headers)
            captured['timeout'] = timeout
            captured['body'] = json.loads(request.data)
            return FakeResponse(chat_payload(' 你好鸭 '))

        with mock.patch('remote_models.urllib.request.urlopen', fake_urlopen):
            self.assertEqual(self.llm.complete(self.messages, timeout=7), '你好鸭')
        self.assertEqual(captured['url'], 'https://ollama.example.test/v1/chat/completions')
        self.assertEqual(captured['headers']['Authorization'], 'Bearer sk-test')
        self.assertEqual(captured['body']['model'], 'qwen3-vl:4b-instruct')
        self.assertEqual(captured['body']['messages'], self.messages)
        self.assertEqual(captured['body']['max_tokens'], models.MAX_REPLY_CHARS)
        self.assertIs(captured['body']['stream'], False)
        self.assertEqual(captured['timeout'], 7)

    def test_rejects_empty_content_missing_choices_and_non_json(self):
        for raw in (chat_payload('   '), b'{}', b'{"choices":[]}', b'not json'):
            with self.subTest(raw=raw[:16]):
                with mock.patch('remote_models.urllib.request.urlopen',
                                lambda *a, **k: FakeResponse(raw)):
                    with self.assertRaises(models.RemoteProviderError):
                        self.llm.complete(self.messages)

    def test_response_over_bound_is_rejected(self):
        llm = models.OpenAIChatLlm('https://ollama.example.test/v1', 'm', 'sk-test',
                                   max_response_bytes=16)
        with mock.patch('remote_models.urllib.request.urlopen',
                        lambda *a, **k: FakeResponse(chat_payload('x' * 64))):
            with self.assertRaises(models.RemoteProviderError):
                llm.complete(self.messages)

    def test_http_error_does_not_echo_credentials(self):
        error = urllib.error.HTTPError('https://ollama.example.test/v1/chat/completions',
                                       401, 'unauthorized', {}, None)
        with mock.patch('remote_models.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(models.RemoteProviderError) as raised:
                self.llm.complete(self.messages)
        self.assertNotIn('sk-test', str(raised.exception))
        self.assertIn('401', str(raised.exception))

    def test_transport_error_is_wrapped_without_url(self):
        error = urllib.error.URLError('https://ollama.example.test failed')
        with mock.patch('remote_models.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(models.RemoteProviderError) as raised:
                self.llm.complete(self.messages)
        self.assertNotIn('ollama.example.test', str(raised.exception))

    def test_cancellation_is_checked_before_and_during_reading(self):
        calls = []

        def cancelled():
            calls.append(1)
            return len(calls) > 2

        with mock.patch('remote_models.urllib.request.urlopen',
                        lambda *a, **k: FakeResponse(chat_payload('你好'))):
            with self.assertRaises(models.RemoteCancelled):
                self.llm.complete(self.messages, cancelled)
        self.assertGreater(len(calls), 2)

        with mock.patch('remote_models.urllib.request.urlopen') as urlopen:
            with self.assertRaises(models.RemoteCancelled):
                self.llm.complete(self.messages, lambda: True)
        urlopen.assert_not_called()

    def test_endpoint_and_credential_validation(self):
        for base_url in ('http://ollama.example.test/v1', 'ftp://host/v1', '',
                         'https://user:pw@host/v1', 'https://host/v1?token=x'):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                models.OpenAIChatLlm(base_url, 'm', 'sk-test')
        models.OpenAIChatLlm('http://127.0.0.1:11434/v1', 'm', 'sk-test')
        models.OpenAIChatLlm('http://localhost:11434/v1/', 'm', 'sk-test')
        with self.assertRaises(ValueError):
            models.OpenAIChatLlm('https://host/v1', 'm', '  ')
        with self.assertRaises(ValueError):
            models.OpenAIChatLlm('https://host/v1', 'm', 'sk-test', max_tokens=0)

    def test_invalid_messages_are_rejected_before_network(self):
        for messages in ([], [{'role': 'tool', 'content': 'x'}],
                         [{'role': 'user', 'content': 1}], 'not a list'):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                self.llm.complete(messages)


class ResampleTests(unittest.TestCase):
    def test_one_second_of_22050_becomes_48000_frames(self):
        mono = pcm(22050, channels=1)
        self.assertEqual(len(models.resample_pcm(mono, 22050)), 48000 * 2)

    def test_endpoints_are_preserved_and_rate_match_is_identity(self):
        samples = array.array('h', [0, 1000, -1000, 500]).tobytes()
        output = models.resample_pcm(samples, 22050)
        self.assertEqual(len(output), 2 * round(4 * 48000 / 22050))
        self.assertEqual(output[:2], samples[:2])
        self.assertEqual(output[-2:], samples[-2:])
        self.assertEqual(models.resample_pcm(samples, 48000), samples)

    def test_invalid_input_is_rejected(self):
        mono = pcm(4, channels=1)
        for data, rate in ((b'', 22050), (b'\x00', 22050), (mono, 1000), (mono, 96000)):
            with self.subTest(rate=rate, size=len(data)), self.assertRaises(ValueError):
                models.resample_pcm(data, rate)


class HttpTtsTests(unittest.TestCase):
    def setUp(self):
        self.tts = models.HttpTts('https://voice.example.test', 'sk-tts')

    def test_request_and_resample_match_the_service_contract(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.headers)
            captured['body'] = json.loads(request.data)
            captured['timeout'] = timeout
            return FakeResponse(pcm(22050, channels=1), {'X-Sample-Rate': '22050'})

        with mock.patch('remote_models.urllib.request.urlopen', fake_urlopen):
            audio = self.tts.synthesize(' 你好鸭 ', timeout=9)
        self.assertEqual(captured['url'], 'https://voice.example.test/tts')
        self.assertEqual(captured['headers']['Authorization'], 'Bearer sk-tts')
        self.assertEqual(captured['body'],
                         {'text': '你好鸭', 'format': 'pcm_s16le', 'speed': 1.0})
        self.assertEqual(captured['timeout'], 9)
        self.assertEqual(len(audio), 48000 * 2)

    def test_voice_and_speed_are_sent_and_missing_rate_falls_back(self):
        tts = models.HttpTts('https://voice.example.test/', 'sk-tts', voice='default', speed=1.2)
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['body'] = json.loads(request.data)
            return FakeResponse(pcm(2205, channels=1))

        with mock.patch('remote_models.urllib.request.urlopen', fake_urlopen):
            audio = tts.synthesize('好')
        self.assertEqual(captured['url'], 'https://voice.example.test/tts')
        self.assertEqual(captured['body']['voice'], 'default')
        self.assertEqual(captured['body']['speed'], 1.2)
        self.assertEqual(len(audio), 4800 * 2)

    def test_failures_are_bounded_and_never_echo_credentials(self):
        error = urllib.error.HTTPError('https://voice.example.test/tts', 503, 'busy', {}, None)
        with mock.patch('remote_models.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(models.RemoteProviderError) as raised:
                self.tts.synthesize('好')
        self.assertIn('503', str(raised.exception))
        self.assertNotIn('sk-tts', str(raised.exception))

        with mock.patch('remote_models.urllib.request.urlopen',
                        lambda *a, **k: FakeResponse(b'x' * 64)):
            with self.assertRaises(models.RemoteProviderError):
                models.HttpTts('https://voice.example.test', 'sk-tts',
                               max_audio_bytes=8).synthesize('好')

        with mock.patch('remote_models.urllib.request.urlopen',
                        lambda *a, **k: FakeResponse(wav_bytes())):
            with self.assertRaises(models.RemoteProviderError):
                self.tts.synthesize('好')

    def test_empty_text_cancellation_and_configuration_are_rejected(self):
        with self.assertRaises(ValueError):
            self.tts.synthesize('  ')
        with mock.patch('remote_models.urllib.request.urlopen') as urlopen:
            with self.assertRaises(models.RemoteCancelled):
                self.tts.synthesize('好', lambda: True)
        urlopen.assert_not_called()
        for arguments in (('https://voice.example.test', ' '),
                          ('http://voice.example.test', 'sk-tts'),
                          ('https://voice.example.test', 'sk-tts', ' ', 1.0),
                          ('https://voice.example.test', 'sk-tts', None, 3.0),
                          ('https://user:pw@voice.example.test', 'sk-tts')):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                models.HttpTts(*arguments)
        models.HttpTts('http://127.0.0.1:1181', 'sk-tts')


class HttpAsrTests(unittest.TestCase):
    def setUp(self):
        self.asr = models.HttpAsr('https://voice.example.test', 'sk-asr')

    def test_phrase_is_posted_unchanged_with_the_audio_headers(self):
        phrase = pcm(480)
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['headers'] = dict(request.headers)
            captured['body'] = request.data
            captured['timeout'] = timeout
            return FakeResponse(json.dumps({'text': ' 小鸭小鸭 ', 'rtc': 0.4}).encode())

        with mock.patch('remote_models.urllib.request.urlopen', fake_urlopen):
            text = self.asr.transcribe(phrase, timeout=7)
        self.assertEqual(captured['url'], 'https://voice.example.test/asr')
        self.assertEqual(captured['body'], phrase)
        self.assertEqual(captured['headers']['Content-type'], 'audio/L16')
        self.assertEqual(captured['headers']['X-sample-rate'], '48000')
        self.assertEqual(captured['headers']['X-channels'], '2')
        self.assertEqual(captured['headers']['Authorization'], 'Bearer sk-asr')
        self.assertEqual(captured['timeout'], 7)
        self.assertEqual(text, ' 小鸭小鸭 ')

    def test_malformed_or_oversized_responses_are_rejected(self):
        for payload in (b'not json', b'[]', b'{"text": 3}', b'{}'):
            with self.subTest(payload=payload), \
                    mock.patch('remote_models.urllib.request.urlopen',
                               lambda *a, **k: FakeResponse(payload)):
                with self.assertRaises(models.RemoteProviderError):
                    self.asr.transcribe(pcm(4))
        with mock.patch('remote_models.urllib.request.urlopen',
                        lambda *a, **k: FakeResponse(b'x' * 64)):
            with self.assertRaises(models.RemoteProviderError):
                models.HttpAsr('https://voice.example.test', 'sk-asr',
                               max_response_bytes=8).transcribe(pcm(4))

    def test_http_failure_does_not_echo_credentials(self):
        error = urllib.error.HTTPError('https://voice.example.test/asr', 503, 'busy', {}, None)
        with mock.patch('remote_models.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(models.RemoteProviderError) as raised:
                self.asr.transcribe(pcm(4))
        self.assertIn('503', str(raised.exception))
        self.assertNotIn('sk-asr', str(raised.exception))

    def test_cancellation_and_bad_phrases_are_rejected_before_network(self):
        with mock.patch('remote_models.urllib.request.urlopen') as urlopen:
            with self.assertRaises(models.RemoteCancelled):
                self.asr.transcribe(pcm(4), lambda: True)
            urlopen.assert_not_called()
        for phrase in (b'', b'\x00', pcm(4) + b'\x00', pcm(480001)):
            with self.subTest(size=len(phrase)), self.assertRaises(ValueError):
                self.asr.transcribe(phrase)

    def test_configuration_is_validated(self):
        for arguments in (('https://voice.example.test', ' '),
                          ('http://voice.example.test', 'sk-asr'),
                          ('https://voice.example.test', 'sk-asr', 16000),
                          ('https://voice.example.test', 'sk-asr', 48000, 3)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                models.HttpAsr(*arguments)
        models.HttpAsr('http://127.0.0.1:1181', 'sk-asr')


if __name__ == '__main__':
    unittest.main()
