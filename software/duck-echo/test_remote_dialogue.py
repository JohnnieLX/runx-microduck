import threading
import unittest

import remote_dialogue as dialogue_module


class FakeASR:
    def __init__(self, transcripts=None, entered=None, release=None, error=None):
        self.transcripts = list(transcripts or ['你好'])
        self.entered, self.release, self.error = entered, release, error
        self.calls = []

    def transcribe(self, phrase, cancelled, timeout):
        self.calls.append((phrase, cancelled, timeout))
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        if self.error:
            raise self.error
        return self.transcripts.pop(0)


class FakeLLM:
    def __init__(self, replies=None, entered=None, release=None, error=None):
        self.replies = list(replies or ['你好，我是鸭子。'])
        self.entered, self.release, self.error = entered, release, error
        self.calls = []

    def complete(self, messages, cancelled, timeout):
        self.calls.append((list(messages), cancelled, timeout))
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        if self.error:
            raise self.error
        return self.replies.pop(0)


class FakeTTS:
    def __init__(self, audio=b'\x00\x00' * 8, entered=None, release=None, error=None):
        self.audio = audio
        self.entered, self.release, self.error = entered, release, error
        self.calls = []

    def synthesize(self, text, cancelled, timeout):
        self.calls.append((text, cancelled, timeout))
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        if self.error:
            raise self.error
        return self.audio


class FakeIntentGateway:
    def __init__(self):
        self.direct_reply = None
        self.model_replies = []
        self.transcripts = []
        self.model_texts = []
        self.cancel_count = 0

    def handle_transcript(self, transcript, allowed):
        self.transcripts.append((transcript, allowed()))
        return self.direct_reply

    def reply(self, text, allowed):
        self.model_texts.append((text, allowed()))
        return self.model_replies.pop(0)

    def cancel(self):
        self.cancel_count += 1


class RemoteDialogueTests(unittest.TestCase):
    def make(self, asr=None, llm=None, tts=None, gateway=None):
        self.asr = asr or FakeASR()
        self.llm = llm or FakeLLM()
        self.tts = tts or FakeTTS()
        self.played = []
        self.dialogue = dialogue_module.RemoteDialogue(
            self.asr, self.llm, self.tts,
            lambda pcm, allowed: self.played.append((pcm, allowed())),
            intent_gateway=gateway, timeout=3)
        self.addCleanup(self.dialogue.close)
        return self.dialogue

    def test_asr_llm_tts_and_reply_playback_run_in_order(self):
        dialogue = self.make()
        result = dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.asr.calls[0][0], b'\x00\x00\x00\x00')
        self.assertEqual(self.asr.calls[0][2], 3)
        messages = self.llm.calls[0][0]
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[-1], {'role': 'user', 'content': '你好'})
        self.assertEqual(self.tts.calls[0][0], '你好，我是鸭子。')
        self.assertEqual(self.played, [(b'\x00\x00' * 16, True)])
        self.assertEqual(dialogue.history, [
            {'role': 'user', 'content': '你好'},
            {'role': 'assistant', 'content': '你好，我是鸭子。'},
        ])

    def test_history_is_shared_across_turns_and_bounded_to_twelve_messages(self):
        dialogue = self.make(FakeASR([f'问题{i}' for i in range(8)]),
                             FakeLLM([f'回答{i}' for i in range(8)]))
        for _ in range(8):
            self.assertTrue(dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)['ok'])
        self.assertEqual(len(dialogue.history), 12)
        self.assertEqual(dialogue.history[0]['content'], '问题2')
        latest_request = self.llm.calls[-1][0]
        self.assertEqual(len(latest_request), 13)  # system + at most 12 messages including new user
        self.assertEqual(latest_request[-1]['content'], '问题7')

    def test_reset_cancels_transaction_and_clears_history(self):
        dialogue = self.make()
        dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)
        dialogue.reset()
        self.assertEqual(dialogue.history, [])
        self.assertTrue(dialogue.cancelled())

    def test_failure_is_not_retried_and_next_input_still_works(self):
        error = RuntimeError('https://secret.invalid/?key=do-not-log')
        dialogue = self.make(FakeASR(error=error), FakeLLM(['恢复']))
        with self.assertLogs('duck-echo', level='ERROR') as logged:
            result = dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.asr.calls), 1)
        self.assertNotIn('do-not-log', '\n'.join(logged.output))
        self.asr.error = None
        self.asr.transcripts = ['下一次']
        self.assertTrue(dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)['ok'])
        self.assertEqual(len(self.played), 1)

    def test_cancel_during_each_remote_stage_prevents_stale_playback(self):
        for stage in ('asr', 'llm', 'tts'):
            with self.subTest(stage=stage):
                entered, release = threading.Event(), threading.Event()
                kwargs = {stage: {'entered': entered, 'release': release}}
                asr = FakeASR(**kwargs.get('asr', {}))
                llm = FakeLLM(**kwargs.get('llm', {}))
                tts = FakeTTS(**kwargs.get('tts', {}))
                dialogue = self.make(asr, llm, tts)
                worker = threading.Thread(target=dialogue.handle,
                                          args=(b'\x00\x00\x00\x00',), kwargs={'allowed': lambda: True})
                worker.start()
                self.assertTrue(entered.wait(1))
                dialogue.cancel()
                release.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(self.played, [])
                self.assertEqual(dialogue.history, [])

    def test_external_allowed_false_cancels_before_playback(self):
        dialogue = self.make()
        allowed = [True]
        # The TTS fake flips the same generation gate used by VoiceControl.
        original = self.tts.synthesize
        def synthesize(text, cancelled, timeout):
            result = original(text, cancelled, timeout)
            allowed[0] = False
            return result
        self.tts.synthesize = synthesize
        result = dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: allowed[0])
        self.assertFalse(result['ok'])
        self.assertEqual(self.played, [])

    def test_intent_mode_uses_strict_prompt_and_returns_motion_candidate_prompt(self):
        gateway = FakeIntentGateway()
        gateway.model_replies = ['准备坐下，请在6秒内说“确认”。']
        raw = '{"version":1,"type":"motion","action":"sit","args":{}}'
        dialogue = self.make(FakeASR(['坐下']), FakeLLM([raw]), gateway=gateway)
        result = dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)
        self.assertTrue(result['ok'])
        self.assertEqual(gateway.transcripts, [('坐下', True)])
        self.assertEqual(gateway.model_texts, [(raw, True)])
        system = self.llm.calls[0][0][0]['content']
        self.assertIn('"version":1', system)
        self.assertIn('sit', system)
        self.assertIn('stand', system)
        self.assertEqual(self.tts.calls[0][0], '准备坐下，请在6秒内说“确认”。')
        self.assertEqual(dialogue.history[-1]['content'], '准备坐下，请在6秒内说“确认”。')

    def test_confirmation_bypasses_llm_and_never_enters_later_history(self):
        gateway = FakeIntentGateway()
        gateway.direct_reply = '已坐下。'
        dialogue = self.make(FakeASR(['确认', '下一个问题']), gateway=gateway)
        result = dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(self.tts.calls[0][0], '已坐下。')
        self.assertEqual(dialogue.history, [])

        gateway.direct_reply = None
        gateway.model_replies = ['普通回答']
        self.llm.replies = ['{"version":1,"type":"chat","text":"普通回答"}']
        self.assertTrue(dialogue.handle(b'\x00\x00\x00\x00', allowed=lambda: True)['ok'])
        self.assertEqual(len(self.llm.calls), 1)
        self.assertNotIn('确认', str(self.llm.calls[0][0]))
        self.assertNotIn('已坐下', str(self.llm.calls[0][0]))

    def test_cancel_and_reset_invalidate_gateway_candidate(self):
        gateway = FakeIntentGateway()
        dialogue = self.make(gateway=gateway)
        dialogue.cancel()
        self.assertEqual(gateway.cancel_count, 1)
        dialogue.reset()
        self.assertEqual(gateway.cancel_count, 2)


if __name__ == '__main__':
    unittest.main()
