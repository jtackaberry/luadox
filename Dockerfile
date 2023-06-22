FROM python:3.11-alpine
COPY build/luadox /usr/local/bin
CMD luadox
