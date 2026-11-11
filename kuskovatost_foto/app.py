"""Запуск веб-версии в Hugging Face Space (SDK Gradio): свой сервер на порту 7860."""
import os

try:
    # На железе ZeroGPU Hugging Face требует хотя бы одну функцию с @spaces.GPU.
    # Видеокарта программе не нужна, функция-пустышка нигде не вызывается.
    import spaces

    @spaces.GPU
    def _gpu_not_used():
        return None
except ImportError:
    pass

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
