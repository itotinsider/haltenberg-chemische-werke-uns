FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY dcs/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/iomap.py common/sites.py common/
COPY dcs/config.py dcs/control.py dcs/historian.py dcs/opcua_server.py dcs/native.py dcs/main.py ./
COPY dcs/web ./web

EXPOSE 4840 8110
USER nobody
CMD ["python", "main.py"]
