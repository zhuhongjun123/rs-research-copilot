"""FastAPI 服务：把审查能力暴露成接口。

## 为什么 API 层是必要的

「能演示」是这个项目的验收标准之一（JD 调研里「生产级/可演示」出现 15/35）。
CLI 只能自己跑，API 能让别人一条命令起服务、浏览器里点。

## 设计

- **无状态**：每次请求按需读取 `data/index/parsed/<key>.json`
- **失败可诊断**：依赖缺失（Zotero 未运行 / 没解析产物）返回 4xx + 可执行的修复提示，
  而不是 500 堆栈
- `POST /audit/inject` 是**演示端点**：现场注入已知缺陷，展示「能抓到」

启动：
    pixi run api
    # 然后 http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from rsrc import __version__
from rsrc.audit.checks import run_checks
from rsrc.audit.extract import extract_facts
from rsrc.audit.inject import inject_all
from rsrc.config import INDEX_DIR, PROJECT_ROOT
from rsrc.graph import run_audit

PARSED_DIR = INDEX_DIR / "parsed"

app = FastAPI(
    title="rs-research-copilot",
    version=__version__,
    description=(
        "遥感科研辅助 Agent —— 论文数值自洽审查。\n\n"
        "检查项为**确定性规则**（指标值域、指标间数学关系、同一对象的跨位置一致性、"
        "单位量纲），不依赖 LLM 自由判断；LLM 仅用于生成摘要段落。"
    ),
)


# ── 模型 ────────────────────────────────────────────────────────
class AuditRequest(BaseModel):
    key: str = Field(description="Zotero item key 或已解析产物的文件名（不含 .json）")
    use_llm: bool = Field(default=False, description="是否调用 LLM 生成摘要")
    inject: bool = Field(default=False, description="注入已知缺陷（演示用）")


class FactOut(BaseModel):
    metric: str
    value: float
    unit: str
    relation: str
    page: int | None
    block_type: str
    context: str


class FindingOut(BaseModel):
    check: str
    title: str
    verdict: str
    severity: str
    subject: str
    detail: str
    items: list[FactOut]


class AuditResponse(BaseModel):
    key: str
    counts: dict[str, int]
    facts: list[FactOut]
    findings: list[FindingOut]
    report: str
    injections: list[str] = []


def _load_parsed(key: str) -> dict:
    path = PARSED_DIR / f"{key}.json"
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"未找到解析产物 {path.name}。请先解析：\n"
                "  pixi run -e parser python scripts/parse_pdf.py <pdf> "
                f"data/index/parsed/{key}.json"
            ),
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _fact_out(f) -> FactOut:
    return FactOut(
        metric=f.metric, value=f.value, unit=f.unit, relation=f.relation,
        page=f.page, block_type=f.block_type, context=f.context,
    )


# ── 端点 ────────────────────────────────────────────────────────
@app.get("/", summary="项目信息")
def root() -> dict:
    return {
        "name": "rs-research-copilot",
        "version": __version__,
        "summary": "遥感科研辅助 Agent —— 论文数值自洽审查",
        "docs": "/docs",
    }


@app.get("/health", summary="健康检查与依赖状态")
def health() -> dict:
    """报告各依赖是否可用 —— 便于定位「为什么跑不起来」。"""
    from rsrc.llm import build_chat, build_embedder

    zotero_ok, zotero_msg = False, ""
    try:
        from rsrc.zotero import ZoteroLibrary

        lib = ZoteroLibrary()
        info = lib.probe()
        zotero_ok = True
        zotero_msg = f"API v{info.get('api_version')} / Schema v{info.get('schema_version')}"
    except Exception as exc:  # noqa: BLE001
        zotero_msg = str(exc)[:160]

    chat = build_chat()
    try:
        embed = build_embedder()
        embed_msg = f"{embed.spec.provider}/{embed.spec.model} ({embed.spec.dim} 维)"
    except Exception as exc:  # noqa: BLE001
        embed_msg = f"不可用：{str(exc)[:100]}"

    return {
        "ok": True,
        "zotero": {"available": zotero_ok, "detail": zotero_msg},
        "llm": {"available": chat is not None,
                "detail": f"{chat[1].provider}/{chat[1].model}" if chat else "未配置（审查仍可用）"},
        "embedding": {"detail": embed_msg},
        "parsed_papers": len(list(PARSED_DIR.glob("*.json"))) if PARSED_DIR.is_dir() else 0,
    }


@app.get("/papers", summary="已解析的论文清单")
def papers() -> list[dict]:
    if not PARSED_DIR.is_dir():
        return []
    out = []
    for p in sorted(PARSED_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"key": p.stem, "page_count": data.get("page_count"),
                    "blocks": len(data.get("blocks", []))})
    return out


@app.get("/papers/{key}/facts", summary="某篇论文抽取到的数值型事实",
         response_model=list[FactOut])
def facts(key: str) -> list[FactOut]:
    data = _load_parsed(key)
    return [_fact_out(f) for f in extract_facts(data["blocks"])]


@app.post("/audit", summary="数值自洽审查", response_model=AuditResponse)
def audit(req: AuditRequest) -> AuditResponse:
    data = _load_parsed(req.key)
    injections: list[str] = []

    if req.inject:
        blocks, injs = inject_all(data["blocks"])
        data = {**data, "blocks": blocks}
        injections = [f"[{i.expected_check}] p{i.page} {i.description}" for i in injs]
        tmp = INDEX_DIR / f"_api_injected_{req.key}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        src = tmp
    else:
        src = PARSED_DIR / f"{req.key}.json"

    if req.inject or req.use_llm:
        state = run_audit(src, item_key=req.key, use_llm=req.use_llm)
        facts_list = state.get("facts", [])
        findings = state.get("findings", [])
        report = state.get("report", "")
    else:
        # 纯确定性路径：不经过 LLM
        facts_list = extract_facts(data["blocks"])
        findings = run_checks(data["blocks"], facts_list)
        from rsrc.report import AuditResult, render_report

        report = render_report(
            AuditResult(source=src.name, item_key=req.key,
                        findings=findings, facts=facts_list)
        )

    counts = {"high": 0, "medium": 0, "info": 0}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    return AuditResponse(
        key=req.key,
        counts=counts,
        facts=[_fact_out(f) for f in facts_list],
        findings=[
            FindingOut(check=f.check, title=f.title, verdict=f.verdict,
                       severity=f.severity, subject=f.subject, detail=f.detail,
                       items=[_fact_out(i) for i in f.items])
            for f in findings
        ],
        report=report,
        injections=injections,
    )


def main() -> None:
    """开发用启动入口（生产请用 uvicorn 直接指定）。"""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
