"""python -m web — веб-версия на http://127.0.0.1:8000 (порт — переменная PORT, адрес — HOST)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.server:app", host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")), log_level="info")
