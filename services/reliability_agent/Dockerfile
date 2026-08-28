FROM python:3.11-slim

WORKDIR /srv/evoagent

COPY evoagent /srv/evoagent/evoagent
COPY services/reliability_agent /srv/evoagent/services/reliability_agent
COPY requirements.txt pyproject.toml /srv/evoagent/

RUN pip install --no-cache-dir fastapi uvicorn && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8002
CMD ["uvicorn", "services.reliability_agent.app:app", "--host", "0.0.0.0", "--port", "8002"]