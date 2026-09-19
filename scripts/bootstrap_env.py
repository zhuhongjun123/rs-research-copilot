#!/usr/bin/env python
"""在「干净 PATH」下启动 Python —— 绕开 Windows 上的 GDAL DLL 冲突。

## 问题

如果 PATH 中存在**其它 GDAL/PROJ 发行版**（本站典型来源：Anaconda 的
``Library\\bin``、ArcGIS 自带的 Python），Windows 会解析到错误版本的
``gdal.dll``，于是 rasterio / fiona / pyogrio 在导入时抛出：

    ImportError: DLL load failed while importing _warp: 找不到指定的程序

（ERROR_PROC_NOT_FOUND —— 找到了同名 DLL，但缺少所需的导出符号。）

**注意**：``os.add_dll_directory()`` 救不了这个 —— 错误的 DLL 在更早的阶段
就已被解析，事后补目录无效。实测只有从进程启动起就清干净 PATH 才有效。

## 做法

把 PATH 重建为「当前环境 + 系统目录」，并设好 GDAL_DATA / PROJ_DATA，
然后以该环境启动目标解释器。对本机以外同样成立：不依赖任何硬编码路径。

详见 ``RUN_LOG.md`` →「Windows GDAL DLL 冲突」。

## 用法

    pixi run py -c "import rasterio; print(rasterio.__version__)"
    pixi run py scripts/build_index.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PREFIX = Path(sys.prefix)


def clean_env() -> dict[str, str]:
    """返回一个只含当前环境与系统目录的 PATH 的环境变量副本。"""
    systemroot = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    parts = [
        PREFIX / "Library" / "bin",
        PREFIX,
        PREFIX / "Scripts",
        PREFIX / "bin",
        systemroot / "System32",
        systemroot,
    ]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(str(p) for p in parts)

    # GDAL / PROJ 数据文件；缺了会报 header.dxf / gdalvrt.xsd 缺失
    for var, sub in (("GDAL_DATA", "share/gdal"), ("PROJ_DATA", "share/proj")):
        data_dir = PREFIX / "Library" / sub
        if data_dir.is_dir():
            env[var] = str(data_dir)
    return env


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    return subprocess.call([sys.executable, *args], env=clean_env())


if __name__ == "__main__":
    raise SystemExit(main())
