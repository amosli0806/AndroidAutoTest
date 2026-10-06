# services/wecom_notify.py
"""企业微信自建应用推送：把消息发到自己的企业微信应用，直达个人微信。

为什么选它（用户 2026-10-06 拍板）：个人手机号即可免费注册企业微信、无需
认证、无需营业执照，创建「自建应用」拿到三件套后，关注「微信插件」，推送的
消息直接出现在个人微信里 —— 全程不依赖 Server酱/PushPlus 等第三方服务号。

三件套：
  - 企业ID（corpid）：「我的企业」页面最下方
  - 应用ID（agentid）+ 应用Secret：应用管理 → 自建 → 创建应用 → 应用详情页
  - 收消息：我的企业 → 微信插件 → 扫码关注，之后消息直达微信

发送流程：gettoken（corpid+secret 换 access_token）→ message/send（应用消息）。

设计约定：推送是**尽力而为**的旁路——后台线程发送、超时短、失败只打日志，
绝不阻塞或打断主流程（定时任务/性能采集的结果不能因为推送失败而受影响）。
"""
import threading

import requests

_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


def push(cfg: dict, title: str, detail: str = "") -> None:
    """异步推送一条消息到企业微信应用。cfg 为 Settings.get_notify_config() 返回值。

    未开开关 / 三件套缺任一 时静默返回；网络失败只打日志。
    """
    if not cfg.get("push_enabled"):
        return
    corpid = (cfg.get("wecom_corpid") or "").strip()
    agentid = (cfg.get("wecom_agentid") or "").strip()
    secret = (cfg.get("wecom_secret") or "").strip()
    if not (corpid and agentid and secret):
        return

    content = f"{title}\n{detail}" if detail else title
    threading.Thread(
        target=_do_send, args=(corpid, agentid, secret, content),
        daemon=True, name="wecom-notify",
    ).start()


def _do_send(corpid: str, agentid: str, secret: str, content: str) -> None:
    try:
        # 1) 换 access_token
        r = requests.get(
            _TOKEN_URL,
            params={"corpid": corpid, "corpsecret": secret},
            timeout=5,
        )
        d = r.json()
        token = d.get("access_token")
        if not token:
            # 业务失败（HTTP 仍是 200，错误码在 body 里）：写日志供用户排查
            print(f"[wecom] 获取 access_token 失败: "
                  f"errcode={d.get('errcode')} {d.get('errmsg', '')[:200]}")
            return
        # 2) 发应用消息（touser=@all：个人注册的企业就自己一个成员）
        r2 = requests.post(
            f"{_SEND_URL}?access_token={token}",
            json={
                "touser": "@all",
                "msgtype": "text",
                "agentid": int(agentid),
                "text": {"content": content},
            },
            timeout=5,
        )
        body = r2.json()
        if body.get("errcode") == 0:
            print("[wecom] 推送成功")
        else:
            # 常见：60020 = 来源 IP 不在企业可信 IP 白名单（去应用详情页配置）
            print(f"[wecom] 推送失败 errcode={body.get('errcode')}: "
                  f"{body.get('errmsg', '')[:200]}")
    except Exception as e:  # 网络/超时/证书等一律吞掉，只留日志
        print(f"[wecom] 推送失败: {type(e).__name__}: {str(e)[:120]}")
