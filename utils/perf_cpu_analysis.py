# utils/perf_cpu_analysis.py
"""CPU 采样的深度分析（纯函数，不碰界面、不读文件）。

报告层拿这些结果生成 HTML：
  * 分位数 P50/P75/P90/P95/P99
  * 多阈值分档（阈值随本次峰值自适应）
  * 百分位分档（按采样点数量取前 2% / 5% / 10% / 25%）
  * 连续峰值段（≥ P90 的连续采样点）
  * 峰值采样点明细
  * 峰值集中度（Top1 / Top5 线程占比）—— 需要线程探针数据

为什么单独成文件：这些是纯计算，能直接喂假数据做单测；
塞进 perf_report.py 会变成"一边拼 HTML 一边算统计"，改一个数都要重跑报告。
"""
import math


# ---------------------------------------------------------------- 基础统计
def percentile(values, p: float) -> float:
    """最近秩法分位数（p 取 0~100）。空列表返回 0。"""
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if p <= 0:
        return vals[0]
    if p >= 100:
        return vals[-1]
    idx = int(math.ceil(p / 100.0 * len(vals))) - 1
    return vals[max(0, min(idx, len(vals) - 1))]


def _mean(values) -> float:
    vals = list(values)
    return (sum(vals) / len(vals)) if vals else 0.0


# ---------------------------------------------------------------- 线程对齐
def _thread_tolerance(thread_samples) -> float:
    """主 CPU 采样点与线程采样点的最大允许时间差。

    取线程采样间隔中位数的 1.5 倍（至少 10 秒）：两个探针各自独立跑，
    间隔不同，不要求同一时刻，但差太远就不能把线程归因到这次峰值上。
    """
    ts = sorted(float(t.get("ts") or 0) for t in thread_samples)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if not gaps:
        return 30.0
    gaps.sort()
    return max(10.0, gaps[len(gaps) // 2] * 1.5)


def _thread_at(ts: float, thread_samples, tol: float):
    """找离 ts 最近的线程采样点；超出 tol 返回 None。"""
    best = None
    best_dt = tol
    for item in thread_samples:
        try:
            dt = abs(float(item.get("ts") or 0) - ts)
        except (TypeError, ValueError):
            continue
        if dt <= best_dt:
            best, best_dt = item, dt
    return best


def _thread_shares(item):
    """从线程采样点算 (Top1 占比, Top5 占比)；数据不全返回 None。"""
    if not item:
        return None
    tops = sorted((item.get("top") or []),
                  key=lambda x: float(x.get("cpu") or 0), reverse=True)
    total = float(item.get("cpu_total") or 0)
    if not tops or total <= 0:
        return None
    top1 = float(tops[0].get("cpu") or 0) / total * 100.0
    top5 = sum(float(t.get("cpu") or 0) for t in tops[:5]) / total * 100.0
    return round(top1, 1), round(top5, 1)


# ---------------------------------------------------------------- 主分析
def analyze_cpu(cpu_points, interval: float, thread_samples=None) -> dict:
    """cpu_points: [(ts, cpu_pct), ...]（已剔除采集失败的点）。

    返回 dict：
      count / interval / percentiles / thresholds / percentile_buckets /
      segments / peak_samples / concentration / thread_tolerance
    """
    thread_samples = thread_samples or []
    tol = _thread_tolerance(thread_samples)
    points = [(float(ts), float(v)) for ts, v in cpu_points]
    values = [v for _ts, v in points]
    result = {
        "count": len(points),
        "interval": interval,
        "thread_tolerance": tol,
        "percentiles": {},
        "thresholds": [],
        "percentile_buckets": [],
        "segments": [],
        "isolated_peaks": 0,
        "peak_samples": [],
        "concentration": {},
    }
    if not values:
        return result

    result["percentiles"] = {
        f"p{p}": round(percentile(values, p), 2) for p in (50, 75, 90, 95, 99)
    }
    result["percentiles"]["max"] = round(max(values), 2)
    p90 = percentile(values, 90)

    # ---- 阈值分档：按本次峰值自适应（25/50/75/90/100%）----
    max_v = max(values)
    if max_v > 0:
        # 用 2 位小数：用 1 位会把 23.55 四舍五入成 23.6，
        # 结果"峰值本身"达不到自己的阈值，100% 那一档整行消失
        thresholds = sorted({round(max_v * f, 2) for f in (0.25, 0.5, 0.75, 0.9, 1.0)})
        for th in thresholds:
            hit = [(ts, v) for ts, v in points if v >= th]
            if not hit:
                continue
            shares = [_thread_shares(_thread_at(ts, thread_samples, tol))
                      for ts, _v in hit]
            shares = [s for s in shares if s]
            top5 = round(_mean(s[1] for s in shares), 1) if shares else None
            result["thresholds"].append({
                "threshold": th,
                "count": len(hit),
                "share": round(len(hit) / len(points) * 100.0, 1),
                "avg": round(_mean(v for _t, v in hit), 2),
                "max": round(max(v for _t, v in hit), 2),
                "top5": top5,
            })

    # ---- 百分位分档：按采样点数量取前 N% ----
    ordered = sorted(points, key=lambda x: x[1], reverse=True)
    for pct in (2, 5, 10, 25):
        n = max(1, int(round(len(ordered) * pct / 100.0)))
        hit = ordered[:n]
        shares = [_thread_shares(_thread_at(ts, thread_samples, tol))
                  for ts, _v in hit]
        shares = [s for s in shares if s]
        result["percentile_buckets"].append({
            "label": f"前 {pct}%",
            "threshold": round(min(v for _t, v in hit), 2),
            "count": n,
            "share": round(n / len(points) * 100.0, 1),
            "avg": round(_mean(v for _t, v in hit), 2),
            "max": round(max(v for _t, v in hit), 2),
            "top5": round(_mean(s[1] for s in shares), 1) if shares else None,
        })

    # ---- 连续峰值段：值 ≥ P90 且**至少连续 2 个采样点** ----
    # 只有 1 个点的"段"没有分析价值（平坦数据上会冒出一堆 #13~#13），
    # 单独计入 isolated_peaks，报告里用一句话带过。
    peak_idx = [i for i, (_t, v) in enumerate(points) if v >= p90 and v > 0]
    runs = []
    cur = []
    for i in peak_idx:
        if cur and i == cur[-1] + 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    result["isolated_peaks"] = sum(1 for r in runs if len(r) < 2)

    for run in runs:
        if len(run) < 2:
            continue
        seg = [points[i] for i in run]
        # 主导线程：匹配到的线程采样点里，按 TID 聚合 CPU 后取最高
        agg = {}
        for ts, _v in seg:
            item = _thread_at(ts, thread_samples, tol)
            for t in (item or {}).get("top") or []:
                key = (t.get("tid"), t.get("name", ""))
                agg[key] = agg.get(key, 0.0) + float(t.get("cpu") or 0)
        dominant = ""
        if agg:
            (tid, name), _cpu = max(agg.items(), key=lambda kv: kv[1])
            dominant = f"{name}（TID {tid}）"
        result["segments"].append({
            "start": run[0] + 1,          # 展示用 1-based
            "end": run[-1] + 1,
            "duration": round((run[-1] - run[0] + 1) * interval, 1),
            "avg": round(_mean(v for _t, v in seg), 2),
            "max": round(max(v for _t, v in seg), 2),
            "dominant": dominant,
        })

    # ---- 峰值采样点明细（前 5）----
    for idx, (ts, v) in enumerate(ordered[:5], start=1):
        item = _thread_at(ts, thread_samples, tol)
        contrib = []
        total = float((item or {}).get("cpu_total") or 0)
        if total > 0:
            for t in (item.get("top") or []):
                cpu = float(t.get("cpu") or 0)
                if cpu / total * 100.0 >= 5.0:
                    contrib.append(f"{t.get('name', '')}={cpu:.1f}")
        # 原始采样序号（1-based），便于对着 CSV 找
        pos = next((i + 1 for i, (t0, v0) in enumerate(points)
                    if t0 == ts and v0 == v), 0)
        result["peak_samples"].append({
            "index": pos,
            "value": round(v, 2),
            "contributors": "、".join(contrib),
            "matched": bool(item),
        })

    # ---- 峰值集中度（值 ≥ P90 的点）----
    top1_list, top5_list = [], []
    for ts, v in points:
        if v < p90 or v <= 0:
            continue
        shares = _thread_shares(_thread_at(ts, thread_samples, tol))
        if shares:
            top1_list.append(shares[0])
            top5_list.append(shares[1])
    if top1_list:
        top1 = round(_mean(top1_list), 1)
        top5 = round(_mean(top5_list), 1)
        if top1 >= 50:
            mode = "单线程主导"
        elif top5 >= 60:
            mode = "中等集中（少数线程扛大头）"
        else:
            mode = "分散（多线程协同）"
        result["concentration"] = {
            "top1": top1, "top5": top5, "mode": mode,
            "samples": len(top1_list),
        }
    return result


# ---------------------------------------------------------------- 线程分析
def analyze_threads(thread_samples) -> dict:
    """线程探针序列 → 数量统计 + 最忙线程排行。

    排行只覆盖每个采样点里记录的最忙 TOP_N 个线程（全量明细在 CSV 产物里），
    所以「占比」是相对**线程 CPU 合计**算的，不是相对整机。
    """
    result = {"count": 0, "totals": {}, "threads": []}
    if not thread_samples:
        return result

    totals = [int(x.get("thread_total") or 0) for x in thread_samples]
    actives = [int(x.get("thread_active") or 0) for x in thread_samples]
    cpu_totals = [float(x.get("cpu_total") or 0) for x in thread_samples]
    result["count"] = len(thread_samples)
    result["totals"] = {
        "total_avg": round(_mean(totals), 1),
        "total_max": max(totals) if totals else 0,
        "total_min": min(totals) if totals else 0,
        "active_avg": round(_mean(actives), 1),
        "active_max": max(actives) if actives else 0,
        "active_min": min(actives) if actives else 0,
        "cpu_avg": round(_mean(cpu_totals), 2),
        "cpu_max": round(max(cpu_totals), 2) if cpu_totals else 0.0,
        "cpu_min": round(min(cpu_totals), 2) if cpu_totals else 0.0,
    }

    agg = {}
    for item in thread_samples:
        for t in (item.get("top") or []):
            key = (t.get("tid"), t.get("name", ""))
            row = agg.setdefault(key, {"cpu_sum": 0.0, "cpu_max": 0.0, "n": 0})
            cpu = float(t.get("cpu") or 0)
            row["cpu_sum"] += cpu
            row["cpu_max"] = max(row["cpu_max"], cpu)
            row["n"] += 1
    denom = sum(cpu_totals) or 0.0
    n_samples = max(1, len(thread_samples))
    rows = []
    for (tid, name), row in agg.items():
        rows.append({
            "tid": tid,
            "name": name,
            # 分母用**会话采样数**，不是"进入 Top N 的次数"：
            # 后者会把偶尔冒尖的线程均摊成很高的平均（比如只出现 3 次、
            # 每次 4.5% 的线程显示 4.5%，比一直占 1.4% 的主线程还高）。
            "cpu_avg": round(row["cpu_sum"] / n_samples, 2),
            "cpu_max": round(row["cpu_max"], 2),
            "share": round(row["cpu_sum"] / denom * 100.0, 1) if denom > 0 else 0.0,
            "samples": row["n"],
        })
    rows.sort(key=lambda r: r["cpu_avg"], reverse=True)
    result["threads"] = rows[:15]
    return result
