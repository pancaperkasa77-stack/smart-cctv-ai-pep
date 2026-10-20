"""Operator-controlled sources only; never accept stream URLs from HTTP clients."""
import math
import os
from urllib.parse import urlsplit

DEFAULT_URLS = {
    1: 'https://cctv.jogjaprov.go.id/cctv-proxy/cctv-uptmalioboro/streams/UPTMalio_NolKm_Timur.stream.m3u8',
    2: 'https://cctv.jogjaprov.go.id/cctv-proxy/cctv-uptmalioboro/streams/UPTMalio_NolKmGedungAgung.stream.m3u8',
}
DEFAULT_NAMES = {1: 'Nol Kilometer Timur', 2: 'Nol Kilometer Gedung Agung'}


def source_url(value, key):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https', 'rtsp', 'rtsps') or not parsed.hostname:
            raise ValueError()
        if any(c.isspace() for c in value) or parsed.fragment:
            raise ValueError()
        parsed.port
    except (ValueError, TypeError):
        raise ValueError(key + ': URL HLS/RTSP tidak valid') from None
    return value


def seconds(env, key, default, minimum, maximum):
    try:
        value = float(env.get(key, default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError(key + ': interval tidak valid') from None


def load_settings(env=None):
    env = os.environ if env is None else env
    sources = {i: source_url(env.get('CCTV_%d_URL' % i, DEFAULT_URLS[i]), 'CCTV_%d_URL' % i) for i in (1, 2)}
    names = {i: env.get('CCTV_%d_NAME' % i, DEFAULT_NAMES[i]).strip() or 'CCTV %d' % i for i in (1, 2)}
    return {
        'sources': sources, 'names': names,
        'vehicle_profiles': {i: env.get('CCTV_%d_VEHICLE_PROFILE' % i, '').strip() for i in (1, 2)},
        'sample': seconds(env, 'CCTV_SAMPLE_SECONDS', 2, .5, 60),
        'stale': seconds(env, 'CCTV_STALE_SECONDS', 30, 5, 300),
        'reconnect': seconds(env, 'CCTV_RECONNECT_SECONDS', 5, 5, 60),
        'timeout': seconds(env, 'CCTV_TIMEOUT_SECONDS', 10, 2, 60),
    }


def source_description(url):
    # No userinfo, host, path or query in public status (tokens may be anywhere).
    return 'RTSP' if urlsplit(url).scheme in ('rtsp', 'rtsps') else 'HTTP/HLS'


def attribution(url):
    if urlsplit(url).hostname == 'cctv.jogjaprov.go.id':
        return 'CCTV Pemerintah Daerah DIY / cctv.jogjaprov.go.id'
    return 'Sumber kamera yang dikonfigurasi operator'
