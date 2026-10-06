# services/webhook_notify.py
"""微信/IM 推送：把消息转成 HTTP 请求推给第三方中转服务。

背景：微信个人号不开放「服务端主动推消息」的接口，市面上所有「微信推送」都
走第三方中转（用户自己注册拿 key）。本服务做成**通用 webhook**，兼容：
  - Server酱（serverchan）：填 SendKey，地址自动拼 sctapi.ftqq.com
  - PushPlus（pushplus）：填 token，地址自动拼 pushplus.plus
  - 企业微信群机器人（wecom）/ 钉钉机器人（dingtalk）：填完整 webhook URL
  - 自定义（custom）：填完整 URL，按 {title, content} POST

设计约定：推送是**尽力而为**的旁路——后台线程发送、超时短、失败只打日志，
绝不阻塞或打断主流程（定时任务/性能采集的结果不能因为推送失败而受影响）。
"""
import sys
import threading

import requests

# 各类型默认地址模板
_DEFAULT_URLS = {
    "serverchan": "https://sctapi.ftqq.com/{key}.send",
    "pushplus": "http://www.pushplus.plus/send",
}
# 需要用户填完整 webhook URL 的类型
_URL_REQUIRED = {"wecom", "dingtalk", "custom"}


def _build_payload(kind: str, title: str, detail: str) -> dict:
    """按类型拼请求体。返回 (url, data)；url 为空表示配置不足、不应发送。"""
    if kind == "serverchan":
        return "serverchan", {"title": title, "desp": detail or title}
    if kind == "pushplus":
        return "pushplus", {"title": title, "content": detail or title}
    if kind in ("wecom", "dingtalk"):
        # 两者都是 markdown 里用换行分隔标题和正文
        return "wecom", {"msgtype": "text", "text": {"content": f"{title}\n{detail}" if detail else title}}
    # custom：约定 {title, content}
    return "custom", {"title": title, "content": detail or title}


def push(cfg: dict, title: str, detail: str = "") -> None:
    """异步推送一条消息。cfg 为 Settings.get_notify_config() 返回的字典。

    配置不足（没开开关 / 缺 key / 缺 url）时静默返回；网络失败只打日志。
    """
    if not cfg.get("webhook_enabled"):
        return
    kind = cfg.get("webhook_type") or "serverchan"
    key = (cfg.get("webhook_key") or "").strip()
    url = (cfg.get("webhook_url") or "").strip()

    # 决定最终 URL 与请求体
    body_kind, data = _build_payload(kind, title, detail)

    if kind in _URL_REQUIRED:
        if not url:
            return
        final_url = url
    else:
        if not key:
            return
        if url:
            # 用户手动覆盖了地址（如自建镜像），仍把 key 传给模板
            final_url = url
            if kind == "pushplus":
                data["token"] = key
            elif kind == "serverchan":
                data["key"] = key
        else:
            if kind == "serverchan":
                final_url = _DEFAULT_URLS["serverchan"].format(key=key)
            else:  # pushplus
                final_url = _DEFAULT_URLS["pushplus"]
                data["token"] = key

    # 后台线程发送，超时短，失败静默
    threading.Thread(
        target=_do_post, args=(final_url, data), daemon=True, name="webhook-notify"
    ).start()


def _do_post(url: str, data: dict) -> None:
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:  # 网络/超时/证书等一律吞掉，只留日志
        print(f"[webhook] 推送失败: {type(e).__name__}: {str(e)[:120]}")
