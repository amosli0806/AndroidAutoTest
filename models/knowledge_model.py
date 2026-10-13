# models/knowledge_model.py
"""个人知识库的数据模型：按项目分门别类记录「差异点 / 踩坑 / 环境 / 速查」。

为什么需要它：
    一个测试同学往往同时负责多个项目（不同车型 / 不同 ROM），每个项目都有
    自己的坑与差异。这些信息散在聊天记录、便签、脑子里，换项目就忘、踩过的
    坑还会再踩。这个模型负责把它们结构化地落盘，便于事后检索。

两条设计约定（写代码时别绕开）：

  1. **分类固定四类**（见 CATEGORIES），不做自由目录。
     固定分类省掉了「这条该往哪写」的决策成本 —— 对一个随手记的库来说，
     决策成本比分类的精确度重要得多。

  2. **「速查」不另建数据结构**。
     它就是 category == 速查 的普通条目，只是在界面上额外置顶渲染成卡片带。
     一份数据、多一种渲染，避免出现「卡片里的值和正文对不上」这种双份真相。

图片资源：`data/knowledge_assets/<note_id>/assets/<文件名>`，
正文里用相对路径 `assets/<文件名>` 引用 —— 这个相对路径**在应用内和导出后都成立**
（导出时按项目建目录，图片平铺进 `<项目>/assets/`），所以导出不需要改写链接。
文件名里带 note_id，天然全局唯一，平铺不会撞名。

本文件不依赖 Qt：数据层只管存取与检索，界面层见 views/knowledge_view.py。
"""
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from utils.app_paths import data_path, get_data_dir

# ---------- 固定分类（列表顺序 = 界面上树的展示顺序）----------
CATEGORY_QUICK = "速查"
CATEGORY_DIFF = "差异点"
CATEGORY_PITFALL = "踩坑"
CATEGORY_ENV = "环境"
CATEGORIES = [CATEGORY_QUICK, CATEGORY_DIFF, CATEGORY_PITFALL, CATEGORY_ENV]

# 新建项目时「用标准模板」一键生成的骨架：分类 / 标题 / 正文提示。
# 空白项目最难的是「不知道写什么」，给个骨架比给个空编辑器有用。
PROJECT_TEMPLATE = [
    (CATEGORY_QUICK, "项目速查",
     "- 包名：\n- 启动 Activity：\n- 常用 adb 命令：\n- 输入法方案：\n"),
    (CATEGORY_DIFF, "与其他项目的差异",
     "- \n"),
    (CATEGORY_PITFALL, "踩过的坑",
     "### 现象\n\n### 原因\n\n### 解决\n"),
    (CATEGORY_ENV, "环境信息",
     "- 设备型号：\n- 系统版本：\n- adb 版本：\n"),
]

# 图片资源目录（data/ 下），条目图片放在 <ASSETS_DIR_NAME>/<note_id>/assets/
ASSETS_DIR_NAME = "knowledge_assets"

# 正文里引用图片的相对路径前缀，导出时也用它
ASSET_LINK_PREFIX = "assets/"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_name(name: str, fallback: str = "未命名") -> str:
    """把项目 / 条目名转成合法的文件名（Windows 下 : * ? " < > | \\ / 都非法）"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" .")
    return cleaned[:80] or fallback


@dataclass
class KbProject:
    """一个项目（通常对应一款车型 / 一套 ROM）。"""
    id: str
    name: str
    order: int = 0
    created: str = ""

    def to_dict(self):
        return {'id': self.id, 'name': self.name,
                'order': self.order, 'created': self.created}

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=str(data.get('id', '')),
            name=str(data.get('name', '')),
            order=int(data.get('order', 0) or 0),
            created=str(data.get('created', '')),
        )


@dataclass
class KbNote:
    """一条笔记。category 必为 CATEGORIES 之一；body 是 Markdown 文本。"""
    id: str
    project_id: str
    category: str
    title: str = ""
    body: str = ""
    tags: List[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    def to_dict(self):
        return {
            'id': self.id, 'project_id': self.project_id,
            'category': self.category, 'title': self.title, 'body': self.body,
            'tags': list(self.tags), 'created': self.created, 'updated': self.updated,
        }

    @classmethod
    def from_dict(cls, data):
        tags = data.get('tags') or []
        if not isinstance(tags, list):
            tags = []
        category = str(data.get('category', ''))
        if category not in CATEGORIES:
            category = CATEGORY_DIFF
        return cls(
            id=str(data.get('id', '')),
            project_id=str(data.get('project_id', '')),
            category=category,
            title=str(data.get('title', '')),
            body=str(data.get('body', '')),
            tags=[str(t) for t in tags if str(t).strip()],
            created=str(data.get('created', '')),
            updated=str(data.get('updated', '')),
        )


@dataclass
class SearchHit:
    """一条检索命中。snippet 是给界面直接展示的上下文片段（已去掉换行）。"""
    note: KbNote
    project_name: str
    matched: str      # 'title' / 'body' / 'tag'
    snippet: str


class KnowledgeModel:
    DATA_FILE = data_path("knowledge_data.json")

    def __init__(self):
        self.projects: List[KbProject] = []
        self.notes: List[KbNote] = []
        self.load()

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.projects = [KbProject.from_dict(p) for p in data.get('projects', [])]
                self.notes = [KbNote.from_dict(n) for n in data.get('notes', [])]
            except Exception as e:
                print(f"[knowledge] 读取失败，按空库处理: {e}")
                self.projects, self.notes = [], []
        else:
            self.projects, self.notes = [], []
            self.save()

    def save(self):
        data = {
            'projects': [p.to_dict() for p in self.projects],
            'notes': [n.to_dict() for n in self.notes],
        }
        os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
        with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _generate_id(self, prefix: str, existing) -> str:
        base = int(time.time() * 1000) % 1000000000
        counter = 0
        while True:
            new_id = f"{prefix}_{base}_{counter}"
            if new_id not in existing:
                return new_id
            counter += 1

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------
    def get_projects(self) -> List[KbProject]:
        """按用户排的顺序返回（order 相同则按名字，保证顺序稳定）"""
        return sorted(self.projects, key=lambda p: (p.order, p.name))

    def get_project(self, project_id: str) -> Optional[KbProject]:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None

    def add_project(self, name: str, use_template: bool = False) -> KbProject:
        """新建项目；use_template=True 时一并生成四类骨架笔记"""
        name = str(name or "").strip() or "未命名项目"
        order = max([p.order for p in self.projects], default=-1) + 1
        project = KbProject(
            id=self._generate_id("kbproj", {p.id for p in self.projects}),
            name=name, order=order, created=_now(),
        )
        self.projects.append(project)
        if use_template:
            for category, title, body in PROJECT_TEMPLATE:
                self.add_note(project.id, category, title=title, body=body)
        else:
            self.save()
        return project

    def rename_project(self, project_id: str, name: str) -> bool:
        project = self.get_project(project_id)
        name = str(name or "").strip()
        if project is None or not name:
            return False
        project.name = name
        self.save()
        return True

    def delete_project(self, project_id: str) -> bool:
        """删项目 = 删它的所有条目 + 条目图片目录（数据层唯一会删文件的地方）"""
        project = self.get_project(project_id)
        if project is None:
            return False
        doomed = [n for n in self.notes if n.project_id == project_id]
        self.notes = [n for n in self.notes if n.project_id != project_id]
        self.projects.remove(project)
        for note in doomed:
            shutil.rmtree(self.note_base_dir(note.id), ignore_errors=True)
        self.save()
        return True

    def move_project(self, project_id: str, delta: int) -> bool:
        """把项目在列表里上移 / 下移一位（delta = -1 / +1）"""
        ordered = self.get_projects()
        pos = next((i for i, p in enumerate(ordered) if p.id == project_id), None)
        if pos is None:
            return False
        target = pos + delta
        if target < 0 or target >= len(ordered):
            return False
        ordered[pos], ordered[target] = ordered[target], ordered[pos]
        # 重排后统一写回 0..n-1，避免 order 值无限增长
        for i, project in enumerate(ordered):
            project.order = i
        self.save()
        return True

    # ------------------------------------------------------------------
    # 条目
    # ------------------------------------------------------------------
    def get_notes(self, project_id: str = None,
                  category: str = None) -> List[KbNote]:
        """按创建顺序返回（稳定：不会因为编辑一下就在列表里跳位）"""
        result = []
        for note in self.notes:
            if project_id is not None and note.project_id != project_id:
                continue
            if category is not None and note.category != category:
                continue
            result.append(note)
        return sorted(result, key=lambda n: (n.created, n.id))

    def get_note(self, note_id: str) -> Optional[KbNote]:
        for n in self.notes:
            if n.id == note_id:
                return n
        return None

    def add_note(self, project_id: str, category: str, title: str = "",
                 body: str = "", tags=None) -> Optional[KbNote]:
        if self.get_project(project_id) is None:
            return None
        if category not in CATEGORIES:
            category = CATEGORY_DIFF
        note = KbNote(
            id=self._generate_id("kbnote", {n.id for n in self.notes}),
            project_id=project_id, category=category,
            title=str(title or "").strip() or "未命名条目",
            body=str(body or ""),
            tags=[str(t) for t in (tags or []) if str(t).strip()],
            created=_now(), updated=_now(),
        )
        self.notes.append(note)
        self.save()
        return note

    def update_note(self, note_id: str, **kwargs) -> bool:
        note = self.get_note(note_id)
        if note is None:
            return False
        for key, value in kwargs.items():
            if key == 'category':
                if value in CATEGORIES:
                    note.category = value
            elif key == 'tags':
                note.tags = [str(t) for t in (value or []) if str(t).strip()]
            elif hasattr(note, key):
                setattr(note, key, value)
        note.updated = _now()
        self.save()
        return True

    def delete_note(self, note_id: str) -> bool:
        note = self.get_note(note_id)
        if note is None:
            return False
        self.notes.remove(note)
        shutil.rmtree(self.note_base_dir(note_id), ignore_errors=True)
        self.save()
        return True

    def count_notes(self, project_id: str) -> int:
        return sum(1 for n in self.notes if n.project_id == project_id)

    def quick_cards(self, project_id: str) -> List[KbNote]:
        """该项目的「速查」条目 —— 界面把它置顶渲染成卡片带"""
        return self.get_notes(project_id=project_id, category=CATEGORY_QUICK)

    def all_tags(self) -> List[str]:
        """全库出现过的标签（去重排序），供输入时提示"""
        tags = set()
        for note in self.notes:
            tags.update(note.tags)
        return sorted(tags)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(self, keyword: str) -> List[SearchHit]:
        """跨项目全文检索标题 / 正文 / 标签（不区分大小写）。

        命中正文时带一段上下文片段 —— 「踩过的坑」是事后才想起去查的东西，
        只给标题没用，得让用户一眼看到命中的那句话。
        """
        kw = str(keyword or "").strip().lower()
        if not kw:
            return []
        project_names = {p.id: p.name for p in self.projects}
        hits: List[SearchHit] = []
        for note in self.get_notes():
            if kw in (note.title or "").lower():
                matched = 'title'
            elif kw in (note.body or "").lower():
                matched = 'body'
            elif any(kw in t.lower() for t in note.tags):
                matched = 'tag'
            else:
                continue
            hits.append(SearchHit(
                note=note,
                project_name=project_names.get(note.project_id, ""),
                matched=matched,
                snippet=self._make_snippet(note, kw, matched),
            ))
        return hits

    @staticmethod
    def _make_snippet(note: KbNote, kw: str, matched: str) -> str:
        if matched != 'body':
            return note.title or ""
        body = note.body or ""
        idx = body.lower().find(kw)
        if idx < 0:
            return ""
        start = max(0, idx - 30)
        end = min(len(body), idx + len(kw) + 60)
        piece = body[start:end].replace("\n", " ").strip()
        return ("…" if start > 0 else "") + piece + ("…" if end < len(body) else "")

    # ------------------------------------------------------------------
    # 图片资源
    # ------------------------------------------------------------------
    def note_base_dir(self, note_id: str) -> str:
        """该条目资源的根目录（预览 Markdown 时作为 baseUrl，`assets/x.png` 靠它解析）"""
        return os.path.join(get_data_dir(), ASSETS_DIR_NAME, str(note_id))

    def note_asset_dir(self, note_id: str) -> str:
        """图片实际存放目录：<note_base_dir>/assets"""
        return os.path.join(self.note_base_dir(note_id), "assets")

    def save_asset(self, note_id: str, data: bytes, suffix: str = ".png") -> str:
        """把一张图落盘，返回正文里该用的相对路径（如 `assets/kbnote_x_...png`）。

        文件名里带 note_id 与时间戳：全局唯一，导出时平铺进同一个 assets/ 不会撞名。
        """
        target_dir = self.note_asset_dir(note_id)
        os.makedirs(target_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = suffix if suffix.startswith(".") else "." + str(suffix)
        counter = 0
        while True:
            filename = f"{note_id}_{stamp}_{counter}{suffix}"
            path = os.path.join(target_dir, filename)
            if not os.path.exists(path):
                break
            counter += 1
        with open(path, "wb") as f:
            f.write(data)
        return ASSET_LINK_PREFIX + filename

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_markdown(self, dest_dir: str) -> Dict[str, int]:
        """把整个库导成 Markdown 目录树：

            <dest_dir>/index.md
            <dest_dir>/<项目名>/index.md
            <dest_dir>/<项目名>/<条目标题>.md
            <dest_dir>/<项目名>/assets/<图片>

        图片按项目平铺到同一个 assets/（文件名全局唯一，不会撞），
        正文里的 `assets/xxx.png` 因此不用改写就能直接显示。
        """
        stats = {"projects": 0, "notes": 0, "images": 0}
        os.makedirs(dest_dir, exist_ok=True)
        root_lines = ["# 个人知识库\n", f"导出时间：{_now()}\n"]

        for project in self.get_projects():
            folder = _safe_name(project.name, project.id)
            project_dir = os.path.join(dest_dir, folder)
            os.makedirs(project_dir, exist_ok=True)
            stats["projects"] += 1

            notes = self.get_notes(project_id=project.id)
            root_lines.append(f"- [{project.name}]({folder}/index.md)（{len(notes)} 条）")

            project_lines = [f"# {project.name}\n", f"共 {len(notes)} 条\n"]
            used_names = set()
            for note in notes:
                # 注意补回 .md：_safe_name 会把点号清掉（它按文件名规则清洗），
                # 只传标题会导出一批没有扩展名的文件
                filename = self._unique_filename(
                    _safe_name(note.title, note.id) + ".md", used_names)
                project_lines.append(f"- [{note.title}]({filename})　`{note.category}`")
                self._write_note_md(note, project, os.path.join(project_dir, filename))
                stats["notes"] += 1
                stats["images"] += self._copy_note_assets(note, project_dir)

            self._write_text(os.path.join(project_dir, "index.md"),
                             "\n".join(project_lines) + "\n")

        self._write_text(os.path.join(dest_dir, "index.md"),
                         "\n".join(root_lines) + "\n")
        return stats

    @staticmethod
    def _unique_filename(base: str, used: set) -> str:
        name, ext = os.path.splitext(base)
        candidate, n = base, 1
        while candidate.lower() in used:
            candidate = f"{name}({n}){ext}"
            n += 1
        used.add(candidate.lower())
        return candidate

    def _write_note_md(self, note: KbNote, project: KbProject, path: str):
        tags = " ".join(f"#{t}" for t in note.tags)
        head = [
            f"# {note.title}\n",
            f"> 项目：{project.name}　｜　分类：{note.category}",
        ]
        if tags:
            head.append(f"> 标签：{tags}")
        head.append(f"> 更新时间：{note.updated}\n")
        self._write_text(path, "\n".join(head) + "\n" + (note.body or "") + "\n")

    def _copy_note_assets(self, note: KbNote, project_dir: str) -> int:
        src_dir = self.note_asset_dir(note.id)
        if not os.path.isdir(src_dir):
            return 0
        dst_dir = os.path.join(project_dir, "assets")
        os.makedirs(dst_dir, exist_ok=True)
        copied = 0
        for name in os.listdir(src_dir):
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            shutil.copy2(src, os.path.join(dst_dir, name))
            copied += 1
        return copied

    @staticmethod
    def _write_text(path: str, text: str):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
