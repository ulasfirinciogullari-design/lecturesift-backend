"""One real guest-browser attestation attempt in the isolated CI runner.

No account cookies, proxy purchase, customer data or retained browser profile.
The shell enforces an overall timeout. Output is diagnostic booleans only.
"""
import contextlib
import io
import json
import shutil
import tempfile
import time
from pathlib import Path

from lecturesift import media
from lecturesift.errors import normalize_error

diagnostics = {'provider_seen': False, 'token_generated': False, 'provider_error': False}


class Logger:
    def debug(self, message):
        if 'PO Token Providers:' in message and 'wpc' in message:
            diagnostics['provider_seen'] = True
        if 'Retrieved ' in message and ' PO Token:' in message:
            diagnostics['token_generated'] = True

    def warning(self, message):
        if 'wpc' in message.lower() or 'WebPoClient' in message:
            diagnostics['provider_error'] = True

    def error(self, message):
        pass


def main():
    browser = shutil.which('google-chrome') or shutil.which('chromium')
    if not browser:
        print(json.dumps({'status': 'browser_unavailable', 'production_verified': False}))
        return 1
    original = media.yt_dlp.YoutubeDL
    def downloader(options):
        options.update(logger=Logger(), verbose=True, no_warnings=False, retries=0, socket_timeout=15)
        args = options.setdefault('extractor_args', {})
        args.setdefault('youtube', {}).update(fetch_pot=['always'], pot_trace=['true'])
        args['youtubepot-wpc'] = {'browser_path': [browser]}
        args['youtubepot-bgutilhttp'] = {'disable': ['true']}
        args['youtubepot-bgutilscript'] = {'disable': ['true']}
        return original(options)
    media.yt_dlp.YoutubeDL = downloader
    media.MAX_VIDEO_BYTES = 16 * 1024 * 1024
    started = time.monotonic()
    result = {'provider': 'wpc', 'sample_id': 'x41yOUIvK2k', 'production_verified': False}
    try:
        with tempfile.TemporaryDirectory(prefix='lecturesift-wpc-probe-') as folder:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                path = media._download_ytdlp_attempt('https://www.youtube.com/watch?v=x41yOUIvK2k', Path(folder), 'audio_export', False, clients=['mweb'])
                if not media.has_audio_stream(path):
                    raise RuntimeError('Downloaded sample has no audio')
            result.update(status='downloaded', bytes=path.stat().st_size)
    except Exception as exc:
        reason = str(exc.__cause__ or exc).lower()
        result.update(status='unavailable', error_code=normalize_error(exc).code,
                      failure_kind='bot_challenge' if 'not a bot' in reason else 'other')
    finally:
        media.yt_dlp.YoutubeDL = original
    result.update(elapsed_seconds=round(time.monotonic()-started,1), diagnostics=diagnostics)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'downloaded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
