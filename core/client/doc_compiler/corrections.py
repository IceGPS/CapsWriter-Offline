"""语音纠错统计（留声输入法一期：只记录 + 每日统计）。

从当日 JSONL 条目中筛 type=correction 的事件（由 Android 端
CorrectionTracker 在检查点差分捕获、TranscriptLogger.logCorrection 落盘），
聚合为一段确定性生成的 Markdown，追加到每日文档末尾。
无纠错事件时返回 None（文档不加此节）。

事件字段见 Android 侧设计文档 correction-learning-phase1-design.md：
before/after/ctx_l/ctx_r/homophone/pinyin_before/pinyin_after/voice_id/ts。
"""

from collections import Counter


def build_correction_stats_md(entries: list[dict]) -> str | None:
    events = [e for e in entries if e.get("type") == "correction"]
    if not events:
        return None

    total = len(events)
    homo = [e for e in events if e.get("homophone")]
    pairs = Counter(
        (e.get("before", ""), e.get("after", "")) for e in events
    )
    homo_pairs = Counter(
        (e.get("before", ""), e.get("after", "")) for e in homo
    )

    lines = [
        "## 语音纠错统计",
        "",
        "<!-- 自动生成：当日语音输入的手工纠错记录（只记录，不自动修改）。 -->",
        "",
        f"今日共记录 {total} 处纠错，其中同音纠错 {len(homo)} 处"
        f"（{len(homo) * 100 // total}%）。",
        "",
    ]

    if homo_pairs:
        lines += [
            "同音纠错 TOP：",
            "",
            "| 原识别 | 改为 | 次数 |",
            "| --- | --- | --- |",
        ]
        for (before, after), n in homo_pairs.most_common(10):
            lines.append(f"| {before} | {after} | {n} |")
        lines.append("")

    others = [(p, n) for p, n in pairs.most_common() if p not in homo_pairs]
    if others:
        lines.append(
            "非同音修改（多为改说法，仅供参考）："
            + "、".join(f"{b}→{a}×{n}" for (b, a), n in others[:10])
        )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
