"""Fixed-endpoint DeepSeek client; no redirects, logged bodies, or retries."""
import json
import platform
from pathlib import Path
import socket
import urllib.error
import urllib.request
from .contracts import AIError, MODEL, parse_json

ENDPOINT = 'https://api.deepseek.com/chat/completions'
MAX_RESPONSE = 131072


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _board():
    return platform.system() == 'Linux' and platform.machine().lower() in ('aarch64', 'arm64')


class DeepSeekClient:
    model = MODEL
    def __init__(self, config_path=None, *, opener=None):
        self._key = None
        self.timeout = 20.
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        # Do not look for local PC credentials. An explicit test/config path is
        # supported; the default file is read only by the ARM64 Linux runtime.
        if config_path is None and not _board():
            return
        path = Path(config_path) if config_path is not None else Path.home()/'.config/ink-suite/deepseek.json'
        try:
            if not path.is_file() or path.stat().st_size > 16384:
                return
            config = json.loads(path.read_text(encoding='utf-8'))
            key = config.get('api_key') if isinstance(config, dict) else None
            if not isinstance(key, str) or not 8 <= len(key.strip()) <= 512 or any(ord(c)<33 or ord(c)>126 for c in key.strip()):
                return
            if config.get('model', MODEL) != MODEL:
                return
            timeout = config.get('timeout', 20.)
            if type(timeout) in (int, float) and 3 <= timeout <= 30:
                self.timeout = float(timeout)
            self._key = key.strip()
        except (OSError, ValueError, TypeError, UnicodeError):
            return

    @property
    def configured(self):
        return bool(self._key)

    def complete(self, messages):
        if not self.configured:
            raise AIError('not_configured', 'AI 尚未配置，请先设置 API Key。', 503)
        payload = dict(model=MODEL, messages=messages, thinking={'type':'disabled'},
                       response_format={'type':'json_object'}, max_tokens=1000,
                       stream=False, temperature=.3)
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded) > 65536:
                raise AIError('request_too_large', '本次汇总过长，请缩小范围。', 400)
            request = urllib.request.Request(ENDPOINT, data=encoded, method='POST',
                headers={'Authorization':'Bearer '+self._key, 'Content-Type':'application/json',
                         'Accept':'application/json', 'User-Agent':'InkSuite-AI/1'})
            with self._opener.open(request, timeout=self.timeout) as response:
                data = response.read(MAX_RESPONSE+1)
                if len(data) > MAX_RESPONSE:
                    raise AIError('invalid_response', 'AI 返回过长，请稍后重试。', 502)
            result = json.loads(data.decode('utf-8'))
            choice = result['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise AIError('incomplete_response', 'AI 回答未完整生成，请重试。', 502)
            message = choice['message']
            if message.get('tool_calls'):
                raise AIError('invalid_response', 'AI 返回了不支持的操作。', 502)
            return parse_json(message['content'])
        except AIError:
            raise
        except urllib.error.HTTPError as exc:
            # Never read, echo, log or persist the server response body.
            if exc.code in (401, 403):
                raise AIError('authentication', 'AI 授权未通过，请检查 API Key。', 503) from None
            if exc.code == 429:
                raise AIError('rate_limited', 'AI 服务繁忙，请稍后重试。', 429) from None
            raise AIError('remote_error', 'AI 服务暂不可用，请稍后重试。', 503) from None
        except (TimeoutError, socket.timeout):
            raise AIError('timeout', 'AI 请求超时，可以继续本地体验。', 504) from None
        except urllib.error.URLError:
            raise AIError('network', '暂时无法连接 AI 服务，可以继续本地体验。', 503) from None
        except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
            raise AIError('invalid_response', 'AI 返回格式异常，请稍后重试。', 502) from None
