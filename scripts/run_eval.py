#!/usr/bin/env python
"""评测：本系统 vs 裸 LLM（同一批注入缺陷论文，同一 ground truth）。

## 为什么这样设计

真实论文**不该有内部矛盾**（实测作者 4 篇上 0 conflict），所以「能不能找到问题」
无法用真实数据验证 —— 必须**注入已知缺陷**当 ground truth。

两组跑**同一个任务**（找数值不一致）、**同一批数据**、**同一份答案**：

| 组 | 做法 |
| --- | --- |
| **A 裸 LLM** | 论文全文塞进 context，让它直接找 |
| **B 本系统** | 确定性规则（C1–C6） |

指标：
- **检出率**（recall）= 找到的注入缺陷数 / 注入总数
- **误报数**（在**干净论文**上的 conflict 数）—— 这是关键，
  因为 LLM 很可能"编出"不存在的问题
- 成本与耗时

用法：
    pixi run py scripts/run_eval.py                 # 确定性评测（快，不用 LLM）
    pixi run py scripts/run_eval.py --llm-baseline  # 加上裸 LLM 对照组
    pixi run py scripts/run_eval.py --json out.json # 落盘
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rsrc.audit.checks import run_checks  # noqa: E402
from rsrc.audit.extract import extract_facts  # noqa: E402
from rsrc.audit.inject import inject_all  # noqa: E402

PARSED = ROOT / "data" / "index" / "parsed"
OUT_DIR = ROOT / "data" / "eval"


# ── 数据模型 ────────────────────────────────────────────────────
@dataclass
class PaperResult:
    key: str
    injected: int = 0
    caught: int = 0
    clean_conflicts: int = 0  # 干净论文上的误报
    llm_injected: int = 0
    llm_caught: int = 0
    llm_clean_reports: int = 0
    llm_seconds: float = 0.0
    llm_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    def recall(self) -> float:
        return self.caught / self.injected if self.injected else 0.0

    def llm_recall(self) -> float:
        return self.llm_caught / self.llm_injected if self.llm_injected else 0.0


# ── 论文文本（给裸 LLM 用）──────────────────────────────────────
def paper_text(blocks: list[dict], limit_chars: int = 90_000) -> str:
    """把解析产物拼成带页码标注的全文。"""
    parts: list[str] = []
    size = 0
    for b in blocks:
        if b.get("type") in ("picture",):
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        chunk = f"[p{b.get('page')}|{b.get('type')}] {text}"
        size += len(chunk)
        if size > limit_chars:
            parts.append("...（后续内容因长度限制省略）")
            break
        parts.append(chunk)
    return "\n".join(parts)


LLM_PROMPT = """下面是一篇科研论文的全文（每段带 [页码|类型] 标注）。

请找出**文中数值自相矛盾或不一致**之处，例如：
- 同一指标（R²/RMSE/MAE/Bias 等）在文中不同位置给了不同取值，但指的是同一对象/同一组数据
- 指标之间的数学关系不成立（如 RMSE 小于 MAE、|Bias| 大于 RMSE、R² 超出 [0,1]）
- 同一数据来源的样本量前后不一致

**只报告你能在文中指出具体位置的问题。** 不要报告"建议补充验证""样本偏少"这类评价。
若确实没发现，返回空数组。

以 JSON 数组输出，不要输出任何其它文字：
[{"metric": "R²", "values": [0.75, 0.21], "locations": ["p2", "p14"], "reason": "同一对象 TRI 取值不一致"}]

论文全文：
---
%s
---
"""


def llm_find_inconsistencies(
    blocks: list[dict], *, keep_raw: bool = False
) -> tuple[list[dict], float, int] | tuple[list[dict], float, int, str]:
    """裸 LLM 基线。返回 (发现列表, 耗时秒, token 数[, 原始输出])。

    ⚠️ `keep_raw` 很重要：不加它就无法区分「LLM 真的返回了 []」与
    「输出不是合法 JSON 被丢弃」—— 两者在指标上都是 0，但含义完全不同。
    """
    from rsrc.llm import build_chat

    chat = build_chat()
    if chat is None:
        return ([], 0.0, 0, "") if keep_raw else ([], 0.0, 0)
    client, spec = chat

    prompt = LLM_PROMPT % paper_text(blocks)
    t0 = time.time()
    # ⚠️ 限流必须自己处理：实测 SiliconFlow 会返回
    #    429 `TPM limit reached` —— 免费/低价层常见，评测跑一半就崩。
    #    指数退避重试，并把“重试了几次”记录下来（否则耗时数字会误导）。
    resp = None
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model=spec.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0,
            )
            break
        except Exception as exc:  # noqa: BLE001 - 限流/网络都重试
            last_err = exc
            if "429" not in str(exc) and "rate" not in str(exc).lower():
                break
            time.sleep(2 ** attempt * 5)  # 5s / 10s / 20s
    if resp is None:
        print(f"    [警告] LLM 调用失败：{str(last_err)[:90]}", file=sys.stderr)
        return ([], time.time() - t0, 0, "") if keep_raw else ([], time.time() - t0, 0)
    dt = time.time() - t0
    raw = (resp.choices[0].message.content or "").strip()
    tokens = getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0

    reports: list[dict] = []
    match = re.search(r"\[[\s\S]*\]", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            reports = data if isinstance(data, list) else []
        except json.JSONDecodeError:
            reports = []
    return (reports, dt, tokens, raw) if keep_raw else (reports, dt, tokens)


def _llm_matches_injection(reports: list[dict], injection) -> bool:
    """判断 LLM 是否报到了这条注入缺陷 —— 按**数值**匹配（客观、可自动化）。

    注入的特征是「同一个数被改成了另一个数」，所以只要 LLM 的报告里
    同时出现了改后的值与改前的值，或明确点出改后的值，就算命中。
    """
    try:
        new_val = float(re.sub(r"[^\d.]", "", injection.after) or "nan")
    except ValueError:
        return False
    blob_parts: list[str] = []
    for r in reports:
        blob_parts.append(json.dumps(r, ensure_ascii=False))
    blob = " ".join(blob_parts)
    if not blob:
        return False
    nums = {round(float(x), 4) for x in re.findall(r"\d+\.?\d*", blob) if x}
    return any(abs(n - new_val) < 5e-3 for n in nums)


# ── 主流程 ──────────────────────────────────────────────────────
def evaluate(with_llm: bool) -> list[PaperResult]:
    files = sorted(p for p in PARSED.glob("*.json") if not p.name.startswith("_"))
    results: list[PaperResult] = []

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        blocks = data["blocks"]
        r = PaperResult(key=path.stem)

        # B 组：干净论文上的误报
        clean_findings = run_checks(blocks, extract_facts(blocks))
        r.clean_conflicts = sum(1 for f in clean_findings if f.verdict == "conflict")

        # 注入
        inj_blocks, injections = inject_all(blocks)
        r.injected = len(injections)
        if injections:
            inj_findings = run_checks(inj_blocks, extract_facts(inj_blocks))
            found_checks = {f.check for f in inj_findings if f.verdict == "conflict"}
            for inj in injections:
                if inj.expected_check in found_checks:
                    r.caught += 1
                else:
                    r.notes.append(f"未抓到 {inj.expected_check}: {inj.description}")

        # A 组：裸 LLM
        if with_llm and injections:
            reports, dt, tokens = llm_find_inconsistencies(inj_blocks)
            r.llm_seconds, r.llm_tokens = dt, tokens
            r.llm_injected = len(injections)
            for inj in injections:
                if _llm_matches_injection(reports, inj):
                    r.llm_caught += 1
            # LLM 在**干净**论文上报了多少（全是误报）
            clean_reports, _, _ = llm_find_inconsistencies(blocks)
            r.llm_clean_reports = len(clean_reports)

        results.append(r)
        print(
            f"  {path.stem:<10} 注入 {r.injected} 检出 {r.caught} | "
            f"干净误报 {r.clean_conflicts}"
            + (f" | LLM 检出 {r.llm_caught}/{r.llm_injected} "
               f"干净误报 {r.llm_clean_reports} ({r.llm_seconds:.0f}s)"
               if with_llm and r.injected else "")
        )

    return results


def summarize(results: list[PaperResult], with_llm: bool) -> str:
    inj = sum(r.injected for r in results)
    caught = sum(r.caught for r in results)
    clean_fp = sum(r.clean_conflicts for r in results)
    papers = len(results)

    lines = [
        "",
        "=" * 74,
        "  评测结果",
        "=" * 74,
        f"  语料：{papers} 篇论文（作者本人的一作/共一作论文）",
        f"  注入缺陷：{inj} 处（确定性注入，可复现）",
        "",
        "  ── B 组：本系统（确定性规则 C1–C6）──────────────",
        f"    检出率（recall）   {caught}/{inj} = "
        f"{(caught / inj * 100 if inj else 0):.0f}%",
        f"    误报数（干净论文上）{clean_fp}  ← 越低越好",
    ]
    if with_llm:
        l_inj = sum(r.llm_injected for r in results)
        l_caught = sum(r.llm_caught for r in results)
        l_fp = sum(r.llm_clean_reports for r in results)
        secs = sum(r.llm_seconds for r in results)
        toks = sum(r.llm_tokens for r in results)
        lines += [
            "",
            "  ── A 组：裸 LLM（全文塞 context 直接找）───────",
            f"    检出率（recall）   {l_caught}/{l_inj} = "
            f"{(l_caught / l_inj * 100 if l_inj else 0):.0f}%",
            f"    误报数（干净论文上）{l_fp}  ← 越低越好",
            f"    耗时 {secs:.0f}s · {toks} tokens",
        ]
    lines += [
        "",
        "  说明：",
        "    · 检出率 = 抓到注入缺陷数 / 注入总数",
        "    · 误报数 = 在**未注入**论文上报出的 conflict 数（真实论文不该有内部矛盾）",
        "    · 本系统为确定性规则，结果可复现；LLM 组 temperature=0，但仍可能有波动",
        "=" * 74,
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="数值审查评测")
    ap.add_argument("--llm-baseline", action="store_true", help="加跑裸 LLM 对照组")
    ap.add_argument("--json", help="把明细写到 JSON")
    args = ap.parse_args()

    if not PARSED.is_dir() or not list(PARSED.glob("*.json")):
        print(f"未找到解析产物：{PARSED}", file=sys.stderr)
        return 2

    if args.llm_baseline:
        # ⚠️ 护栏：LLM 不可用时**立即报错退出**，不要静默跑成 0%。
        #    实测踩过：.env 未加载 → build_chat() 返回 None → 对照组“检出 0/12”，
        #    表面上像是“LLM 完全找不到问题”的结论，实际根本没调 API。
        from rsrc.llm import build_chat

        chat = build_chat()
        if chat is None:
            print(
                "✗ 无法构建 LLM 客户端，对照实验无法进行。\n"
                "  请检查 .env 里的 LLM_PROVIDER_NAME / LLM_BASE_URL / LLM_MODEL\n"
                "  以及对应的 *_API_KEY（当前会取 SILICONFLOW_API_KEY / DEEPSEEK_API_KEY 等）。",
                file=sys.stderr,
            )
            return 3
        print(f"  LLM 对照组使用：{chat[1].provider}/{chat[1].model}")

    print("=== 逐篇 ===")
    results = evaluate(with_llm=args.llm_baseline)
    print(summarize(results, args.llm_baseline))

    if args.json:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = Path(args.json)
        out.write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"明细已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
