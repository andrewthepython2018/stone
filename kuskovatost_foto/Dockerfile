# Веб-версия «Кусковатость породы по фото».
# Сборка и запуск:  docker build -t kuskovatost . && docker run -p 7860:7860 kuskovatost
# Пароль на страницу (необязательно):  docker run -e ACCESS_CODE=секрет -p 7860:7860 kuskovatost
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/mpl HOST=0.0.0.0 PORT=7860
RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY --chown=user fragmentation ./fragmentation
COPY --chown=user foto/__init__.py foto/analyze.py ./foto/
COPY --chown=user web ./web
COPY --chown=user primery ./primery
USER user
EXPOSE 7860
CMD ["python", "-m", "web"]
