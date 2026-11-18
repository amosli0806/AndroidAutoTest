"""MCP 工具分组与按需暴露（opt-in，默认 full）。

默认仍暴露全部工具（兼容现有客户端）；需要省 token 的用户可以通过
`--tools=core` 或 `data/mcp_config.json` 主动收窄。分组与第四章方案一致：

* core    设备/定位/操作 + 用例执行主链路
* gen     用例生成（草稿 / 保存）
* ops     ADB / 性能 / 长任务 / 设备影响型操作
* content 用例维护、套件、元素、接口、知识库、报告、语音

未在映射表里的新增工具默认归入 core，不会因为忘记标注而“消失”。
"""
from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from utils.app_paths import data_path

CORE = "core"
GEN = "gen"
OPS = "ops"
CONTENT = "content"

TOOL_GROUPS = {
    CORE: frozenset({
        "device_list", "screenshot", "ui_find", "ui_dump", "tap",
        "input_text", "swipe", "press_key", "launch_app", "wait_until",
        "assert", "list_cases", "run_case", "run_case_status",
        "run_case_stop", "run_suite",
    }),
    GEN: frozenset({
        "explore_and_draft", "draft_status", "save_case",
    }),
    OPS: frozenset({
        "adb_pull", "adb_push", "adb_device_info", "adb_logcat_snapshot",
        "logcat_start", "logcat_read", "logcat_stop",
        "perf_start", "perf_stop", "perf_status", "perf_sessions",
        "perf_report", "perf_compare", "perf_baseline_save",
        "perf_baseline_delete", "perf_baselines",
        "monkey_start", "monkey_status", "monkey_stop",
        "tcpdump_start", "tcpdump_stop",
        "screenrecord_start", "screenrecord_stop",
        "weak_net_apply", "weak_net_clear", "hprof_dump",
        "device_reboot", "app_install", "app_uninstall", "app_clear_data",
    }),
    CONTENT: frozenset({
        "case_copy", "case_rename", "case_delete", "step_update",
        "step_move", "suite_save", "suite_delete", "suite_list",
        "element_upsert", "element_list", "api_run", "api_list",
        "knowledge_list", "knowledge_read", "knowledge_upsert_note",
        "knowledge_delete_note", "report_list", "report_read",
        "voice_list_cases", "voice_speak", "voice_verify",
        "voice_settings_get", "voice_settings_update",
        "ai_analyze_failure",
    }),
}

PROFILES = {
    "core": (CORE,),
    "standard": (CORE, CONTENT),
    "full": (CORE, GEN, OPS, CONTENT),
}
DEFAULT_PROFILE = "full"
CONFIG_FILE = "mcp_config.json"


def all_grouped() -> set:
    names = set()
    for members in TOOL_GROUPS.values():
        names |= set(members)
    return names


def _expand(token: str, grouped: set, known: set) -> Optional[set]:
    """把「组名 / profile / 单工具」展开成工具集合；不认识返回 None。"""
    if token in PROFILES:
        result = set()
        for group in PROFILES[token]:
            result |= set(TOOL_GROUPS[group])
        return result & known
    if token in TOOL_GROUPS:
        return set(TOOL_GROUPS[token]) & known
    if token in known:
        return {token}
    return None


def _members_for(profile: str, unassigned: set) -> set:
    result = set()
    for group in PROFILES.get(profile, PROFILES[DEFAULT_PROFILE]):
        result |= set(TOOL_GROUPS[group])
    # 未标注组的新工具默认进 core
    result |= unassigned
    return result


def _apply_changes(base: set, add: Iterable[str], remove: Iterable[str],
                   grouped: set, known: set,
                   warnings: list) -> set:
    result = set(base)
    for token in add or []:
        expanded = _expand(str(token).strip(), grouped, known)
        if expanded is None:
            warnings.append(f"忽略未知的工具/组：{token}")
            continue
        result |= expanded
    for token in remove or []:
        expanded = _expand(str(token).strip(), grouped, known)
        if expanded is None:
            warnings.append(f"忽略未知的工具/组：{token}")
            continue
        result -= expanded
    return result


def parse_tools_arg(argv: Iterable[str]) -> Optional[str]:
    """从命令行找 `--tools=...` 或 `--tools ...`，返回值原样字符串。"""
    argv = list(argv)
    for i, item in enumerate(argv):
        if item.startswith("--tools="):
            return item.split("=", 1)[1]
        if item == "--tools" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def read_config() -> dict:
    path = data_path(CONFIG_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tools = data.get("tools")
        return tools if isinstance(tools, dict) else {}
    except Exception:
        return {}


def resolve(argv: Optional[Iterable[str]] = None,
            known_tools: Optional[Iterable[str]] = None):
    """返回 (启用的工具集合, 说明文本, warnings)。

    优先级：`--tools=` > `data/mcp_config.json` > 默认 full。
    """
    known = set(known_tools or [])
    grouped = all_grouped() & known if known else all_grouped()
    unassigned = known - grouped if known else set()
    warnings: list = []
    spec = parse_tools_arg(argv or [])

    if spec is not None:
        tokens = [t.strip() for t in spec.split(",") if t.strip()]
        enabled = set()
        remove = []
        for token in tokens:
            if token.startswith("-"):
                remove.append(token[1:])
                continue
            expanded = _expand(token, grouped, known or grouped)
            if expanded is None:
                warnings.append(f"忽略未知的工具/组：{token}")
                continue
            enabled |= expanded
        if not enabled and not remove:
            warnings.append("--tools 未命中任何工具，回退为 full")
            enabled = set(known or grouped) | unassigned
        elif not enabled:
            # 只有排除项（--tools=-monkey_start）：从 full 开始减
            enabled = set(known or grouped) | unassigned
        enabled = _apply_changes(enabled, [], remove, grouped,
                                 known or grouped, warnings)
        source = f"命令行 --tools={spec}"
    else:
        cfg = read_config()
        profile = str(cfg.get("profile") or DEFAULT_PROFILE).strip().lower()
        if profile not in PROFILES:
            warnings.append(f"配置里的 profile 不认识：{profile}，回退为 full")
            profile = DEFAULT_PROFILE
        enabled = _members_for(profile, unassigned)
        enabled &= (known or grouped)
        enabled = _apply_changes(enabled, cfg.get("add") or [],
                                 cfg.get("remove") or [], grouped,
                                 known or grouped, warnings)
        source = f"配置 profile={profile}" if cfg else f"默认 {DEFAULT_PROFILE}"

    # 未标注组的新工具永远进 core，避免“忘记标注就消失”
    enabled |= unassigned
    return enabled, source, warnings
