FROM python:3.5-alpine

WORKDIR /root
COPY requirements.txt /root
RUN pip install -r requirements.txt

COPY . /root/ecs_deploy
WORKDIR /root/ecs_deploy
RUN pip install .
