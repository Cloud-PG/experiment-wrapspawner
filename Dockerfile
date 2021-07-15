FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && apt upgrade -y
RUN apt install -y git python3 python3-pip npm nodejs

RUN npm install -g configurable-http-proxy
RUN python3 -m pip install jupyterlab jupyterhub notebook
RUN python3 -m pip install dockerspawner
RUN python3 -m pip install nest_asyncio
RUN python3 -m pip install rich

RUN /bin/bash -c "mkdir -p /usr/local/share/jupyterhub"

WORKDIR /usr/local/share/jupyterhub
RUN git clone https://github.com/jupyterhub/wrapspawner
WORKDIR /usr/local/share/jupyterhub/wrapspawner

COPY wrapspawner/wrapspawner/customwrap.py wrapspawner/
COPY wrapspawner/wrapspawner/__init__.py wrapspawner/
RUN python3 -m pip install -e .

WORKDIR /usr/local/share/jupyterhub

# basic user to test
RUN groupadd jupytershare
RUN useradd -m -G jupytershare -d /home/user0 user0

COPY jupyterhub_config.py /usr/local/share/jupyterhub

CMD ["jupyterhub", "-f", "/usr/local/share/jupyterhub/jupyterhub_config.py"]
