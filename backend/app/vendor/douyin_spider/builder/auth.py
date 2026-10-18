import base64
import http.cookiejar
import hashlib
import json
import os
import time
import uuid

from app.vendor.douyin_spider.dy_apis.douyin_api import DouyinAPI
from app.vendor.douyin_spider.utils.dy_util import (trans_cookies, generate_dynamic_msToken,
                           generate_s_v_web_id)

# msToken 缓存有效期（秒）：过期后下次访问自动重新换取
_MS_TTL = 600


class DouyinAuth:
    def __init__(self):
        self.cookie = None
        self._cookie_str = None
        self._raw_cookie_tokens = []
        # Provenance is deliberately kept separate from the Cookie mapping.
        # Opaque anonymous values must never be mistaken for locally
        # reproducible algorithms merely because their length is correct.
        self._cookie_sources = {}
        self._cookie_source_events = []
        self.private_key = None
        self.ticket = None
        self.ts_sign = None
        self.client_cert = None
        self.ree_public_key = None
        self.uid = None
        self.server_cert = None  # passport 下发的 ecies 服务端证书
        # x-tt-session-dtrait：设备特征头，三种来源，优先级从高到低——
        #   session_dtrait  直接给成品头
        #   dtrait_blob     给内层 blob，外层由 utils/dtrait.py 按 path 现算
        #   dtrait_profile  给设备档案，内层 blob 也由 utils/dtrait_features.py 现算
        self.dtrait_blob = None
        self.dtrait_profile = None
        self.session_dtrait = None
        # x-tt-passport-verify-portrait（`<uuid>.login`），登录期间 passport 请求要带
        self.verify_portrait = None
        # One-shot ticket returned by passport/web/send_code for the SMS
        # login flow.  It is session state only and is deliberately not
        # persisted with long-lived credentials.
        self.mobile_ticket = None
        self._sms_sent_at = None
        self.phone_login_profile = None
        # Phone login sets this when it is running against the browser-
        # evidence contract.  Keep it explicit so helpers can fail closed
        # instead of silently synthesizing missing wire state.
        self.strict_browser_alignment = False
        self._proxies = None
        self._trait_pk_cache = {}
        # Chrome reuses the RSA-encrypted AES session key in
        # x-tt-session-dtrait across requests; only IV/payload changes per
        # pathname.  Cache the first segment per (aid, origin, blob, key).
        self._dtrait_material_cache = {}
        self._dtrait_last_evidence = None
        self._ttwid = ""
        self._webid = ""         # 真实设备号缓存
        self._webid_resolving = False
        self._ms_cache = ""      # 动态 msToken 缓存
        self._ms_ts = 0          # 缓存时间戳
        # PC IM obtains a short-lived identity-security token immediately
        # before message/send.  Keep it on the same Auth instance so callers
        # can reuse a token during a burst, while retaining the device id
        # returned alongside it for the protobuf header map.
        self.identity_security_token = ""
        self.identity_security_device_id = ""
        self.identity_security_token_ts = 0
        # Passport's encrypted account_sdk_source_info is a page-session
        # measurement.  Chrome reuses it across challenge/send_code/sms_login;
        # keep the cached value and the two role-specific ``browser.t`` values
        # on Auth instead of regenerating them per request.
        self._passport_sdk_source_info = ""
        self._t_suffix = ""
        self._t_query = ""
        self._t_cookie = ""
        # QR login owns the mssdk lifecycle while polling.  During that
        # window the token is rotated by /web/common on Chrome's timer, not by
        # the generic 600-second /web/r/token cache path.
        self._ms_pinned = False
        self._ecdh_cache = {}    # (aid, origin) -> 32 字节 HMAC 密钥 / None
        self.http = None          # creator 全链路持久 curl_cffi Session
        self._http_seeded = False
        self._session_cookie_names = set()
        self._session_cookie_scopes = {}
        self._session_cookie_values = {}
        self.creator_csrf_token = ""
        self.creator_bootstrapped = False
        # Capability verification is intentionally separate from capability
        # construction.  A shared Auth/session can be wired for live or
        # creator traffic without having contacted those services yet.
        self.live_rest_verified = False
        self.live_websocket_connected = False
        self.live_websocket_verified = False
        self.main_read_verified = False
        self.creator_read_verified = False
        self.creator_upload_verified = False
        self.publish_attempted = False
        self.publish_server_verified = False
        # Optional wire-exact Cookie header captured from the browser.  This
        # deliberately stays separate from the CookieJar: duplicate names
        # with different scopes must survive exactly as sent by Chromium.
        self._creator_cookie_override = ""
        self._creator_cookie_template = []

    def perepare_auth(self, cookieStr: str, web_protect_: str = "", keys_: str = ""):
        self.cookie = trans_cookies(cookieStr)
        # ``trans_cookies`` keeps Chromium's occasional no-equals cookie
        # tokens (for example ``douyin.com``) on a sidecar because a normal
        # mapping cannot serialize that wire form without changing it.
        self._raw_cookie_tokens = list(
            getattr(self.cookie, "raw_tokens", ()) or ()
        )
        # Let the compatibility HTTP facade discover the owning Auth when a
        # legacy API still passes ``cookies=auth.cookie`` to a module-level
        # request helper.  The reference is process-local only and is never
        # serialized into the Cookie header.
        try:
            self.cookie._auth_owner = self
        except Exception:
            pass
        self._cookie_sources = {
            name: "captured_input" for name in self.cookie.keys()
        }
        self._cookie_source_events = []
        self._ttwid = self.cookie.get("ttwid", "")
        # 真实请求 msToken 只在 query 携带；cookie 里若有旧 msToken 去掉，避免冲突
        self.cookie.pop("msToken", None)
        # verifyFp / fp 取自 s_v_web_id，必须与随请求发出的 cookie 一致；缺失就自己生成
        if not self.cookie.get("s_v_web_id"):
            self.cookie["s_v_web_id"] = generate_s_v_web_id()
        self._sync_cookie_str()
        if web_protect_ != "":
            web_protect_ = json.loads(json.loads(web_protect_)['data'])
            self.ticket = web_protect_['ticket']
            self.ts_sign = web_protect_['ts_sign']
            self.client_cert = web_protect_['client_cert']
        if keys_ != "":
            keys_ = json.loads(json.loads(keys_)['data'])
            self.private_key = keys_['ec_privateKey']
            self.ree_public_key = base64.b64encode(self.private_key.encode()).decode()

    def _serialize_cookie_str(self):
        cookie = getattr(self, "cookie", None) or {}
        raw = list(getattr(self, "_raw_cookie_tokens", ()) or ())
        parts = [
            name if name in raw else f"{name}={value}"
            for name, value in cookie.items()
        ]
        parts.extend(token for token in raw if token not in cookie)
        return "; ".join(parts)

    @property
    def cookie_str(self):
        """Always reflect the current Cookie mapping on read.

        A number of bootstrap paths update ``auth.cookie`` directly.  A plain
        string attribute can therefore become stale between two requests.
        Re-serializing on read makes the mapping and wire Cookie header a
        single source of truth while preserving the caller's bare tokens.
        """
        if getattr(self, "cookie", None) is None:
            return self._cookie_str
        return self._serialize_cookie_str()

    @cookie_str.setter
    def cookie_str(self, value):
        # Keep compatibility with existing callers that assign the field;
        # subsequent reads intentionally derive from ``cookie`` instead.
        self._cookie_str = value

    def _sync_cookie_str(self):
        """Serialize ``cookie`` without losing Chromium bare tokens.

        Cookie values are updated by several independent bootstrap helpers.
        Keeping this operation in one place prevents ``cookie`` and
        ``cookie_str`` from silently diverging after a Set-Cookie merge.
        ``_raw_cookie_tokens`` contains the rare no-equals tokens observed in
        browser captures and must remain verbatim on the wire.
        """
        self._cookie_str = self._serialize_cookie_str()
        return self._cookie_str







    def ensure_http_session(self):
        """创建持久 TLS/HTTP2/Cookie 会话，并把主域 Cookie 按 domain 写入 jar。"""
        if self.http is None:
            from app.vendor.douyin_spider.utils.http_client import Session
            self.http = Session()
        # The login/bootstrap code may mutate ``auth.cookie`` after the
        # Session was first created (for example local ``bit_env`` derivation,
        # acrawler ``__ac_signature`` or a later Set-Cookie merge).  Mirror
        # those changes before every request so legacy call sites that route
        # through this Auth cannot accidentally send a stale jar.
        current = {
            str(name): str(value)
            for name, value in (self.cookie or {}).items()
            if name and value is not None
        }
        # These names are host-only in Chromium's www.douyin.com captures;
        # placing them on ``.douyin.com`` would leak page/device state to
        # login, live and creator subdomains.  Keep the list local to avoid a
        # builder -> login_api import cycle during Auth construction.
        www_only = {
            "s_v_web_id", "__ac_nonce", "__ac_signature",
            "x-web-secsdk-uid", "dy_swidth", "dy_sheight",
            "device_web_cpu_core", "device_web_memory_size",
            "architecture", "fpk1", "fpk2",
        }
        for name, value in current.items():
            scope = "www.douyin.com" if name in www_only else ".douyin.com"
            if (self._session_cookie_values.get(name) == value
                    and self._session_cookie_scopes.get(name) == scope):
                continue
            # A name may have been seeded on the old shared scope by a prior
            # call or by a response. Remove only the tracked Auth copy before
            # moving it to its browser-accurate scope; creator host-only
            # copies are not tracked and are left untouched.
            old_scope = self._session_cookie_scopes.get(name)
            if old_scope and old_scope != scope:
                try:
                    self.http.cookies.jar.clear(old_scope, "/", name)
                except KeyError:
                    pass
            self.http.cookies.set(
                name, value, domain=scope, path="/", secure=True,
            )
            self._session_cookie_values[name] = value
            self._session_cookie_scopes[name] = scope
        # Remove only the shared-domain entries previously seeded by Auth;
        # creator host-only/domain-scoped cookies belong to the creator
        # template and must survive this synchronization.
        stale = self._session_cookie_names.difference(current)
        for name in stale:
            try:
                self.http.cookies.jar.clear(
                    self._session_cookie_scopes.get(name, ".douyin.com"),
                    "/", name,
                )
            except KeyError:
                pass
            self._session_cookie_values.pop(name, None)
            self._session_cookie_scopes.pop(name, None)
        self._session_cookie_names = set(current)
        self._http_seeded = True
        return self.http

    def request(self, method, url, **kwargs):
        """使用 Auth 拥有的持久会话发送请求。"""
        session = self.ensure_http_session()
        # Legacy endpoints pass ``cookies=self.cookie`` even though this
        # Auth-owned Session already has the same jar.  Suppress that
        # duplicate parameter; curl_cffi otherwise serializes the mapping in
        # addition to the jar and can emit duplicate Cookie pairs.
        if kwargs.get("cookies") is self.cookie:
            kwargs = dict(kwargs)
            kwargs.pop("cookies", None)
        if kwargs.get("proxies") is None and self._proxies is not None:
            kwargs["proxies"] = self._proxies
        split_cookie_header = bool(kwargs.pop("split_cookie_header", False))
        headers = kwargs.get("headers") or {}
        cookie_header = next(
            (value for name, value in headers.items() if name.lower() == "cookie"),
            None,
        )
        if cookie_header is not None:
            # Session 会临时清空 jar，避免显式 Cookie 与自动 Cookie 叠加；
            # 保留原字典则可同时保留调用方指定的 HTTP/2 wire 位置。
            kwargs["headers"] = dict(headers)
            response = session.request_with_cookie_header(
                method, url, cookie_header,
                split_cookie_header=split_cookie_header,
                on_fresh_cookies=self._refresh_creator_cookie_template,
                **kwargs,
            )
            # Explicit Cookie-header callers (currently creator parity paths)
            # already receive scoped Set-Cookie updates through
            # ``_refresh_creator_cookie_template``.  Do not flatten those
            # host-only creator cookies into ``auth.cookie`` here; doing so
            # would reseed them on the shared .douyin.com domain next time.
            return response
        response = session.request(method, url, **kwargs)
        # Keep the flattened mapping/provenance ledger in sync for legacy
        # callers that route through ``requests.get(..., cookies=auth.cookie)``.
        # The persistent curl Session already stores these values in its jar;
        # this merge only mirrors response Set-Cookie into Auth state.
        try:
            from app.vendor.douyin_spider.utils.passport import merge_set_cookies
            merge_set_cookies(self, response.cookies.get_dict())
        except Exception:
            pass
        return response

    def _refresh_creator_cookie_template(self, fresh_cookies):
        """按 Chromium 的 Set-Cookie 更新时间重排模板中的同 scope occurrence。"""
        if not fresh_cookies:
            return

        def scope_of(cookie):
            if (
                cookie.domain == "creator.douyin.com"
                and not cookie.domain_specified
            ):
                return "host-only"
            return cookie.domain.lstrip(".")

        for cookie in fresh_cookies:
            scope = scope_of(cookie)
            if scope not in {"host-only", "creator.douyin.com", "douyin.com"}:
                continue
            matched = False
            retained = []
            for entry in self._creator_cookie_template:
                if (
                    not matched
                    and entry["name"] == cookie.name
                    and entry["scope"] == scope
                    and entry["path"] == cookie.path
                ):
                    matched = True
                    continue
                retained.append(entry)
            if matched or self._creator_cookie_template:
                retained.append({
                    "name": cookie.name,
                    "scope": scope,
                    "path": cookie.path,
                })
                self._creator_cookie_template = retained
            if scope == "douyin.com" and self.cookie is not None:
                self.cookie[cookie.name] = cookie.value
        if self.cookie is not None:
            self.cookie_str = "; ".join(
                f"{name}={value}" for name, value in self.cookie.items()
            )

    def cookie_header_for_url(self, url, method="GET"):
        return self.ensure_http_session().cookie_header(url, method=method)




    def mark_cookie_source(self, name, source, *, detail=""):
        """Record where a Cookie value came from.

        ``source`` is intentionally a small vocabulary used by the parity
        audits: ``captured_input``, ``server_set_cookie``, ``browser_runtime``,
        ``node_page_js``, ``local_pure_conditional``,
        ``runtime_response_unclassified`` and
        ``unproven_synthetic``.  This metadata is not sent on the wire and is
        safe to inspect in diagnostics.
        """
        if not name:
            return
        value = str(source or "unknown")
        self._cookie_sources[name] = value
        self._cookie_source_events.append({
            "name": str(name), "source": value,
            "detail": str(detail or ""), "time": time.time(),
        })

    def cookie_source(self, name):
        return (getattr(self, "_cookie_sources", {}) or {}).get(name, "unknown")



    def dtrait_evidence(self):
        """Return the latest local dtrait material for byte-parity audits.

        This is diagnostic state only; the RSA private material is never
        exposed.  The AES key is included because a hook/capture must pair it
        with the exact final header and IV to prove ciphertext equality.
        """
        value = getattr(self, "_dtrait_last_evidence", None)
        return dict(value) if isinstance(value, dict) else None


    def close(self):
        if self.http is not None:
            self.http.close()
            self.http = None
        # 下次创建 Session 时必须重新注入 Cookie，不能沿用旧连接的注入标记。
        self._session_cookie_names.clear()
        self._session_cookie_scopes.clear()
        self._session_cookie_values.clear()
        self._http_seeded = False


    def ticket_matches_session(self):
        """校验 ts_sign 与当前 cookie 是否同属一次登录。

        cookie 里的 `bd_ticket_guard_ts_sign_id` 就是 ts_sign 的前缀。二者对不上
        说明 ticket 和 cookie 来自不同次登录，create_v2 这类强校验接口会直接失败，
        且报错信息（403 空响应 / status_code 4）完全看不出根因。提前拦下来报清楚。
        """
        if not self.ts_sign:
            return False
        sign_id = (self.cookie or {}).get("bd_ticket_guard_ts_sign_id")
        if not sign_id:
            return True  # 没有该 cookie 时无从判断，交给服务端
        return self.ts_sign.startswith(sign_id)

    def ecdh_key(self, aid=6383, origin="https://www.douyin.com"):
        """bd-ticket-guard HMAC 密钥（ECDH + HKDF），失败返回 None 由调用方回退 ECDSA。

        证书按 (aid, origin) 缓存：不同子域下发的 aid 不同，密钥不通用。
        """
        if not self.private_key:
            return None
        cache_key = (aid, origin)
        if cache_key in self._ecdh_cache:
            return self._ecdh_cache[cache_key]
        key = None
        try:
            from app.vendor.douyin_spider.utils.bd_ticket import derive_ecdh_key, fetch_server_cert
            from app.vendor.douyin_spider.builder.header import HeaderBuilder
            cert, _sn = fetch_server_cert(
                aid, self.cookie_str, origin=origin, user_agent=HeaderBuilder.ua,
            )
            self.server_cert = cert
            key = derive_ecdh_key(self.private_key, cert)
        except Exception:
            key = None
        self._ecdh_cache[cache_key] = key
        return key

    @property
    def webid(self):
        """真实设备号，取一次缓存复用。

        优先向 `/aweme/v1/web/query/user` 换取；该接口自身也要带 webid，
        引导阶段用随机值顶上（`_webid_resolving` 防递归）。
        退而求其次才去抓服务端渲染页面里的 `user_unique_id`。
        随机 webid 会被搜索等接口静默拒绝（HTTP 200 但响应体为空），所以务必拿到真值。
        """
        if self._webid:
            return self._webid
        if self._webid_resolving:
            from app.vendor.douyin_spider.utils.dy_util import generate_fake_webid
            return generate_fake_webid()
        self._webid_resolving = True
        try:
            from app.vendor.douyin_spider.dy_apis.douyin_api import DouyinAPI
            wid = str(DouyinAPI.get_device_id(self) or "")
        except Exception:
            wid = ""
        finally:
            self._webid_resolving = False
        if not wid:
            from app.vendor.douyin_spider.utils.dy_util import generate_webid
            wid = generate_webid(self, "https://www.douyin.com/discover")
        self._webid = wid
        return self._webid

    def session_dtrait_header(self, path, aid=6383, origin="https://www.douyin.com",
                              timestamp=None, randbytes=None, strict=False,
                              allow_static=True):
        """按请求 path 生成 x-tt-session-dtrait；拿不到素材时返回 None。"""
        blob = self.dtrait_blob
        if not blob and self.dtrait_profile:
            try:
                from app.vendor.douyin_spider.utils.dtrait_features import build_blob
                blob = build_blob(self.dtrait_profile)
            except Exception:
                blob = None
        # A captured complete header is safe only for non-strict legacy
        # callers.  Strict login/publish paths must rebuild the second AES
        # segment for the current pathname and therefore cannot reuse a
        # static whole header.
        if not blob and self.session_dtrait and allow_static and not strict:
            return self.session_dtrait
        if not blob:
            if strict:
                raise RuntimeError(
                    "缺少可按 path 重算的 dtrait 设备素材；请配置 DY_DTRAIT_BLOB，"
                    "发布接口禁止省略 x-tt-session-dtrait"
                )
            return None
        try:
            from app.vendor.douyin_spider.utils.dtrait import (
                build_session_dtrait,
                builtin_trait_pubkey,
                fetch_trait_pubkey,
            )
            from app.vendor.douyin_spider.builder.header import HeaderBuilder
            cache_key = (aid, origin)
            pk = self._trait_pk_cache.get(cache_key)
            if pk is None:
                # The browser's QR-login initialization uses the SDK's
                # embedded d0 certificate and therefore does not issue a
                # separate ``type=trait`` request before challenge.  Keep a
                # remote escape hatch for key rotation/debugging, but make
                # browser parity the default.
                cert_mode = os.getenv("DY_DTRAIT_CERT_MODE", "builtin").lower()
                if cert_mode in {"remote", "server", "fetch"}:
                    pk = fetch_trait_pubkey(
                        aid, self.cookie_str,
                        csrf_token=self.cookie.get('passport_csrf_token')
                        or self.cookie.get('passport_csrf_token_default') or '',
                        origin=origin, user_agent=HeaderBuilder.ua,
                    )
                else:
                    pk = builtin_trait_pubkey()
                # ticket_guard/get_client_cert also seeds passport CSRF
                # cookies.  Preserve them before the caller builds its next
                # passport request (notably the initial challenge).
                for name, value in (pk.get("_set_cookies") or {}).items():
                    if value:
                        self.cookie[name] = value
                self.cookie_str = "; ".join(
                    f"{k}={v}" for k, v in self.cookie.items())
                self._trait_pk_cache[cache_key] = pk
            fixed_timestamp = (
                timestamp if timestamp is not None
                else os.getenv("DY_DTRAIT_FIXED_TIMESTAMP")
            )
            effective_timestamp = (
                int(fixed_timestamp) if fixed_timestamp is not None
                else int(time.time())
            )
            sdk_version = (os.getenv("DY_DTRAIT_SDK_VERSION")
                           or "1.0.0.16")
            if randbytes is None:
                encoded = (os.getenv("DY_DTRAIT_FIXED_RANDBYTES") or "").strip()
                if encoded:
                    try:
                        raw = (bytes.fromhex(encoded) if len(encoded) % 2 == 0
                               else base64.b64decode(encoded + "=="))
                    except Exception as error:
                        raise ValueError(
                            "DY_DTRAIT_FIXED_RANDBYTES 必须是十六进制或 base64"
                        ) from error
                    cursor = 0

                    def fixed_randbytes(size):
                        nonlocal cursor
                        end = cursor + int(size)
                        if end > len(raw):
                            raise ValueError(
                                "DY_DTRAIT_FIXED_RANDBYTES 长度不足，无法完成 RSA/AES 随机流"
                            )
                        chunk = raw[cursor:end]
                        cursor = end
                        return chunk

                    randbytes = fixed_randbytes
            material_key = (
                int(aid), str(origin), hashlib.sha256(str(blob).encode("utf-8")).hexdigest(),
                str(pk.get("pk1_version") or ""),
            )
            material = self._dtrait_material_cache.get(material_key)
            if material is None:
                header, material = build_session_dtrait(
                    path, blob, pk["pk1"], pk["pk1_version"],
                    sdk_version=sdk_version,
                    timestamp=effective_timestamp,
                    randbytes=randbytes, return_material=True,
                )
                self._dtrait_material_cache[material_key] = material
                self._record_dtrait_evidence(path, blob, effective_timestamp,
                                              sdk_version, pk, header, material)
                return header
            header = build_session_dtrait(
                path, blob, pk["pk1"], pk["pk1_version"],
                sdk_version=sdk_version,
                timestamp=effective_timestamp,
                randbytes=randbytes, session_material=material,
            )
            self._record_dtrait_evidence(path, blob, effective_timestamp,
                                          sdk_version, pk, header, material)
            return header
        except Exception as error:
            if strict:
                raise RuntimeError(
                    f"x-tt-session-dtrait 构造失败: {error}"
                ) from error
            return None

    def _record_dtrait_evidence(self, path, blob, timestamp, sdk_version, pk,
                                header, material):
        """Capture exact AES inputs/outputs used for the latest header."""
        try:
            key_hex, enc_key = material
            parts = str(header).split("_")
            raw = base64.b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
            iv, ciphertext = raw[:16], raw[16:]
            payload = {
                "dtrait": blob,
                "timestamp": int(timestamp if timestamp is not None else time.time()),
                "sdkVersion": sdk_version or "1.0.0.16",
                "path": path,
            }
            payload_json = json.dumps(payload, ensure_ascii=False,
                                      separators=(",", ":"))
            self._dtrait_last_evidence = {
                "evidence_source": "local_rebuild",
                "browser_wire_paired": False,
                "payload": payload_json,
                "aes_key_hex": key_hex,
                "iv": iv.hex(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "cipherText": base64.b64encode(raw).decode(),
                "enc_key": base64.b64encode(enc_key).decode(),
                "header": header,
                "same_invocation": True,
                "path": path,
                "pk1_version": pk.get("pk1_version", ""),
            }
        except Exception:
            self._dtrait_last_evidence = None

    @property
    def msToken(self):
        """仅缓存当前账号令牌；续期失败停止请求，不生成随机替代令牌。"""
        now = time.time()
        if self._ms_cache and (self._ms_pinned or now - self._ms_ts < _MS_TTL):
            return self._ms_cache
        tok = generate_dynamic_msToken(ttwid=self._ttwid, current_token=self._ms_cache)
        if not tok:
            raise ValueError('私信动态令牌更新失败，请检查网络和账号认证')
        self._ms_cache, self._ms_ts = tok, now
        return tok

    @msToken.setter
    def msToken(self, value):
        if value:
            self._ms_cache = value
            self._ms_ts = time.time()


    def _merge_runtime_cookies(self, values):
        """Merge cookies emitted by SDK runtime endpoints.

        The sink receives a cookie mapping, not the original response header.
        Keep that distinction explicit: only ``merge_set_cookies`` (called
        with an actual HTTP response's Set-Cookie mapping) may mark a value as
        ``server_set_cookie``.  This sink remains unclassified so a future SDK
        cookie cannot be mistaken for proven server provenance.
        """
        if not values:
            return
        for name, value in dict(values).items():
            if value in (None, ""):
                continue
            self.cookie[name] = str(value)
            self.mark_cookie_source(
                name, "runtime_response_unclassified",
                detail="mssdk runtime cookie sink; original Set-Cookie header unavailable",
            )
        self.cookie_str = "; ".join(
            f"{name}={value}" for name, value in self.cookie.items())

    def get_uid(self):
        if self.uid is None:
            self.uid = DouyinAPI.get_my_uid(self)
        return self.uid
