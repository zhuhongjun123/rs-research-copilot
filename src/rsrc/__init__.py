"""rs-research-copilot — 遥感科研辅助 Agent。"""

__version__ = "0.1.0"

# ⚠️ 在这里加载 .env，而不是只写在 config.py 里。
# 实测踩过：`scripts/run_eval.py` 只 import 了 audit 模块（不经过 config），
# 导致 .env 未加载 → `build_chat()` 返回 None → LLM 对照组**静默跑成 0%**。
# 放在包入口可以保证任何入口点都能拿到配置。
from rsrc.config import load_dotenv as _load_dotenv

_load_dotenv()
