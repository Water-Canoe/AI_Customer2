# 私信接口提取自固定上游版本；来源与修改见SOURCE.md。

import base64

import hashlib

import json

import re

import time

import urllib

import uuid

from curl_cffi import CurlError
from curl_cffi.const import CurlECode

from app.vendor.douyin_spider.utils import http_client as requests


from google.protobuf.json_format import MessageToDict as _message_to_dict

def protobuf_to_dict(message):
    return _message_to_dict(message, preserving_proto_field_name=True)

import app.vendor.douyin_spider.static.Response_pb2 as ResponseProto

from app.vendor.douyin_spider.builder.header import HeaderBuilder, HeaderType

from app.vendor.douyin_spider.builder.params import Params

from app.vendor.douyin_spider.builder.proto import ProtoBuilder

from app.vendor.douyin_spider.utils.fingerprint import get_profile

from app.vendor.douyin_spider.utils.dy_util import splice_url, generate_a_bogus, generate_msToken, trans_cookies, generate_a_bogus_pure


class MessageSendUncertain(RuntimeError):
    """最终发送请求已开始，但未能可靠解析平台是否接受；禁止自动重发。"""


class MessagePreparationError(RuntimeError):
    """明确发生于消息提交前的异常；只携带可安全记录的阶段和异常类型。"""

    def __init__(self, stage, error):
        super().__init__('私信发送准备失败')
        self.stage = stage
        self.error_type = type(error).__name__
        self.error_code = int(error.code) if isinstance(error, CurlError) else None
        self.retryable = isinstance(error, CurlError) and error.code in {
            CurlECode.COULDNT_RESOLVE_PROXY, CurlECode.COULDNT_RESOLVE_HOST,
            CurlECode.COULDNT_CONNECT, CurlECode.HTTP2, CurlECode.PARTIAL_FILE,
            CurlECode.OPERATION_TIMEDOUT, CurlECode.SSL_CONNECT_ERROR,
            CurlECode.GOT_NOTHING, CurlECode.SEND_ERROR, CurlECode.RECV_ERROR,
            CurlECode.HTTP2_STREAM,
        }


def check_risk_response(resp):
    """抖音风控拒绝时返回 HTTP 200 + 空 body 或 HTML 挑战页，原因只在响应头里，
    这里翻译成可读异常。直接 `resp.json()` 只会得到毫无信息量的 JSONDecodeError。
    """
    text = (resp.text or "").lstrip()
    if text.startswith("{") or text.startswith("["):
        return
    bd = resp.headers.get("X-Vc-Bdturing-Parameters")
    if bd:
        subtype = ""
        try:
            info = json.loads(base64.b64decode(bd + "=" * (-len(bd) % 4)))
            subtype = info.get("subtype", "")
        except Exception:
            pass
        raise RuntimeError(
            f"触发人机验证（bdturing {subtype or '未知类型'}）。"
            f"需在浏览器完成验证、或更换 IP / 降低请求频率后重试。logid={resp.headers.get('X-Tt-Logid')}")
    pp = resp.headers.get("X-Tt-Verify-Passport-Decision")
    if pp:
        scene = ""
        try:
            scene = json.loads(pp)["event_params"]["verify_scene"]
        except Exception:
            pass
        raise RuntimeError(
            f"需要二次身份验证（scene={scene or '未知'}）。"
            f"多为缺少 x-tt-session-dtrait 或账号风控所致。logid={resp.headers.get('X-Tt-Logid')}")
    if not text:
        raise RuntimeError(
            f"接口返回空响应（HTTP {resp.status_code}），通常是签名参数不对或被风控拦截。"
            f"logid={resp.headers.get('X-Tt-Logid')}")
    if "__ac_nonce" in text or "_$jsvmprt" in text:
        raise RuntimeError(
            f"命中 acrawler 挑战页（HTTP {resp.status_code}）。需要 __ac_signature，"
            f"该签名尚未纯算实现。logid={resp.headers.get('X-Tt-Logid')}")
    raise RuntimeError(
        f"接口返回非 JSON（HTTP {resp.status_code}），前 120 字：{text[:120]!r}。"
        f"logid={resp.headers.get('X-Tt-Logid')}")


class DouyinAPI:
    douyin_url = "https://www.douyin.com"


    @staticmethod
    def get_user_profile(auth, user_id: str) -> dict:
        """沿用上游用户资料接口，以私信的数字 UID 查询对方资料。"""
        refer = 'https://www.douyin.com/'
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(refer)
        headers.with_uifid(auth)
        params = Params()
        params.add_param('device_platform', 'webapp').add_param('aid', '6383').add_param('channel', 'channel_pc_web')
        params.add_param('publish_video_strategy_type', '2').add_param('source', 'channel_pc_web')
        params.add_param('user_id', user_id).add_param('sec_user_id', '').add_param('personal_center_strategy', '1')
        params.add_param('profile_other_record_enable', '1').add_param('land_to', '1')
        params.with_platform(round_trip_time='0').with_web_id(auth, refer).with_uifid(auth)
        params.add_param('msToken', auth.msToken).with_a_bogus().with_verify_fp(auth)
        response = requests.get(f'{DouyinAPI.douyin_url}/aweme/v1/web/user/profile/other/',
                                headers=headers.get(), params=params.get(), cookies=auth.cookie,
                                verify=True, timeout=15)
        check_risk_response(response)
        return response.json()


    @staticmethod
    def get_my_uid(auth, **kwargs) -> int:
        """
        获取自己的用户ID.
        :param auth: DouyinAuth object.
        :return: 用户ID.
        """
        url = 'https://www.douyin.com/aweme/v1/web/query/user/'
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = 'https://www.douyin.com/'
        headers.set_header('referer', refer)
        headers.with_uifid(auth)
        params = Params()
        params.add_param('publish_video_strategy_type', '2')
        params.with_platform()
        params.with_uifid(auth)
        params.with_web_id(auth, refer)
        params.add_param('verifyFp', auth.cookie['s_v_web_id'])
        params.add_param('fp', auth.cookie['s_v_web_id'])
        params.with_a_bogus()
        resp = requests.get(url, params=params.get(), verify=True, headers=headers.get(), cookies=auth.cookie)
        check_risk_response(resp)
        resp_json = resp.json()
        return int(resp_json['user_uid'])


    @staticmethod
    def create_conversation(auth, to_user_id: int, **kwargs):
        """
        创建私信对话.
        :param auth: DouyinAuth object.
        :param to_user_id: 私信对话接收者ID.
        :return: 私信对话ID.
        """
        url = "https://imapi.douyin.com/v2/conversation/create"
        requestProto = ProtoBuilder.build_create_conversation_request(auth, to_user_id, auth.get_uid())
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')

        resp = requests.post(
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            verify=True
        )
        responseProto = ResponseProto.Response()
        responseProto.ParseFromString(resp.content)
        resp_json = protobuf_to_dict(responseProto)
        conversation = resp_json['body']['create_conversation_v2_body']['conversation_info_list'][0]
        conversation_id = conversation['conversation_id']
        conversation_short_id, ticket = int(conversation['conversation_short_id']), conversation['ticket']
        return conversation_id, conversation_short_id, ticket


    @staticmethod
    def get_identity_security_token(auth, force=False, **kwargs):
        """Fetch the short-lived identity token required by PC IM sends.

        The web IM bundle started calling ``/passport/safe/get_identity_security_token/``
        before ``imapi/v1/message/send`` in the 2026 rollout.  The returned
        token is not a Cookie and is instead copied into the protobuf header
        map together with the returned device id.  It is tied to the current
        ticket/dtrait session, so never synthesize it or carry it across Auth
        instances.
        """
        now = time.time()
        cached = str(getattr(auth, 'identity_security_token', '') or '')
        cached_ts = float(getattr(auth, 'identity_security_token_ts', 0) or 0)
        if cached and not force and now - cached_ts < 240:
            return cached, str(getattr(auth, 'identity_security_device_id', '') or '')

        api = '/passport/safe/get_identity_security_token/'
        referer = kwargs.get('referer') or 'https://www.douyin.com/chat?isPopup=1'
        trace_id = uuid.uuid4().hex[:8]
        params = {
            'passport_jssdk_version': '4.2.3',
            'passport_jssdk_type': 'lite',
            'is_from_ttaccountsdk': '1',
            'aid': '6383',
            'language': 'zh',
            'scene': 'web_im',
            'auto_retry_req': '0',
            'skip_verify': 'false',
            'identity_token_force_get_tag': '0',
            'biz_trace_id': trace_id,
            'id_token_version': '1.2.10',
            'msToken': auth.msToken,
        }
        # The browser signs the query before appending a_bogus itself; no
        # verifyFp/fp fields are present on this passport endpoint.
        params['a_bogus'] = generate_a_bogus(
            splice_url(params), host='www.douyin.com'
        )

        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(referer)
        headers.set_header('accept', 'application/json, text/javascript')
        headers.set_header(
            'x-tt-passport-csrf-token',
            (auth.cookie or {}).get('passport_csrf_token', '')
            or (auth.cookie or {}).get('passport_csrf_token_default', ''),
        )
        headers.set_header('x-tt-passport-trace-id', trace_id)
        # This is a write-adjacent, ticket-protected passport call.  Use the
        # same bd-ticket-guard client-data and dtrait generation as the send
        # request so both requests belong to one security session.
        headers.with_bd(api, auth)

        resp = requests.get(
            f'{DouyinAPI.douyin_url}{api}', params=params,
            headers=headers.get(), cookies=auth.cookie, verify=True,
        )
        check_risk_response(resp)
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError('身份安全 token 接口返回不可解析响应') from exc
        data = payload.get('data') or {}
        token = str(data.get('identity_security_token') or '')
        device_id = str(data.get('device_id') or '')
        if payload.get('message') not in (None, 'success') or not token:
            error_code = data.get('error_code')
            missing_cookie_hints = [
                name for name in (
                    'UIFID', 'passport_mfa_token', 'x_tt_token',
                    'passport_csrf_token',
                ) if not (auth.cookie or {}).get(name)
            ]
            hint = (
                f'; 当前会话缺少/未同步的浏览器 Cookie: {", ".join(missing_cookie_hints)}'
                if error_code == 3 and missing_cookie_hints else ''
            )
            raise RuntimeError(f'身份安全 token 获取失败{hint}')
        auth.identity_security_token = token
        auth.identity_security_device_id = device_id
        auth.identity_security_token_ts = now
        return token, device_id


    @staticmethod
    def _send_message_raw(auth, conversation_id, conversation_short_id, ticket,
                          message_type, content, *, ext=None,
                          mentioned_users=None, client_message_id=None,
                          **kwargs) -> dict:
        """Send one already-built PC IM message payload.

        Rich-media helpers upload first and call this method with the message
        type/content returned by :class:`DouyinIMMedia`. Keeping one raw send
        path ensures all message types receive the same current ticket-guard,
        identity-security and a_bogus parameters.
        """
        url = 'https://imapi.douyin.com/v1/message/send'
        stage = 'send_signature'
        try:
            headers = HeaderBuilder().build(HeaderType.PROTOBUF)
            headers.set_header('referer', 'https://www.douyin.com/')
            headers.with_bd('/v1/message/send', auth)
            stage = 'identity_token'
            identity_token, identity_device_id = DouyinAPI.get_identity_security_token(auth)
            stage = 'message_payload'
            requestProto = ProtoBuilder.build_send_message_request(
                auth, conversation_id, conversation_short_id, ticket,
                content=content, message_type=message_type, ext=ext,
                mentioned_users=mentioned_users, client_message_id=client_message_id,
                identity_security_token=identity_token,
                identity_security_device_id=identity_device_id,
            )
            # Browser order is msToken -> a_bogus -> verifyFp -> fp.
            params = {'msToken': auth.msToken}
            params['a_bogus'] = generate_a_bogus(splice_url(params), host='www.douyin.com')
            params['verifyFp'] = auth.cookie['s_v_web_id']
            params['fp'] = auth.cookie['s_v_web_id']
            payload = requestProto.SerializeToString()
        except Exception as exc:
            raise MessagePreparationError(stage, exc) from None
        # 此边界之后，请求可能已被平台处理，任何网络异常都不能自动重发。
        try:
            resp = requests.post(url, params=params, headers=headers.get(), verify=True, cookies=auth.cookie,
                                 data=payload)
        except Exception:
            # 网络异常可能发生在服务器已经接收之后，不能直接归为发送失败。
            raise MessageSendUncertain('发送结果待确认，请勿直接重发') from None
        if resp.status_code >= 500:
            raise MessageSendUncertain('平台未能确认发送结果，请勿直接重发')
        if resp.status_code >= 400:
            return {'http_status': resp.status_code, 'message': 'REJECTED'}
        try:
            responseProto = ResponseProto.Response()
            responseProto.ParseFromString(resp.content)
            resp_json = protobuf_to_dict(responseProto)
        except Exception:
            raise MessageSendUncertain('平台返回无法解析，发送结果待确认') from None
        if not resp_json.get('message'):
            raise MessageSendUncertain('平台未返回明确发送状态，发送结果待确认')
        return {'http_status': resp.status_code, **resp_json}


    @staticmethod
    def get_device_id(auth, **kwargs) -> str:
        """
        获取设备ID.
        :param auth: DouyinAuth object.
        :return: 设备ID.
        """
        url = "https://www.douyin.com/aweme/v1/web/query/user"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/discover"
        headers.set_header("referer", refer)
        headers.with_uifid(auth)
        params = Params()
        (params
         .with_platform()
         .add_param("publish_video_strategy_type", "2")
         .with_web_id(auth, refer)
         .with_ms_token()
         .add_param('verifyFp', auth.cookie['s_v_web_id'])
         .add_param('fp', auth.cookie['s_v_web_id'])
         .with_a_bogus()
         )
        resp = requests.get(url, params=params.get(), verify=True, headers=headers.get(), cookies=auth.cookie)
        check_risk_response(resp)
        resp_json = resp.json()
        return resp_json['id']
