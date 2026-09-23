# 虫师（Chongshi）

> Android 自动化测试工具 —— 让自动化触手可及

虫师是一套面向 **Android 车机 / 手机 App** 的桌面端自动化测试工具：在图形界面里编用例、管元素、
跑执行、看报告，并把日常测试中反复用到的 ADB 操作（截图、抓日志、Monkey、弱网、抓包、性能采集）
都收进一个工具箱。基于 Python + PyQt6，设备侧走 uiautomator2。

## 功能

| 模块 | 说明 |
|---|---|
| **自动化编辑** | 项目 / 功能模块 / 用例 / 步骤四级结构，动作卡片编辑（点击、输入、滑动、断言、等待、语音播报…），支持从元素库选元素、拖拽排序 |
| **应用元素库** | 元素集中管理（资源ID / 文本 / 描述 / XPath / 坐标），支持一键在设备上**验证是否存在**、导入导出 |
| **自动化执行** | 勾选用例或套件执行，实时日志、通过率统计、生成 HTML 报告 |
| **定时任务** | 按周期自动执行；设备不在线时跳过并留痕，设备恢复后自动复位 |
| **ADB 工具箱** | 截图/录屏/安装/推送/MD5、Monkey、Crash 与 ANR 抓取、硬件信息、弱网模拟、抓包、堆转储（hprof）、内存监控、无线调试、指令库检索与自定义命令 |
| **应用可视化** | 投屏设备画面并直接在画面上操作，配合 weditor 定位元素 |
| **性能检测** | CPU / 内存 / FPS / 流量采集，实时曲线与阈值告警，可导出 CSV / JSON / 报告 |
| **语音播报** | 独立的语音用例库（唤醒词 + 指令 + 二次交互），批量通过 TTS 播报并回读结果 |
| **消息中心** | 执行结果、设备增删、ADB 长命令失败、AI 分析完成等事件集中留痕，支持动作快捷入口 |
| **检查更新** | 启动后自动检查 GitHub Releases，有新版本在菜单上亮红点；也可手动检查 |

## 环境要求

- **Windows 10 / 11 x64**
- **Python 3.13**（开发与打包均用它）
- 依赖见 `requirements.txt`（PyQt6 / QtWebEngine、uiautomator2、weditor、openpyxl、pyecharts、pyqtgraph…）
- `tools/` 里已内置 **adb / scrcpy / tcpdump**，无需另外安装 platform-tools
- 被测设备需打开 **USB 调试**（无线调试可在 ADB 工具箱里配）

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

## 打包

```bash
pyinstaller main.spec --noconfirm                                          # -> dist/虫师/
pyinstaller updater.spec --noconfirm --distpath dist_updater --workpath build_updater   # -> dist_updater/updater.exe
```

打包产物是 **onedir** 结构：`dist/虫师/虫师.exe` + `_internal/`。
`updater.exe` 是自动更新用的独立小程序（纯标准库，7MB 左右），发布时会打进更新包。

## 目录结构

```
main.py              入口：装配模型 / 视图 / 控制器，启动封面与设备链路
main.spec            主程序打包配置（含启动封面、DPI 感知清单）
updater.py/.spec     独立更新器（下载完成后由它替换文件并重启）
models/              数据模型（项目树、步骤、元素库、套件、任务、性能、语音…）
views/               界面（主窗口、各功能页、对话框、ADB 面板与对话框）
controllers/         控制器（把视图事件转成模型操作与执行流程）
services/            服务层（设备、执行、ADB 命令池、weditor、语音、检查更新）
utils/               通用工具（路径、设置、主题、日志着色、图标、本地命令库…）
resources/           图标、帮助截图、脚本
tools/               内置 adb / scrcpy / tcpdump
data/                运行期数据（不进仓库，见下）
```

## 数据存放

所有运行期数据都在**程序目录下的 `data/`**：`config.json`（设置）、`project_data.json`（项目树）、
`steps_data.json`、`elements_data.json`、`suites_data.json`、`tasks_data.json`、`perf_data.json`、
`voice_data.json` 等。`data/` 不进版本库，换机器时整目录拷贝即可。

### 可选的本地命令库（`data/local_commands.json`）

如果有些命令属于特定客户 / 项目、不希望随仓库分发（厂商私有 settings、客户设备的内部日志路径、
特定机型入口 Activity…），可以放在这个不进版本库的文件里：

```json
{
  "presets": [
    {
      "id": 101,
      "name": "某机型日志",
      "command_text": "pull /data/vendor/log \"{output_dir}/{safe_device_serial}/\"",
      "description": "拉取某机型日志",
      "show_stop_button": true,
      "is_preset": true,
      "no_timeout": true
    }
  ],
  "adb": [
    {"command": "adb shell settings put system xxx 1", "category": "设备管理", "description": "某私有开关"}
  ]
}
```

`presets` 会并入预设列表（和内置预设一样点一下就执行），`adb` 会并入指令库的搜索结果；
文件不存在或某条写错都不影响启动。可用字段与 `services/preset_commands.py`、
`services/adb_commands.py` 里的条目一致。

同理，**目标应用包名**也放在本机设置里，而不是写死在代码：
`data/config.json` 的 `target_package`（内存监控脚本与内存解析用它定位目标进程）、
`monkey_package`（Monkey 面板默认包名，面板里用一次就会记住）。

## 检查更新与发版

- 应用启动 4 秒后会**在后台线程**检查 GitHub Releases（不阻塞启动，失败静默只写日志）；
  菜单 →「检查更新」可手动查，有新版会在菜单项和菜单按钮上亮红点
- 发版三步：改 `utils/version.py` 的 `APP_VERSION` → 提交推送 → `git tag v1.1.4 && git push origin v1.1.4`
- `.github/workflows/release.yml` 会自动：校验 tag 与代码版本一致 → 跑更新器自测 → 打包主程序与更新器
  → 校验产物完整性 → 打 zip → 校验包内不含运行期数据 → 创建 Release 并上传产物
- Release 的说明就是应用内「更新说明」显示的内容，发完可在网页上编辑

## 开发约定

- 运行期数据一律走 `utils/app_paths.py`，不要往程序目录根下写文件
- 数据文件的读写由 models 负责，views 不直接碰文件
- 打包配置里有两处**容易踩的坑**，改动相关代码前请先读注释：
  - 启动封面的 `text_font` 带空格的字体名必须写成 `'{Microsoft YaHei}'`，否则 Tcl 脚本会中断、封面变成空白小窗
  - `splash.binaries` 必须同时交给 EXE 和 COLLECT，否则增量打包会漏掉 tcl/tk 文件（双击 exe 会连弹报错框）

## 截图

*（待补充：把界面截图放到 `docs/` 下，再在这里引用）*

补图时注意：界面上会直接显示**设备序列号、安装包名、日志内容**，
公开仓库的截图前请确认画面里没有内部项目代号、客户包名等信息。

## 许可证

[MIT](LICENSE)
