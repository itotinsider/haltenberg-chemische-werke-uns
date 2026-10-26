FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Standard library only — the plant's Modbus server is hand-written (see modbus_server.py).
COPY common/iomap.py common/sites.py common/
COPY plant/model.py plant/modbus_server.py plant/main.py ./
COPY plant/web ./web

EXPOSE 5020 8111
USER nobody
CMD ["python", "main.py"]
