"""Byte-level packaging regressions for the original offline runner audio."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import wave

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('runner_build_audio', HERE / 'build_audio.py')
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class AudioPackagingTests(unittest.TestCase):
    def setUp(self):
        # Explicit parent keeps test copies on E: as well.
        self.temp = tempfile.TemporaryDirectory(prefix='audio-qa-', dir=HERE)
        self.root = Path(self.temp.name) / 'audio_assets'
        shutil.copytree(audio.ROOT, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, change):
        path = self.root / 'manifest.json'
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_unchanged_cache_and_stereo(self):
        result = audio.verify_assets(self.root)
        self.assertLess(result['pcm_bytes'], 12 * 1024 * 1024)
        self.assertEqual((result['tracks'], result['effects']), (1, 7))

    def test_corrupt_audio_is_rejected(self):
        path = self.root / 'sfx_runner_coin.wav'
        payload = bytearray(path.read_bytes());payload[-4] ^= 1
        path.write_bytes(payload)
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            audio.verify_assets(self.root)

    def test_silent_channel_with_correct_hash_is_rejected(self):
        path = self.root / 'sfx_runner_coin.wav'
        with wave.open(str(path), 'rb') as source:
            params = source.getparams()
            values = audio.array('h', source.readframes(source.getnframes()))
        for i in range(1, len(values), 2):
            values[i] = 0
        with wave.open(str(path), 'wb') as target:
            target.setparams(params);target.writeframes(values.tobytes())
        self.edit(lambda data: data['effects']['sfx_runner_coin'].update(
            sha256=audio.hashlib.sha256(path.read_bytes()).hexdigest()))
        with self.assertRaisesRegex(ValueError, 'Silent channel'):
            audio.verify_assets(self.root)

    def test_wrong_loop_flag_is_rejected(self):
        self.edit(lambda data: data['effects']['sfx_runner_hit'].update(loop=True))
        with self.assertRaisesRegex(ValueError, 'Only background'):
            audio.verify_assets(self.root)

    def test_unknown_effect_id_is_rejected(self):
        self.edit(lambda data: data['effects'].update(sfx_unknown=data['effects']['sfx_runner_coin']))
        with self.assertRaisesRegex(ValueError, 'seven runner'):
            audio.verify_assets(self.root)

    def test_nan_gain_is_rejected(self):
        self.edit(lambda data: data['tracks']['bgm_runner'].update(gain=float('nan')))
        with self.assertRaisesRegex(ValueError, 'Invalid gain'):
            audio.verify_assets(self.root)

    def test_path_escape_is_rejected(self):
        self.edit(lambda data: data['tracks']['bgm_runner'].update(file='../bgm_runner.wav'))
        with self.assertRaisesRegex(ValueError, 'in-directory'):
            audio.verify_assets(self.root)


if __name__ == '__main__':
    unittest.main()
