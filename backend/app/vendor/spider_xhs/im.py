"""摘自 Spider_XHS 的私信帧与签名请求；保留上游字段顺序。"""
from __future__ import annotations
import base64
import binascii
import json
import secrets
import time
import uuid
from typing import Any, Mapping, Optional
from app.vendor.spider_xhs.xhs_utils.http_util import REQUEST_TIMEOUT
from app.vendor.spider_xhs.xhs_utils.xhs_pc import XHSPcAuth
from app.vendor.spider_xhs.xhs_utils.xhs_pc.params import build_pc_live_headers, build_pc_business_headers, generate_request_params


LIVE_ORIGIN = "https://live-room.xiaohongshu.com"


PUSH_URL = "wss://apppush-rws.xiaohongshu.com/rwp"


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


RSA_N = int(
    "d321555d67813eace010dc27e72ab14876a9b671c7d58d3c9c2064cd60f7e9f7"
    "9ad3799657b35a1b7654d82725408a71549d5ade11e74bbf1ec39b549ed32116a"
    "ffd4f6b03f2c9c44d91f84157b159a8a225150916e2716cc82dc8fd62385e5a"
    "01c83b784c139462a1dd45d47d96ebb4f5068c42b3de8590123a03565a9e5aed",
    16,
)


RSA_E = 65537


RSA_BYTES = 128


RSA_CHUNK_BYTES = RSA_BYTES - 11


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _b64(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return base64.b64encode(raw).decode("ascii")


def rsa_encrypt_short_link_payload(payload: bytes) -> bytes:
    """RSAES-PKCS1-v1_5 encrypt the IM protobuf in browser-sized chunks."""
    raw = bytes(payload)
    encrypted = bytearray()
    for offset in range(0, len(raw), RSA_CHUNK_BYTES):
        chunk = raw[offset:offset + RSA_CHUNK_BYTES]
        padding_length = RSA_BYTES - len(chunk) - 3
        padding = bytearray()
        while len(padding) < padding_length:
            padding.extend(byte for byte in secrets.token_bytes(padding_length) if byte)
        encoded = b"\x00\x02" + bytes(padding[:padding_length]) + b"\x00" + chunk
        cipher = pow(int.from_bytes(encoded, "big"), RSA_E, RSA_N)
        encrypted.extend(cipher.to_bytes(RSA_BYTES, "big"))
    return bytes(encrypted)


def _pb_varint(value: int) -> bytes:
    """Encode one protobuf unsigned varint without a runtime dependency."""
    value = int(value)
    if value < 0:
        raise ValueError("protobuf varint values must be non-negative")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _pb_field(number: int, wire_type: int, payload: bytes) -> bytes:
    return _pb_varint((int(number) << 3) | int(wire_type)) + payload


def _pb_string(number: int, value: str) -> bytes:
    raw = str(value).encode("utf-8")
    return _pb_field(number, 2, _pb_varint(len(raw)) + raw)


def _pb_bool(number: int, value: bool) -> bytes:
    return _pb_field(number, 0, _pb_varint(1 if value else 0))


def _pb_uint(number: int, value: int) -> bytes:
    return _pb_field(number, 0, _pb_varint(int(value)))


def _pb_message(number: int, value: bytes) -> bytes:
    return _pb_field(number, 2, _pb_varint(len(value)) + value)


def _pb_read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 64:
            raise ValueError("invalid protobuf varint")
    raise ValueError("truncated protobuf varint")


def _pb_read_fields(data: bytes) -> dict[int, list[tuple[int, Any]]]:
    """Read the small scalar/length-delimited subset used by the IM schema."""
    fields: dict[int, list[tuple[int, Any]]] = {}
    offset = 0
    while offset < len(data):
        tag, offset = _pb_read_varint(data, offset)
        number, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            value, offset = _pb_read_varint(data, offset)
        elif wire_type == 2:
            length, offset = _pb_read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("truncated protobuf field")
            value, offset = data[offset:end], end
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
        fields.setdefault(number, []).append((wire_type, value))
    return fields


def _pb_text(value: bytes) -> str:
    return value.decode("utf-8")


def encode_im_chat_command(command: Mapping[str, Any]) -> bytes:
    """Encode the captured ``ChatCommand`` message (fields 1..4)."""
    required = ("type", "useStrategy", "strategy", "info")
    missing = [key for key in required if key not in command]
    if missing:
        raise ValueError("ChatCommand missing captured fields: " + ", ".join(missing))
    # protobufjs emits fields in field-number order.  Keep the same order even
    # when a caller supplies an insertion-ordered mapping differently.
    return b"".join((
        _pb_uint(1, command["type"]),
        _pb_bool(2, command["useStrategy"]),
        _pb_uint(3, command["strategy"]),
        _pb_string(4, command["info"]),
    ))


def encode_im_chat_message(*, mid: str, ts: int, token: str, sender: str,
                           receiver: str, content: str, content_type: int,
                           nickname: str, group_chat: bool,
                           command: Optional[Mapping[str, Any]], ref_id: str,
                           trigger_source: int) -> bytes:
    """Encode browser ``ChatSendMessage`` + ``ChatOneMessage`` protobuf bytes.

    Every constructor field is mandatory at the Python boundary, including
    empty strings and an explicit ``command=None``.  This prevents callers
    from silently dropping fields that the browser supplies to protobufjs.
    Default-valued fields are omitted by protobuf wire encoding exactly as in
    the browser bundle.
    """
    command_bytes = b"" if command is None else encode_im_chat_command(command)
    send = b"".join((
        _pb_string(1, mid), _pb_uint(2, ts), _pb_string(3, token),
        _pb_string(4, sender), _pb_string(5, receiver),
        _pb_string(6, content), _pb_uint(7, content_type),
        _pb_string(8, nickname), _pb_bool(9, group_chat),
        _pb_message(10, command_bytes) if command is not None else b"",
        _pb_string(11, ref_id), _pb_uint(12, trigger_source),
    ))
    return _pb_uint(1, 1) + _pb_message(9, send)


def decode_im_chat_ack(payload: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode ``ChatOneMessage.chatACK`` and return browser field names."""
    outer = _pb_read_fields(bytes(payload))
    ack_values = outer.get(5)
    if not ack_values:
        raise ValueError("protobuf payload does not contain ChatACK field 5")
    ack = _pb_read_fields(ack_values[-1][1])
    def scalar(number: int, default: Any = 0) -> Any:
        values = ack.get(number)
        if not values:
            return default
        wire_type, value = values[-1]
        if wire_type == 0:
            return int(value)
        return value
    return {
        "mid": _pb_text(scalar(1, b"")),
        "messageid": _pb_text(scalar(2, b"")),
        "ts": int(scalar(3, 0)),
        "token": _pb_text(scalar(4, b"")),
        "code": int(scalar(5, 0)),
        "msg": _pb_text(scalar(6, b"")),
        "storeid": int(scalar(7, 0)),
    }


def decode_im_chat_message(payload: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode the browser ``ChatOneMessage.chatMessage`` envelope.

    ``ChatMessage`` is the inbound private-message branch (outer field 4):
    ``mid``, ``messageid``, ``ts``, ``token``, and a string ``payload``.  The
    payload is parsed as JSON when the current browser message uses JSON and
    is otherwise retained as the original string.
    """
    outer = _pb_read_fields(bytes(payload))
    values = outer.get(4)
    if not values:
        raise ValueError("protobuf payload does not contain ChatMessage field 4")
    fields = _pb_read_fields(values[-1][1])

    def text(number: int) -> str:
        entries = fields.get(number)
        return _pb_text(entries[-1][1]) if entries else ""

    def number(number: int) -> int:
        entries = fields.get(number)
        return int(entries[-1][1]) if entries else 0

    raw_payload = text(5)
    result: dict[str, Any] = {
        "mid": text(1),
        "messageid": text(2),
        "ts": number(3),
        "token": text(4),
        "payload": raw_payload,
    }
    try:
        result["payload_json"] = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        pass
    return result


def decode_im_one_message(payload: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode the observed ``ChatOneMessage`` inbound branches."""
    data = bytes(payload)
    outer = _pb_read_fields(data)
    result: dict[str, Any] = {}
    if outer.get(4):
        result["chatMessage"] = decode_im_chat_message(data)
    if outer.get(5):
        result["chatACK"] = decode_im_chat_ack(data)
    if not result:
        raise ValueError("protobuf payload has no supported ChatOneMessage branch")
    return result


def _insert_after(headers: Mapping[str, str], key: str, value: str,
                  after: str) -> dict[str, str]:
    """Insert a captured live-only header at its observed relative position."""
    result: dict[str, str] = {}
    inserted = False
    for name, current in headers.items():
        if name.lower() in {key.lower(), after.lower()}:
            if name.lower() == key.lower():
                continue
        result[name] = current
        if name.lower() == after.lower():
            result[key] = value
            inserted = True
    if not inserted:
        result[key] = value
    return result


class XHSWebSocket:
    def __init__(self, auth: XHSPcAuth, *, uid: Optional[str] = None,
                 device_id: Optional[str] = None, fingerprint: Optional[str] = None,
                 sid: Optional[str] = None, websocket_url: str = PUSH_URL):
        self.auth = auth
        self.uid = str(uid or auth.user_id or "")
        self.device_id = str(device_id or "")
        self.fingerprint = str(fingerprint or "")
        self.sid = str(sid or "")
        self.websocket_url = websocket_url
        self.ws = None
        # Frames received while waiting for a transport ack are replayed to
        # the caller through events() instead of being dropped.
        self._pending_texts: list[str] = []


    @staticmethod
    def _storage_value(storage: Mapping[str, Any], key: str) -> str:
        value = storage.get(key, "")
        if isinstance(value, (dict, list)):
            return ""
        return str(value or "")


    @classmethod
    def from_auth_storage(cls, auth: XHSPcAuth, *, websocket_url: str = PUSH_URL) -> "XHSWebSocket":
        """Build a push client from Auth-managed browser-equivalent storage.

        The three names are the exact keys used by the current web client.  A
        missing value remains empty and is rejected by ``connect`` rather than
        being fabricated.
        """
        local = dict(getattr(auth, "local_storage", {}) or {})
        session = dict(getattr(auth, "session_storage", {}) or {})
        token = local.get("RWP_LOGIN_TOKEN", "")
        if isinstance(token, str):
            try:
                token = json.loads(token)
            except json.JSONDecodeError:
                token = {}
        session_state = getattr(getattr(auth, "profile", None), "session", None)
        if isinstance(token, Mapping) and token.get("expiredAt") is not None:
            try:
                expired = int(token["expiredAt"]) <= int(time.time() * 1000)
            except (TypeError, ValueError):
                expired = True
            if expired:
                token = {}
                auth.update_runtime_state(local_storage={"RWP_LOGIN_TOKEN": ""})
                if session_state is not None:
                    session_state.set_rwp_login_token({})
        sid = token.get("aLt", "") if isinstance(token, Mapping) else ""
        uid = token.get("uid", "") if isinstance(token, Mapping) else ""
        if isinstance(token, Mapping) and token.get("uid") and auth.user_id:
            if str(token["uid"]) != str(auth.user_id):
                raise ValueError("RWP_LOGIN_TOKEN uid does not match the authenticated user")
        if not sid and session_state is not None:
            sid = (getattr(session_state, "rwp_login_token", {}) or {}).get("aLt", "")
        device_id = cls._storage_value(session, "XHS_TAB_DEVICE_ID")
        fingerprint = cls._storage_value(session, "XHS_RWP_FINGERPRINT")
        if session_state is not None:
            device_id = device_id or str(getattr(session_state, "tab_device_id", "") or "")
            fingerprint = fingerprint or str(getattr(session_state, "rwp_fingerprint", "") or "")
        return cls(
            auth,
            uid=str(uid or auth.user_id or ""),
            sid=str(sid or ""),
            device_id=device_id,
            fingerprint=fingerprint,
            websocket_url=websocket_url,
        )


    @staticmethod
    def message_id() -> str:
        return uuid.uuid4().hex[:14] + "-" + format(int(time.time() * 1000), "x")[-11:]


    @classmethod
    def envelope(cls, *, transport_type: int, message_id: Optional[str] = None,
                 body: Optional[Mapping[str, Any]] = None) -> str:
        frame = {"v": 1, "t": transport_type}
        if message_id is not None:
            frame["m"] = message_id
        if body is not None:
            frame["b"] = body
        return _compact(frame)


    def handshake(self) -> str:
        auth_info = {"authType": "generic", "sid": self.sid,
                     "uid": self.uid, "domain": "red"}
        device = {
            "deviceId": self.device_id,
            "fingerprint": self.fingerprint,
            "platform": "browser", "os": "web", "osVersion": "10.15",
            "deviceName": "Chrome", "appVersion": "131.0.0.0",
            "userAgent": BROWSER_UA,
        }
        payload = {"appId": "xhs-pc", "authInfo": auth_info,
                   "deviceInfo": device, "serviceTag": "",
                   "bizInfos": [
                       {"bizName": "dqa_chatsearch", "serializeType": "protobuf"},
                       {"bizName": "xhs_dots_pc", "serializeType": "protobuf"},
                       {"bizName": "push", "serializeType": "json"}],
                   "roomInfo": [], "roomInfos": [], "tagInfo": [],
                   "extInfo": {}, "state": 1}
        return self.envelope(transport_type=2, message_id=self.message_id(),
                             body={"d": {"a": 1, "s": 0, "b": payload}})


    def register(self, biz_name: str, serialize_type: str) -> str:
        return self.envelope(transport_type=2, message_id=self.message_id(),
                             body={"d": {"a": 1, "s": 1,
                                          "b": {"bizInfo": {"bizName": biz_name,
                                                             "serializeType": serialize_type},
                                                "register": True}}})


    @classmethod
    def transport_heartbeat(cls) -> str:
        """Return the exact RWP keepalive frame observed in Chrome."""
        return cls.envelope(transport_type=0)


    def business_frame(self, biz: str, payload: bytes | str, *, command: str,
                       service_id: str = "rrmp.o.l", action: int = 0) -> str:
        """Wrap a captured protobuf body for an RWP business channel."""
        if not biz or not command:
            raise ValueError("biz and command must be supplied from a browser capture")
        return self.envelope(transport_type=3, message_id=self.message_id(),
                             body={"d": {"a": int(action), "c": command, "biz": str(biz),
                                          "b": _b64(payload), "e": {}, "s": service_id}})


    def im_frame(self, payload: bytes | str, *, command: str) -> str:
        """Build an IM frame from the exact protobuf bytes captured from Chrome.

        ``payload`` may be raw protobuf bytes or an already serialized string.
        The protobuf schema is release-managed by XHS and is intentionally not
        guessed here; callers can pass the bytes produced by their captured UI.
        """
        # Chrome uses action=2 for client-originated IM ``sendMessage``
        # frames.  Action 0 is used by room heartbeats and server-style
        # dispatches, so it must not be reused for private-message sends.
        return self.business_frame("im", payload, command=command,
                                   service_id="rrmp.b.i", action=2)


    def im_chat_frame(self, *, mid: str, ts: int, token: str, sender: str,
                      receiver: str, content: str, content_type: int,
                      nickname: str, group_chat: bool,
                      command: Optional[Mapping[str, Any]], ref_id: str,
                      trigger_source: int) -> tuple[str, str]:
        """Build a browser-shaped IM ``sendMessage`` frame without sending it."""
        payload = encode_im_chat_message(
            mid=mid, ts=ts, token=token, sender=sender, receiver=receiver,
            content=content, content_type=content_type, nickname=nickname,
            group_chat=group_chat, command=command, ref_id=ref_id,
            trigger_source=trigger_source,
        )
        return self.im_frame(payload, command="sendMessage"), mid


    @staticmethod
    def _decode_text_frame(data: str) -> dict[str, Any]:
        try:
            frame = json.loads(data)
        except json.JSONDecodeError:
            return {"raw": data}
        # IM protobuf bytes travel in two envelopes: a single base64 string
        # (``b.d.b``, t=3 dispatches) or a t=4 push batch where ``b.d.b`` is a
        # list of items each carrying base64 in ``d`` — the same batch shape
        # as room pushes.  Preserve the original frame and add decoded views.
        decoded_views: dict[str, Any] = {}
        try:
            payload = frame.get("b", {}).get("d", {})
            if payload.get("biz") == "im":
                body = payload.get("b")
                if isinstance(body, str) and body:
                    decoded_views["im"] = decode_im_one_message(base64.b64decode(body))
                elif isinstance(body, list):
                    items: list[dict[str, Any]] = []
                    for item in body:
                        if isinstance(item, dict) and isinstance(item.get("d"), str):
                            try:
                                items.append(decode_im_one_message(base64.b64decode(item["d"])))
                            except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
                                items.append({"decode_error": str(exc), "envelope": dict(item)})
                        else:
                            items.append({"decode_error": "item.d is not base64 text",
                                          "envelope": item if isinstance(item, dict) else {"raw": item}})
                    if items:
                        decoded_views["im"] = items
        except (AttributeError, KeyError, TypeError, ValueError, binascii.Error) as exc:
            # Preserve an IM event when a future protobuf branch or
            # encoding appears; callers can inspect the original
            # base64 in the untouched frame instead of losing it.
            decoded_views["im_error"] = str(exc)
        if "im" not in decoded_views:
            # The reply to a sent IM frame (t=3) carries the chatACK protobuf
            # in ``b.a.b``.  Handshake replies keep an object there, so only a
            # string body is attempted, and non-IM payloads simply fail to
            # parse and are left untouched.
            try:
                ack_body = frame.get("b", {}).get("a", {}).get("b")
                if isinstance(ack_body, str) and ack_body:
                    decoded_views["im"] = decode_im_one_message(base64.b64decode(ack_body))
            except (AttributeError, KeyError, TypeError, ValueError,
                    binascii.Error, UnicodeDecodeError):
                pass
        if decoded_views:
            frame["decoded"] = decoded_views
        return frame


class XHSLiveAPI:
    def __init__(self, auth: XHSPcAuth):
        if not isinstance(auth, XHSPcAuth):
            raise TypeError("XHSLiveAPI requires XHSPcAuth")
        self.auth = auth
        self.http = auth.http_client
        self.base_url = LIVE_ORIGIN
        self.edith_url = auth.origin("api")


    def _request(self, origin: str, api: str, *, method: str = "GET",
                 data: Any = "", referer: str = "https://www.xiaohongshu.com/",
                 extra_headers: Optional[Mapping[str, str]] = None):
        context = self.auth.next_sign_context(api)
        b1 = self.auth.current_b1(context["now"])
        headers, cookies, body = generate_request_params(
            self.auth.cookies, api, data, method,
            user_id=self.auth.user_id, b1=b1, dsl_pair=self.auth.dsl_pair,
            doc_cookie=self.auth.sign_cookie, tier=context["tier"],
            sign_context=context,
            include_client_hints=False,
            include_trace_headers=origin != self.base_url,
        )
        headers["referer"] = referer
        target = origin + api
        headers["user-agent"] = BROWSER_UA
        if origin == self.base_url:
            headers = _insert_after(headers, "xy-common-params", "platform=web", "referer")
        for key, value in dict(extra_headers or {}).items():
            # `c_device_id` is the one current PC request that appears between
            # x-xray-traceid and x-t; other captured live metadata follows the
            # signing headers after x-s-common.
            anchor = "x-xray-traceid" if str(key).lower() == "c_device_id" else "x-s-common"
            headers = _insert_after(headers, str(key), str(value), anchor)
        wire_cookies = self.auth.cookies_for_url(target, cookies)
        if origin == self.base_url:
            headers = build_pc_live_headers(headers, wire_cookies, method=method)
        else:
            headers = build_pc_business_headers(headers, wire_cookies, api=api, method=method)
        if method.upper() == "GET":
            response = self.http.get(target, headers=headers, cookies=wire_cookies,
                                     proxies=self.auth.proxies, timeout=REQUEST_TIMEOUT)
        else:
            response = self.http.post(target, headers=headers, data=body.encode("utf-8"),
                                      cookies=wire_cookies, proxies=self.auth.proxies,
                                      timeout=REQUEST_TIMEOUT)
        response_cookies = getattr(response, "cookies", None)
        if response_cookies is not None and hasattr(response_cookies, "items"):
            updates = dict(response_cookies.items())
            if updates:
                # Preserve host-only edge cookies (notably acw_tc) for the
                # next request to this exact live or IM origin.
                self.auth.update_cookies(updates, source_url=target)
        response.raise_for_status()
        return response.json()


    def get_celestial_lt(self):
        """Refresh the browser-issued RWP login token.

        The endpoint requires the current tab device id as ``c_device_id``;
        callers must provide it through the captured session storage.
        """
        session = dict(getattr(self.auth, "session_storage", {}) or {})
        device_id = str(session.get("XHS_TAB_DEVICE_ID", "") or "")
        if not device_id:
            profile = getattr(self.auth, "profile", None)
            session_state = getattr(profile, "session", None)
            device_id = str(getattr(session_state, "tab_device_id", "") or "")
        if not device_id:
            raise ValueError("XHS_TAB_DEVICE_ID is required for celestial/lt")
        result = self._request(
            self.edith_url,
            "/api/sns/web/v1/celestial/lt",
            method="GET",
            extra_headers={"c_device_id": device_id},
        )
        data = (result or {}).get("data") or {}
        # Keep the returned token available to a subsequent push connection,
        # while preserving the browser's exact response field names.
        ttl_seconds = data.get("expiredTime", data.get("expired_time"))
        expires = None
        if ttl_seconds is not None:
            try:
                ttl_seconds = int(ttl_seconds)
                expires = int(time.time() * 1000) + ttl_seconds * 1000 // 2
            except (TypeError, ValueError):
                expires = None
        token = {
            "aLt": data.get("aLt", data.get("a_lt", "")),
            "rLt": data.get("rLt", data.get("r_lt", "")),
            "expiredAt": expires,
            "uid": self.auth.user_id,
        }
        if token["aLt"]:
            local = dict(getattr(self.auth, "local_storage", {}) or {})
            local["RWP_LOGIN_TOKEN"] = json.dumps(token, ensure_ascii=False, separators=(",", ":"))
            self.auth.update_runtime_state(local_storage=local)
        return result
