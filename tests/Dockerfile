# Test runner image: the DCS's Python and libraries (asyncua, pymodbus) plus pytest.
# The repo is bind-mounted at /w, so tests always run against the working tree (no rebuild
# needed after a code change). Run with:  docker compose run --rm tests
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY dcs/requirements.txt /tmp/dcs-requirements.txt
COPY tests/requirements.txt /tmp/test-requirements.txt
RUN pip install --no-cache-dir -r /tmp/dcs-requirements.txt -r /tmp/test-requirements.txt

WORKDIR /w
CMD ["python", "-m", "pytest"]
