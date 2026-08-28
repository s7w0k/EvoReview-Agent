FROM python:3.11-slim

WORKDIR /srv/evoagent

COPY evoagent /srv/evoagent/evoagent
COPY services/security_agent /srv/evoagent/services/security_agent
COPY requirements.txt pyproject.toml /srv/evoagent/

RUN pip install --no-cache-dir fastapi uvicorn && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8001
CMD ["uvicorn", "services.security_agent.app:app", "--host", "0.0.0.0", "--port", "8001"]