FROM python:3.11-alpine AS build
RUN mkdir -p /tmp/luadox
COPY requirements.txt pyproject.toml setup.cfg /tmp/luadox
COPY luadox /tmp/luadox/luadox
RUN pip install --user /tmp/luadox && \
    find /root/.local -type f -exec chmod a+r "{}" \; && \
    find /root/.local -type d -exec chmod a+rx "{}" \;

FROM python:3.11-alpine
RUN apk --update upgrade && rm -rf /var/cache/apk/*
COPY --from=build /root/.local /opt/luadox
ENV PYTHONPATH /opt/luadox/lib/python3.11/site-packages/
ENV PATH /opt/luadox/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
ENTRYPOINT ["/opt/luadox/bin/luadox"]
CMD ["--help"]
