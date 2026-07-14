from __future__ import annotations

from pathlib import Path

from flask import request

import runner
from panel_common import esc, page


def register(app) -> None:
    @app.get("/logs")
    def logs() -> str:
        rows = []
        for path in sorted(runner.LOG_DIR.glob("*.log"), reverse=True)[:50]:
            rows.append(
                f"<tr><td>{esc(path.name)}</td><td>{path.stat().st_size}</td>"
                f"<td><a class='button secondary' href='/log?path={esc(str(path))}'>查看</a></td></tr>"
            )
        body = f"<section class='panel'><table><tr><th>文件</th><th>大小（bytes）</th><th>操作</th></tr>{''.join(rows)}</table></section>"
        return page("运行日志", body, "logs")

    @app.get("/log")
    def log_detail() -> str:
        path = Path(request.args.get("path", ""))
        if not path.exists() or runner.LOG_DIR not in path.resolve().parents:
            return page("运行日志", "<section class='panel'><p>日志不存在。</p></section>", "logs")
        content = path.read_text(encoding="utf-8", errors="replace")[-20000:]
        return page(path.name, f"<section class='panel'><pre>{esc(content)}</pre></section>", "logs")
