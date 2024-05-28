FROM python:3.8

WORKDIR /app

COPY . /app

RUN pip install -r requirements.txt

EXPOSE 5555

CMD ["python", "API.py"]

ENV HTTPS_PROXY http://webproxy.pl:9999

ENV NO_PROXY=localhost,127.0.0.1,10.255.3.150
