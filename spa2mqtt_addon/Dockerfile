ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY spa2mqtt /app/spa2mqtt
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["/run.sh"]
