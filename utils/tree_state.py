# utils/tree_state.py
"""树展开状态的持久化：**默认全折叠**，并记住用户上次展开到哪儿。

为什么要这个东西：Qt 在 `setModel()` / `clear()` 之后会把展开态一律丢掉，
所以各页原来的做法都是"数据填完就 expandAll()"。后果是每次打开应用、每次增删
一个节点，树都猛地全展开，用户手点的折叠全白点。

这里把展开状态按「树 key + 节点 id」写进 `data/config.json`（沿用 Settings 的
读写，不另开文件），下次启动按 id 还原。**没有任何记录时保持全折叠**，这就是
"不默认展开"。

各页接法（两处调用，位置很关键）：

    # 建树时绑一次
    self._tree_state = tree_state.bind_view(self.tree_view, "execute_tree")

    def refresh(self):
        self._tree_state.snapshot()     # ① 重建前先把当前展开态收下来
        ...重建节点（clear + 重新填充）...
        self._tree_state.restore()      # ② 填完后按记录还原，没记录就保持全折叠

两个 id 取法：
  - 节点自带 id（项目管理树 / 执行页树 / 语音页两棵树）用默认取 `UserRole`；
  - 没有 id 的静态树（帮助中心、设置导航）用 `index_text_path_id` /
    `item_text_path_id`，按"从根到自己的文字路径"当 id，天然防重名。
"""
import contextlib

from PyQt6.QtCore import QModelIndex, Qt

from utils.settings import Settings

# 在 config.json 里的键名：{"tree_expanded": {"<key>": ["id1", "id2", ...]}}
SETTINGS_KEY = "tree_expanded"


# ----------------------------------------------------------------------
# 读写
# ----------------------------------------------------------------------
def load(key):
    """返回该树上一次记录的展开节点 id 集合；**从未记录过返回 None**。

    None 与空集合的区别：None = 没有历史记录（首次运行，保持全折叠）；
    空集合 = 用户把所有节点都收起来了（下次打开也应当全折叠）。
    两者表现一样，但语义分开才好判断"要不要写盘"。
    """
    record = Settings.load().get(SETTINGS_KEY)
    if not isinstance(record, dict) or key not in record:
        return None
    value = record.get(key)
    if not isinstance(value, list):
        return None
    return {str(v) for v in value}


def save(key, ids):
    """把该树的展开节点 id 集合写回 config.json（只动 tree_expanded 这一个键）"""
    data = Settings.load()
    record = data.get(SETTINGS_KEY)
    if not isinstance(record, dict):
        record = {}
    # 排序是为了让 config.json 可读、便于人工 diff；集合本身无序
    record[key] = sorted(str(i) for i in ids)
    data[SETTINGS_KEY] = record
    Settings.save(data)


# ----------------------------------------------------------------------
# 静态树的 id 取法（节点没有业务 id 时用）
# ----------------------------------------------------------------------
def index_text_path_id(index):
    """QTreeView / QAbstractItemModel：用「根/…/父/自己」的文字路径当 id"""
    parts = []
    cursor = index
    while cursor.isValid():
        parts.append(str(cursor.data(Qt.ItemDataRole.DisplayRole) or ""))
        cursor = cursor.parent()
    return "/".join(reversed(parts))


def item_text_path_id(item):
    """QTreeWidget：用「根/…/父/自己」的文字路径当 id"""
    parts = []
    cursor = item
    while cursor is not None:
        parts.append(cursor.text(0))
        cursor = cursor.parent()
    return "/".join(reversed(parts))


# ----------------------------------------------------------------------
# 视图侧遍历（QTreeView / QTreeWidget 两套）
# ----------------------------------------------------------------------
def _iter_model_indexes(view):
    model = view.model()
    if model is None:
        return

    def walk(parent):
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            yield index
            yield from walk(index)

    yield from walk(QModelIndex())


def _iter_tree_items(tree):
    def walk(item):
        yield item
        for i in range(item.childCount()):
            yield from walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        yield from walk(tree.topLevelItem(i))


def _collect_view(view, id_of):
    ids = set()
    for index in _iter_model_indexes(view):
        if not view.isExpanded(index):
            continue
        node_id = id_of(index)
        if node_id is not None:
            ids.add(str(node_id))
    return ids


def _expand_view(view, id_of, ids):
    for index in _iter_model_indexes(view):
        node_id = id_of(index)
        if node_id is not None and str(node_id) in ids and not view.isExpanded(index):
            view.setExpanded(index, True)


def _collect_tree(tree, id_of):
    ids = set()
    for item in _iter_tree_items(tree):
        if not item.isExpanded():
            continue
        node_id = id_of(item)
        if node_id is not None:
            ids.add(str(node_id))
    return ids


def _expand_tree(tree, id_of, ids):
    for item in _iter_tree_items(tree):
        node_id = id_of(item)
        if node_id is not None and str(node_id) in ids and not item.isExpanded():
            item.setExpanded(True)


# ----------------------------------------------------------------------
# 挂在树上的维护器
# ----------------------------------------------------------------------
class _Keeper:
    """一棵树的展开状态维护器。

    展开/折叠信号驱动：**每次用户点箭头就立刻落盘**，不攒到退出时才写 ——
    程序崩了、被任务管理器结束，状态一样不丢。
    """

    def __init__(self, tree, key, collect, expand, has_nodes):
        self._tree = tree
        self._key = key
        self._collect = collect
        self._expand = expand
        self._has_nodes = has_nodes
        self._ids = load(key)     # 内存镜像，初值就是磁盘上那份
        self._busy = 0            # >0：程序自己在展开/折叠，信号不算用户意图
        self._ready = False       # 树还没填过节点前，采集到的东西说明不了问题

    # -- 供各页调用 --------------------------------------------------
    def snapshot(self):
        """重建节点前调用：把当前真实展开态收下来（重建会把它丢掉）。

        树是空的时候直接跳过：否则"启动时先 refresh 一次空树"会被当成
        "用户把所有节点都收起来了"，把上次的记录覆盖掉。
        """
        if self._busy or not self._ready or not self._has_nodes():
            return
        ids = self._collect()
        if ids != self._ids:
            self._ids = ids
            save(self._key, ids)

    def restore(self):
        """节点填充完后调用：按记录还原；没有记录就保持全折叠"""
        with self.pause():
            if self._ids and self._has_nodes():
                self._expand(self._ids)
        if self._has_nodes():
            self._ready = True

    @contextlib.contextmanager
    def pause(self):
        """重建期间用：Qt 在 clear()/换模型时可能补发展开信号，别当用户操作记"""
        self._busy += 1
        try:
            yield
        finally:
            self._busy -= 1

    # -- 信号回调 ----------------------------------------------------
    def _on_toggled(self, *_args):
        if self._busy:
            return
        self._ids = self._collect()
        save(self._key, self._ids)


def bind_view(view, key, id_of=None):
    """给 QTreeView（QAbstractItemView + QAbstractItemModel）挂上展开状态维护器"""
    id_of = id_of or (lambda index: index.data(Qt.ItemDataRole.UserRole))
    keeper = _Keeper(
        view, key,
        collect=lambda: _collect_view(view, id_of),
        expand=lambda ids: _expand_view(view, id_of, ids),
        has_nodes=lambda: view.model() is not None and view.model().rowCount() > 0,
    )
    view.expanded.connect(keeper._on_toggled)
    view.collapsed.connect(keeper._on_toggled)
    return keeper


def bind_tree(tree, key, id_of=None):
    """给 QTreeWidget 挂上展开状态维护器"""
    id_of = id_of or (lambda item: item.data(0, Qt.ItemDataRole.UserRole))
    keeper = _Keeper(
        tree, key,
        collect=lambda: _collect_tree(tree, id_of),
        expand=lambda ids: _expand_tree(tree, id_of, ids),
        has_nodes=lambda: tree.topLevelItemCount() > 0,
    )
    tree.itemExpanded.connect(keeper._on_toggled)
    tree.itemCollapsed.connect(keeper._on_toggled)
    return keeper
