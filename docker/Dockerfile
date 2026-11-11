FROM python:3.13-slim

COPY docker/bin/xray docker/bin/sing-box /usr/local/bin/
RUN chmod 755 /usr/local/bin/xray /usr/local/bin/sing-box

WORKDIR /src
CMD ["python3", "tests/docker_protocols.py"]
