"""Запуск веб-версии в Hugging Face Space (SDK Gradio): свой сервер на порту 7860."""
import os

try:
    # Бесплатные Gradio-Space работают на железе ZeroGPU. Hugging Face ждёт
    # от приложения хотя бы одну функцию с @spaces.GPU и отчёт о запуске,
    # который пакет spaces обычно отправляет при старте Gradio. Видеокарта
    # программе не нужна: функция-пустышка нигде не вызывается, а отчёт
    # отправляем сами, потому что страница обслуживается своим сервером.
    import spaces

    @spaces.GPU
    def _gpu_not_used():
        return None

    from spaces.config import Config as _SpacesConfig
    if _SpacesConfig.zero_gpu:
        from spaces.zero import client as _zero_client
        _zero_client.startup_report()
except ImportError:
    pass

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
