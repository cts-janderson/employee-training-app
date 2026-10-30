# Application image for every Django role: migrate, web, worker and cleanup (docs/plans/30-production-deployment.md).
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY pyproject.toml ./
COPY config config
COPY training training
# Install the dependencies through the package, then drop the package itself: the app runs from /app, where
# settings find the templates beside it.
RUN pip install . && pip uninstall --yes employee-training-app

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY manage.py ./
COPY config config
COPY training training
COPY templates templates
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
# Placeholder settings that aren't secret; collectstatic opens no database connection and sends no mail.
RUN DJANGO_DEBUG=0 DEV_AUTH_STUB=0 DJANGO_SECRET_KEY=build DJANGO_ALLOWED_HOSTS=build \
    APP_BASE_URL=https://build.invalid DATABASE_URL=postgresql://build/unused \
    GRAPH_TENANT_ID=build GRAPH_CLIENT_ID=build GRAPH_CLIENT_SECRET=build \
    python manage.py collectstatic --noinput --verbosity 0
USER app
EXPOSE 8000
ENTRYPOINT ["entrypoint"]
CMD ["web"]
