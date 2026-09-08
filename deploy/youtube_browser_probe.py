"""One real guest-browser attestation attempt in the isolated CI runner.

No account cookies, proxy purchase, customer data or retained browser profile.
The shell enforces an overall timeout. Output is diagnostic booleans only.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import signal
import tempfile
import time
from pathlib import Path

from lecturesift import media
from lecturesift.errors import normalize_error

diagnostics = {'browser_started': False, 'provider_seen': False, 'token_generated': False, 'provider_error': False, 'provider_failure': 'none'}


class Logger:
    def debug(self, message):
        if 'PO Token Providers:' in message and 'wpc' in message:
            diagnostics['provider_seen'] = True
        if 'Retrieved ' in message and ' PO Token:' in message:
            diagnostics['token_generated'] = True

    def warning(self, message):
        if 'wpc' in message.lower() or 'WebPoClient' in message:
            diagnostics['provider_error'] = True
            lowered = message.lower()
            for category, needles in (
                ('browser_start', ('start browser', 'connect to browser', 'browser closed')),
                ('client_unavailable', ('webpoclient', 'wpc not found', 'initialization')),
                ('timeout', ('timeout', 'timed out')),
                ('attestation_rejected', ('rejected', 'invalid token')),
            ):
                if any(needle in lowered for needle in needles):
                    diagnostics['provider_failure'] = category
                    break
            else:
                diagnostics['provider_failure'] = 'other'

    def error(self, message):
        pass


def main():
    if os.getenv('GITHUB_ACTIONS') != 'true':
        print(json.dumps({'status': 'ci_only', 'production_verified': False}))
        return 1
    browser = shutil.which('google-chrome') or shutil.which('chromium')
    if not browser:
        print(json.dumps({'status': 'browser_unavailable', 'production_verified': False}))
        return 1
    original = media.yt_dlp.YoutubeDL
    # Start a task-owned, disposable browser explicitly and wait for its local
    # debugging endpoint. This avoids nodriver's short startup polling window.
    # The public runner has no customer profile; like its Playwright tests, this
    # browser uses the CI-specific no-sandbox option, never a production setting.
    profile = tempfile.TemporaryDirectory(prefix='lecturesift-wpc-browser-')
    process = subprocess.Popen([
        browser, '--no-sandbox', '--disable-dev-shm-usage', '--no-first-run',
        '--no-default-browser-check', '--disable-background-networking',
        '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
        '--user-data-dir=' + profile.name, 'about:blank',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    def stop_browser():
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
        profile.cleanup()
    port_file = Path(profile.name) / 'DevToolsActivePort'
    deadline = time.monotonic() + 20
    while not port_file.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(.25)
    if not port_file.exists():
        stop_browser()
        print(json.dumps({'status': 'browser_not_ready', 'production_verified': False}))
        return 1
    port = int(port_file.read_text().splitlines()[0])
    diagnostics['browser_started'] = True
    from nodriver.core.config import Config
    original_config = Config.__init__
    def browser_config(self, *args, **kwargs):
        kwargs.update(sandbox=False, host='127.0.0.1', port=port)
        original_config(self, *args, **kwargs)
    Config.__init__ = browser_config
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
        Config.__init__ = original_config
        stop_browser()
    result.update(elapsed_seconds=round(time.monotonic()-started,1), diagnostics=diagnostics)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'downloaded' else 1


if __name__ == '__main__':
    raise SystemExit(main())
