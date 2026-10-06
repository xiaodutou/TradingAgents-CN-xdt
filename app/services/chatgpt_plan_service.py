"""Official Sign in with ChatGPT for this personal, locally hosted installation.

Tokens never enter the model configuration or the browser. The encrypted vault
is mounted separately from source/data exports. One backend process owns refresh.
"""
import base64
import hashlib
import html
import json
import os
import secrets
import threading
import time
import uuid
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import requests
from cryptography.fernet import Fernet

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
MODEL_PREFIX = "chatgpt-plus/"
USAGE_URL = "https://chatgpt.com/#settings/Usage"


class ChatGPTPlanError(RuntimeError):
    def __init__(self, message, code="chatgpt_plan_error", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def upstream_error(status, code=None):
    if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"} or status == 429:
        return ChatGPTPlanError("ChatGPT 会员额度暂不可用，请在 ChatGPT 设置 → 用量查看限额。", "usage_limit", 429)
    if status in {401, 403} or code == "invalid_grant":
        return ChatGPTPlanError("ChatGPT 授权已失效或账号未获准使用，请重新登录授权。", "reauthorization_required", 401)
    return ChatGPTPlanError(f"OpenAI 请求失败（HTTP {status}），请稍后重试。", "upstream_error", 502)


class ChatGPTPlanService:
    def __init__(self, directory=None, secret=None):
        from app.core.config import settings
        self.directory = Path(directory or os.getenv("CHATGPT_PLAN_STORAGE", "/app/chatgpt_credentials"))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256((secret or settings.JWT_SECRET).encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self.lock = threading.RLock()
        self.pending = {}
        self.server = None
        self.last_error = None
        self.path = self.directory / "accounts.enc"
        if self.path.exists():
            self.vault = json.loads(self.cipher.decrypt(self.path.read_bytes()))
        else:
            self.vault = {"host_id": "urn:uuid:" + str(uuid.uuid4()), "profiles": {}, "active": None}
            self._save()

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(self.cipher.encrypt(json.dumps(self.vault).encode()))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def status(self):
        with self.lock:
            profiles = [{"id": key, "email": p.get("email", ""), "label": p.get("label", key),
                         "connected": bool(p.get("access_token")),
                         "sharing": bool(p.get("access_token")) and "chatgpt.tokens.use.direct" in p.get("scopes", [])}
                        for key, p in self.vault["profiles"].items()]
            return {"profiles": profiles, "active": self.vault["active"], "error": self.last_error,
                    "usage_url": USAGE_URL, "pending": any(p["expires"] > time.time() for p in self.pending.values())}

    def _listener(self):
        if self.server is not None:
            return
        service = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # OAuth codes and ID-token hints must not be logged.

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/auth/callback" or len(self.path) > 16000:
                    self.send_error(404)
                    return
                try:
                    service.complete(parse_qs(parsed.query))
                    message = "ChatGPT 授权完成，请返回 TradingAgents 的 ChatGPT Plus 配置页。"
                    status = 200
                except ChatGPTPlanError as exc:
                    message, status = str(exc), exc.status
                except Exception:
                    message, status = "授权未完成，请返回应用重新尝试。", 400
                body = ("<!doctype html><html lang='zh'><meta charset='utf-8'><title>ChatGPT 授权</title>"
                        "<body style='font-family:sans-serif;padding:40px'><h2>" + html.escape(message) +
                        "</h2><a href='http://localhost:8088/settings/config?tab=chatgpt-plus'>返回配置页</a></body></html>").encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

        try:
            self.server = ThreadingHTTPServer((os.getenv("CHATGPT_CALLBACK_BIND", "127.0.0.1"), 1455), Callback)
        except OSError:
            raise ChatGPTPlanError("本地授权回调端口 1455 不可用，请检查是否被其他程序占用。") from None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def begin(self, profile_id=None):
        with self.lock:
            self._listener()
            if profile_id and profile_id not in self.vault["profiles"]:
                raise ChatGPTPlanError("请选择已保存的 ChatGPT 账号。")
            profile = self.vault["profiles"].get(profile_id, {})
            state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            redirect = "http://127.0.0.1:1455/auth/callback"
            self.pending = {k: v for k, v in self.pending.items() if v["expires"] > time.time()}
            if len(self.pending) >= 8:
                raise ChatGPTPlanError("登录请求过多，请等待已有请求完成。")
            self.pending[state] = {"nonce": nonce, "verifier": verifier, "redirect": redirect,
                                   "profile_id": profile_id, "client_id": profile.get("client_id"), "expires": time.time() + 600}
            params = {"client_id": profile.get("client_id", "dynamic_agent_client"), "ext_agent_host_id": self.vault["host_id"],
                      "response_type": "code", "redirect_uri": redirect, "scope": SCOPE, "resource": RESOURCE,
                      "state": state, "nonce": nonce, "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if not profile.get("client_id"):
                params["agent_name_hint"] = "TradingAgents-CN-xdt"
            elif profile.get("id_token"):
                params["id_token_hint"] = profile["id_token"]
                params["login_hint"] = profile.get("email", "")
            self.last_error = None
            return {"authorization_url": ISSUER + "/api/accounts/authorize?" + urlencode(params)}

    def _token_request(self, data):
        try:
            response = requests.post(ISSUER + "/api/accounts/oauth/token", data=data, timeout=30, allow_redirects=False)
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise ChatGPTPlanError("无法连接 OpenAI 授权服务，请检查网络后重试。", status=502) from None
        if response.status_code != 200:
            raise upstream_error(response.status_code, payload.get("error"))
        if not payload.get("access_token") or not payload.get("expires_in"):
            raise ChatGPTPlanError("OpenAI 未返回完整授权凭证，请重新登录。")
        return payload

    def _verify_identity(self, token, client_id, nonce):
        try:
            key = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=20).get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                                options={"require": ["iss", "aud", "exp", "sub", "nonce"]})
            if not secrets.compare_digest(str(claims["nonce"]), nonce):
                raise ValueError("nonce")
            return claims
        except Exception:
            raise ChatGPTPlanError("ChatGPT 登录身份校验失败，请重新登录。", "identity_validation_failed") from None

    def complete(self, query):
        def first(name):
            values = query.get(name, [])
            if len(values) > 1:
                raise ChatGPTPlanError("授权回调参数无效。")
            return values[0] if values else None
        with self.lock:
            state = first("state")
            attempt = self.pending.pop(state, None)
            if not attempt or attempt["expires"] <= time.time():
                raise ChatGPTPlanError("授权请求已过期或不匹配，请重新发起登录。", "invalid_state")
            if first("error"):
                self.last_error = "授权被取消，请重新登录并允许使用 ChatGPT 会员额度。"
                raise ChatGPTPlanError(self.last_error, "consent_declined")
            client_id = first("client_id") or attempt["client_id"]
            if not client_id or client_id == "dynamic_agent_client" or (attempt["client_id"] and client_id != attempt["client_id"]):
                raise ChatGPTPlanError("授权客户端不匹配，请重新登录。")
            code = first("code")
            if not code:
                raise ChatGPTPlanError("授权回调缺少授权码。")
            # Retain an issued client even if the exchange expires; don't replace the active account.
            profile_id = attempt["profile_id"] or client_id
            previous = self.vault["profiles"].get(profile_id, {})
            self.vault["profiles"].setdefault(profile_id, {"client_id": client_id, "label": "新连接 " + client_id[-6:]})
            self._save()
            try:
                tokens = self._token_request({"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                              "code_verifier": attempt["verifier"], "redirect_uri": attempt["redirect"], "resource": RESOURCE})
                claims = self._verify_identity(tokens.get("id_token", ""), client_id, attempt["nonce"])
                if previous.get("subject") and claims["sub"] != previous["subject"]:
                    raise ChatGPTPlanError("登录账号与所选连接不一致，请使用原账号或添加新连接。")
                profile = {"client_id": client_id, "subject": claims["sub"], "email": claims.get("email", ""),
                           "label": claims.get("email", "ChatGPT") + " · " + client_id[-6:],
                           "access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token"),
                           "id_token": tokens["id_token"], "scopes": tokens.get("scope", "").split(),
                           "expires_at": time.time() + int(tokens["expires_in"])}
                self.vault["profiles"][profile_id] = profile
                self.vault["active"] = profile_id
                self._save()
                if "chatgpt.tokens.use.direct" not in profile["scopes"]:
                    raise ChatGPTPlanError("账号已连接，但未授权会员额度，请重新登录并允许使用额度。", "sharing_not_granted")
                self.last_error = None
            except ChatGPTPlanError as exc:
                self.last_error = str(exc)
                raise

    def select(self, profile_id):
        with self.lock:
            if profile_id not in self.vault["profiles"]:
                raise ChatGPTPlanError("账号连接不存在。")
            self.vault["active"] = profile_id
            self._save()
            return self.status()

    def access_token(self):
        with self.lock:
            profile = self.vault["profiles"].get(self.vault["active"], {})
            if not profile.get("access_token") or "chatgpt.tokens.use.direct" not in profile.get("scopes", []):
                raise ChatGPTPlanError("请先在配置管理 → ChatGPT Plus 中登录并授权会员额度。", "not_connected", 401)
            if profile["expires_at"] < time.time() + 120:
                if not profile.get("refresh_token"):
                    raise upstream_error(401)
                try:
                    tokens = self._token_request({"grant_type": "refresh_token", "client_id": profile["client_id"],
                                                  "refresh_token": profile["refresh_token"], "resource": RESOURCE})
                except ChatGPTPlanError as exc:
                    self.last_error = str(exc)
                    if exc.status == 401:
                        for name in ("access_token", "refresh_token", "id_token"):
                            profile.pop(name, None)
                        self._save()
                    raise
                profile["access_token"] = tokens["access_token"]
                profile["refresh_token"] = tokens.get("refresh_token", profile["refresh_token"])
                profile["expires_at"] = time.time() + int(tokens["expires_in"])
                if "scope" in tokens:
                    profile["scopes"] = tokens["scope"].split()
                self._save()
                if "chatgpt.tokens.use.direct" not in profile["scopes"]:
                    raise ChatGPTPlanError("账号已撤销会员额度权限，请重新授权。", "sharing_not_granted", 401)
            return profile["access_token"]

    def models(self):
        token = self.access_token()
        try:
            response = requests.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + token}, timeout=30, allow_redirects=False)
            if response.status_code != 200:
                raise upstream_error(response.status_code)
            payload = response.json()
            return [{"slug": m["slug"], "display_name": m.get("display_name", m["slug"])}
                    for m in payload.get("models", []) if m.get("visibility") == "list" and m.get("slug")]
        except (requests.RequestException, ValueError, KeyError):
            raise ChatGPTPlanError("无法读取 ChatGPT 模型目录，请检查网络后重试。", status=502) from None

    def sign_out(self, profile_id):
        with self.lock:
            profile = self.vault["profiles"].get(profile_id)
            if not profile:
                raise ChatGPTPlanError("账号连接不存在。")
            revoked = not profile.get("refresh_token")
            if profile.get("refresh_token"):
                try:
                    discovery = requests.get(ISSUER + "/.well-known/openid-configuration", timeout=15).json()
                    endpoint = discovery["revocation_endpoint"]
                    if not endpoint.startswith(ISSUER + "/"):
                        raise ValueError("untrusted endpoint")
                    for delay in (0, 1, 2):
                        if delay:
                            time.sleep(delay)
                        response = requests.post(endpoint, data={"token": profile["refresh_token"],
                                                  "token_type_hint": "refresh_token", "client_id": profile["client_id"]},
                                                 timeout=15, allow_redirects=False)
                        if response.status_code == 200:
                            revoked = True
                            break
                        if response.status_code < 500:
                            break
                except (requests.RequestException, ValueError, KeyError):
                    pass
            for name in ("access_token", "refresh_token", "id_token"):
                profile.pop(name, None)
            self.pending = {k: v for k, v in self.pending.items() if v["profile_id"] != profile_id}
            self._save()
            return {"revoked": revoked, "message": "已退出登录。" if revoked else "已清除本地授权，远端撤销未确认，请在 ChatGPT 设置中断开此应用。"}

    def response(self, payload, timeout=180):
        # Allowlist: never forward temperature/max tokens, previous_response_id, or API keys.
        request_body = {k: v for k, v in payload.items() if k in {"model", "input", "instructions", "tools", "tool_choice", "include"}}
        request_body.update(store=False, stream=True)
        token = self.access_token()
        try:
            with requests.post(RESOURCE + "/responses", json=request_body,
                               headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
                               stream=True, timeout=(20, timeout), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise upstream_error(response.status_code)
                response.encoding = "utf-8"
                data = []
                completed_items = {}
                for line in response.iter_lines(decode_unicode=True):
                    if line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        event = json.loads("\n".join(data))
                        data = []
                        event_type = event.get("type")
                        if event_type == "response.output_item.done":
                            completed_items[event["output_index"]] = event["item"]
                        if event_type in {"response.failed", "response.incomplete", "error"}:
                            error = event.get("response", {}).get("error") or event.get("error") or event
                            if not isinstance(error, dict):
                                error = {}
                            raise upstream_error(502, error.get("code"))
                        if event_type == "response.completed":
                            result = event["response"]
                            if result.get("status") != "completed":
                                raise ChatGPTPlanError("ChatGPT 响应未完成，请重新尝试。", "incomplete_response", 502)
                            if not result.get("output") and completed_items:
                                result["output"] = [completed_items[i] for i in sorted(completed_items)]
                            return result
                raise ChatGPTPlanError("ChatGPT 响应流中断，未收到完成事件。", "incomplete_response", 502)
        except (requests.RequestException, ValueError, KeyError):
            raise ChatGPTPlanError("ChatGPT 请求或响应流失败，请检查网络后重试。", "network_error", 502) from None


@lru_cache(maxsize=1)
def get_chatgpt_plan_service():
    return ChatGPTPlanService()
