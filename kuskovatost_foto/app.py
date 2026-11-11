"""Запуск веб-версии в Hugging Face Space (SDK Gradio): свой сервер на порту 7860."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
