import json
import unittest
from unittest.mock import patch

from app.demo_live.config import load_settings, source_description
from app.demo_live.runtime import Runtime


class CameraConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = load_settings({})
        self.assertEqual(set(cfg['sources']), {1, 2})
        self.assertTrue(cfg['sources'][1].endswith('.m3u8'))
        self.assertEqual(cfg['sample'], 2)

    def test_rtsp_override(self):
        url = 'rtsp://tester:p%40ss@192.0.2.1:554/stream1?token=example'
        cfg = load_settings({'CCTV_1_URL': url, 'CCTV_1_NAME': 'Gate A', 'CCTV_SAMPLE_SECONDS': '3'})
        self.assertEqual(cfg['sources'][1], url)
        self.assertEqual(cfg['names'][1], 'Gate A')
        self.assertEqual(source_description(url), 'RTSP')
        self.assertEqual(cfg['sample'], 3)

    def test_invalid_input_does_not_echo_secret(self):
        for value in ['file:///tmp/secret', 'rtsp://user:SECRET@', '', 'https://host/a b']:
            with self.subTest(value=value), self.assertRaises(ValueError) as exc:
                load_settings({'CCTV_1_URL': value})
            self.assertNotIn('SECRET', str(exc.exception))
        for value in ['nan', '0', '9999']:
            with self.assertRaises(ValueError): load_settings({'CCTV_SAMPLE_SECONDS': value})

    def test_status_hides_entire_source_url(self):
        url = 'rtsp://tester:SECRET@192.0.2.1/private?token=PRIVATE'
        with patch('app.demo_live.runtime.SOURCES', {1: url, 2: url}):
            status = Runtime().status(1)
        text = json.dumps(status)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('PRIVATE', text)
        self.assertNotIn('192.0.2.1', text)
        self.assertNotIn('source_url', status)
        self.assertEqual(status['source_type'], 'RTSP')


if __name__ == '__main__':
    unittest.main()
