FROM python:3.9-alpine
COPY build/luadox /usr/local/bin
CMD luadox