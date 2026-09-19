"""配置：路径发现与运行参数。

⚠️ **Zotero 数据目录不能硬编码。**
官方默认是 `~/Zotero`，但用户可以自定义（本项目作者即为 `F:\\ZoteroPaper`）。
本模块按「环境变量 > prefs.js 的 extensions.zotero.dataDir > 默认路径」的顺序发现。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """极简 `.env` 加载器（避免为十几行引入 python-dotenv）。

    规则：**已存在的环境变量不覆盖**（真环境变量优先于文件）。
    本函数幂等，可重复调用。
    """
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

# ── Zotero ──────────────────────────────────────────────────────
_DEFAULT_DATA_DIR_NAME = "Zotero"
_PREFS_RE = re.compile(r'user_pref\("extensions\.zotero\.dataDir",\s*"(.+?)"\)')


def _profile_roots() -> list[Path]:
    """Zotero 的 profile 可能落在 Roaming 或 Local 下。"""
    roots = []
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Zotero" / "Zotero" / "Profiles")
    return [r for r in roots if r.is_dir()]


def find_prefs_files() -> list[Path]:
    """列出所有 Zotero prefs.js。"""
    found: list[Path] = []
    for root in _profile_roots():
        found.extend(sorted(root.glob("*/prefs.js")))
    return found


def zotero_data_dir() -> Path:
    """发现 Zotero 数据目录。找不到时回退到官方默认路径（可能不存在）。"""
    env = os.environ.get("ZOTERO_DATA_DIR")
    if env:
        return Path(env)

    for prefs in find_prefs_files():
        try:
            text = prefs.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if 'extensions.zotero.useDataDir", true' not in text:
            continue
        match = _PREFS_RE.search(text)
        if match:
            return Path(match.group(1))

    return Path.home() / _DEFAULT_DATA_DIR_NAME


def local_api_base() -> str:
    """Zotero 本地 API 地址（需在 Zotero 设置中允许其它应用通信）。"""
    root = os.environ.get("ZOTERO_LOCAL_API", "http://localhost:23119/api")
    return root.rstrip("/") + "/users/0"


# ── 项目路径 ────────────────────────────────────────────────────
DATA_DIR = PROJECT_ROOT / "data"
INDEX_DIR = DATA_DIR / "index"
BENCH_DIR = DATA_DIR / "bench"

# ── 检索 ────────────────────────────────────────────────────────
TOP_K = int(os.environ.get("TOP_K", "10"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")

# ── 附件过滤（见 PLAN §4.2）────────────────────────────────────
# ⚠️ 实测：**HTTP API 返回的 linkMode 是字符串**（"imported_url"），
# 而 SQLite 里存的是整数（0/1/2）。两套都要认，否则附件会被全部过滤掉。
# 源码常量：0=导入文件 1=导入URL 2=链接文件 3=URL 4=内嵌图 —— 只要文件型的三种。
LINK_MODE_FILE_NAMES = ("imported_file", "imported_url", "linked_file")
LINK_MODE_FILE_INTS = (0, 1, 2)
LINK_MODE_FILE = LINK_MODE_FILE_NAMES + LINK_MODE_FILE_INTS
PDF_CONTENT_TYPE = "application/pdf"
